"""Log redaction -- the last line of defence before data reaches disk.

The value types in ``olbostax_schema.sensitive`` prevent the *expected* leaks:
code that formats a taxpayer object gets a masked SSN.  This module handles the
unexpected ones -- a raw dict from a request body, a database driver's error
message quoting the offending row, a third-party library logging its inputs, a
stack trace with local variables.

It is a filter installed on the logging system itself, so it applies to every
log record from every library regardless of who wrote the call. Defence in
depth: the value types make leaks unlikely, this makes them survivable.

The patterns are deliberately broad. A false positive redacts a number that
did not need redacting, which costs a debugging session some detail. A false
negative writes a taxpayer's SSN into a log aggregator, where it is
effectively permanent and turns a log export into a breach notification.
"""

from __future__ import annotations

import logging
import re
from typing import Any

__all__ = [
    "REDACTED",
    "redact_text",
    "redact_mapping",
    "RedactingFilter",
    "RedactingFormatter",
    "install",
]

REDACTED = "[REDACTED]"

# Order matters: more specific patterns run first so that a labelled SSN is
# redacted as an SSN rather than being partially matched by a looser rule.
_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "ssn_labelled",
        re.compile(
            r"""(?ix)
            \b (?: ssn | social[\s_-]?security(?:[\s_-]?number)? | tin | itin )
            \b \s* [:=]? \s* ["']? ( \d{3}[-\s]?\d{2}[-\s]?\d{4} ) ["']?
            """
        ),
        r"\1",
    ),
    (
        "ssn_formatted",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        None,
    ),
    (
        "bank_account",
        re.compile(
            r"""(?ix)
            \b (?: account[\s_-]?(?:number|no)? | routing[\s_-]?(?:number|no)?
                 | aba | iban )
            \b \s* [:=]? \s* ["']? ( \d{5,17} ) ["']?
            """
        ),
        r"\1",
    ),
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        None,
    ),
    (
        "secret_assignment",
        re.compile(
            r"""(?ix)
            \b (?: password | passwd | secret | token | api[\s_-]?key
                 | authorization | bearer | session[\s_-]?id | cookie
                 | private[\s_-]?key | client[\s_-]?secret )
            \b \s* [:=]\s* ["']? ( [^\s,;"'}\]]+ ) ["']?
            """
        ),
        r"\1",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
        None,
    ),
    (
        "pem_block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        None,
    ),
)

# Keys whose values are replaced wholesale in structured logs, regardless of
# whether the value itself matches a pattern.
_SENSITIVE_KEYS = frozenset(
    {
        "ssn", "social_security_number", "tin", "itin", "taxpayer_id",
        "spouse_ssn", "dependent_ssn",
        "account_number", "routing_number", "bank_account", "bank_account_number",
        "card_number", "cvv", "cvc", "pan",
        "password", "current_password", "new_password", "password_confirmation",
        "secret", "client_secret", "api_key", "apikey", "token", "access_token",
        "refresh_token", "id_token", "session_id", "session_token", "csrf_token",
        "authorization", "cookie", "set_cookie", "private_key", "mfa_secret",
        "totp_secret", "recovery_codes",
        "date_of_birth", "dob",
    }
)


def redact_text(text: str) -> str:
    """Redact every recognised sensitive pattern in a string."""
    for _, pattern, group in _PATTERNS:
        if group is None:
            text = pattern.sub(REDACTED, text)
        else:
            # Replace only the captured value, keeping the label so the log
            # still says *what* was redacted -- "ssn=[REDACTED]" is far more
            # useful when debugging than a bare "[REDACTED]".
            text = pattern.sub(
                lambda m: m.group(0).replace(m.group(1), REDACTED), text
            )
    return text


def redact_mapping(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a structure destined for a structured log.

    Depth is bounded: a deeply nested or self-referential structure should not
    turn a log call into a stack overflow.
    """
    if _depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS
                else redact_mapping(item, _depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return type(value)(redact_mapping(item, _depth + 1) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactingFilter(logging.Filter):
    """A logging filter that redacts messages, arguments and structured extras.

    Installed on handlers rather than loggers so that it catches records from
    third-party libraries too -- those are exactly the ones nobody remembers to
    audit.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        elif isinstance(record.msg, dict):
            record.msg = redact_mapping(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = redact_mapping(record.args)
            else:
                record.args = tuple(redact_mapping(arg) for arg in record.args)

        for key in list(vars(record)):
            if key.lower() in _SENSITIVE_KEYS:
                setattr(record, key, REDACTED)

        # An exception message can carry a row of data the driver was writing.
        if record.exc_info and record.exc_info[1] is not None:
            exception = record.exc_info[1]
            if exception.args and isinstance(exception.args[0], str):
                exception.args = (redact_text(exception.args[0]), *exception.args[1:])

        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the fully rendered log line, tracebacks included.

    The filter above cannot cover everything. A traceback is assembled by the
    *formatter*, after filters have run, and it inlines the source line of
    every frame plus the repr of the exception. Data can reach the output
    through either -- a literal in a source line, or a driver exception that
    quotes the row it was writing.

    So redaction happens twice, at two different layers. The filter keeps
    structured fields clean for handlers that consume the record directly
    (JSON log shippers read ``record.__dict__``, never the formatted string);
    this catches whatever is left in the text that actually gets written.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def install(
    root: logging.Logger | None = None, *, formatter: logging.Formatter | None = None
) -> None:
    """Attach redaction to every handler on the root logger.

    Installs both layers: the filter, so structured consumers see redacted
    fields, and a :class:`RedactingFormatter`, so the rendered text -- including
    tracebacks -- is redacted too.

    Call this before any other logging configuration and again after adding
    handlers. A handler added later carries neither, and would log in the clear.
    """
    logger = root or logging.getLogger()
    redactor = RedactingFilter()
    for handler in logger.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(redactor)
        if not isinstance(handler.formatter, RedactingFormatter):
            existing = handler.formatter
            handler.setFormatter(
                formatter
                or RedactingFormatter(
                    fmt=getattr(existing, "_fmt", None),
                    datefmt=getattr(existing, "datefmt", None),
                )
            )
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(redactor)
