"""Value types for information that must never leak into a log line.

The defence here is *structural* rather than procedural.  Telling engineers
"remember not to log the SSN" fails the first time somebody writes
``logger.info("return %s", payload)``.  Instead the sensitive values carry
their own ``__repr__`` and ``__str__`` that render masked, so the accidental
path is the safe one and reading the real value requires calling
:meth:`reveal` -- a method name that shows up in code review and in grep.

These types deliberately do *not* encrypt.  Encryption at rest is a separate
concern handled in ``packages/security``; this is about accidental disclosure
through logs, tracebacks, error messages and analytics payloads.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

__all__ = ["SensitiveStr", "SSN", "BankAccountNumber", "RoutingNumber", "mask_tail"]

_DIGITS = re.compile(r"\D")


def mask_tail(value: str, visible: int = 4, mask_char: str = "*") -> str:
    """Show only the last ``visible`` characters, e.g. ``*******1234``."""
    if len(value) <= visible:
        return mask_char * len(value)
    return mask_char * (len(value) - visible) + value[-visible:]


class SensitiveStr(str):
    """A string that refuses to render itself in full.

    Subclassing :class:`str` keeps it usable anywhere a string is expected
    while overriding every rendering path.  ``reveal()`` is the single,
    greppable escape hatch.

    Normalisation happens in ``__new__`` rather than only in the pydantic
    validator, so ``SSN("123-45-6789")`` constructed directly in a test or a
    migration script holds exactly the same nine digits as one that arrived
    through the API.  A value type whose invariants depend on which door it
    came through is not a value type.
    """

    __slots__ = ()

    def __new__(cls, value: object = "") -> SensitiveStr:
        return super().__new__(cls, cls._normalize(str(value)))

    @classmethod
    def _normalize(cls, value: str) -> str:
        """Canonical storage form. Subclasses strip formatting and validate."""
        return value

    def masked(self) -> str:
        return mask_tail(str.__str__(self))

    def reveal(self) -> str:
        """Return the underlying value in the clear.

        Every call site is a decision point.  Reveal only when handing the
        value to the encryption layer, the e-file serializer, or a validation
        routine that needs the real digits.
        """
        return str.__str__(self)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}({self.masked()!r})"

    def __str__(self) -> str:
        return self.masked()

    def __format__(self, spec: str) -> str:
        return format(self.masked(), spec)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[Any], handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls._validate,
            core_schema.str_schema(),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda v: v.masked() if isinstance(v, SensitiveStr) else mask_tail(str(v)),
                return_schema=core_schema.str_schema(),
                when_used="json",
            ),
        )

    @classmethod
    def _validate(cls, value: str) -> SensitiveStr:
        return cls(value)


class SSN(SensitiveStr):
    """A US Social Security Number or ITIN.

    Stored normalised to nine digits.  Renders as ``***-**-1234``.  Structural
    validity (not existence) is checked by ``packages.validation``; this type
    only guarantees the shape so downstream code can rely on nine digits.
    """

    __slots__ = ()

    @classmethod
    def _normalize(cls, value: str) -> str:
        digits = _DIGITS.sub("", value)
        if len(digits) != 9:
            raise ValueError("SSN must contain exactly 9 digits")
        return digits

    def masked(self) -> str:
        raw = str.__str__(self)
        return f"***-**-{raw[-4:]}"

    def formatted(self) -> str:
        """``123-45-6789`` -- only for rendering a return the taxpayer downloads."""
        raw = str.__str__(self)
        return f"{raw[0:3]}-{raw[3:5]}-{raw[5:9]}"


class BankAccountNumber(SensitiveStr):
    """Direct-deposit account number.  Renders as ``********1234``."""

    __slots__ = ()

    @classmethod
    def _normalize(cls, value: str) -> str:
        digits = _DIGITS.sub("", value)
        if not 1 <= len(digits) <= 17:
            raise ValueError("bank account number must be 1-17 digits")
        return digits

    def masked(self) -> str:
        return mask_tail(str.__str__(self), visible=4)


class RoutingNumber(SensitiveStr):
    """ABA routing transit number -- nine digits with a check digit.

    The checksum is verified here because it is a pure structural property and
    catching a transposed digit before a refund is misdirected is worth the
    few lines.  Weights are the standard ABA 3-7-1 pattern.
    """

    __slots__ = ()

    @classmethod
    def _normalize(cls, value: str) -> str:
        digits = _DIGITS.sub("", value)
        if len(digits) != 9:
            raise ValueError("routing number must contain exactly 9 digits")
        weights = (3, 7, 1, 3, 7, 1, 3, 7, 1)
        checksum = sum(int(d) * w for d, w in zip(digits, weights, strict=True))
        if checksum % 10 != 0:
            raise ValueError("routing number failed ABA checksum")
        return digits

    def masked(self) -> str:
        return mask_tail(str.__str__(self), visible=4)
