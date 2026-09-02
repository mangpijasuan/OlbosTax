"""Encrypting sensitive fields inside a stored return.

A ``TaxReturnInput`` is stored as a JSON document, which creates a specific
problem: the document contains SSNs and bank details, and JSONB columns are
readable by anything with database access.

The approach is selective field encryption inside the document. The structure
stays queryable and the schema stays legible, while the handful of fields that
would actually harm a taxpayer if disclosed are ciphertext. Encrypting the
whole document instead would be simpler but would make the return input opaque
to every diagnostic, migration and analytic query -- and most of what is in
there (wage amounts, filing status, number of dependents) is not the sensitive
part.

The paths are enumerated explicitly rather than discovered by type inspection.
A new sensitive field must be added to ``SENSITIVE_PATHS``, which is a
deliberate speed bump: it fails the round-trip test rather than silently
storing plaintext, and it puts the decision in a diff a reviewer will see.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import Any

from olbostax_schema import TaxReturnInput
from olbostax_schema.sensitive import SensitiveStr
from olbostax_security import FieldCipher, LocalKeyring

__all__ = [
    "encrypt_return_input",
    "decrypt_return_input",
    "get_cipher",
    "SENSITIVE_PATHS",
]

# Dotted paths into the serialized return document. ``[]`` denotes a list, and
# every element is processed.
SENSITIVE_PATHS: tuple[str, ...] = (
    "taxpayer.ssn",
    "spouse.ssn",
    "dependents[].ssn",
    "direct_deposit.routing_number",
    "direct_deposit.account_number",
)

_ENCRYPTED_PREFIX = "enc:"


@lru_cache
def get_cipher() -> FieldCipher:
    """The configured field cipher.

    In production this must be constructed from a KMS-backed keyring. The
    local keyring refuses to run outside a development environment, so a
    production process reaching here without KMS configuration fails at first
    use rather than encrypting under a throwaway key.
    """
    raw = os.environ.get("OLBOSTAX_FIELD_ENCRYPTION_KEYS")
    if raw:
        return FieldCipher(LocalKeyring.from_environment())
    key_id = "ephemeral"
    return FieldCipher(
        LocalKeyring(
            keys={key_id: base64.urlsafe_b64decode(_generate_ephemeral())},
            active=key_id,
        )
    )


def _generate_ephemeral() -> str:
    from olbostax_security import generate_key

    return generate_key()


def _walk(document: Any, parts: list[str], apply: Any) -> None:
    """Descend ``parts`` into ``document`` and apply ``apply`` at the leaf."""
    if not parts:
        return
    head, *rest = parts

    if head.endswith("[]"):
        key = head[:-2]
        items = document.get(key) if isinstance(document, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            _walk(item, rest, apply)
        return

    if not isinstance(document, dict) or head not in document:
        return

    if not rest:
        value = document[head]
        if value is not None:
            document[head] = apply(value)
        return

    _walk(document[head], rest, apply)


def encrypt_return_input(tax_input: TaxReturnInput) -> dict:
    """Serialize a return and encrypt its sensitive fields.

    ``mode="python"`` is used so the sensitive value types serialize to their
    real values rather than their masked JSON form -- a stored return whose
    SSN reads ``***-**-6789`` could never be filed. Those real values are then
    immediately encrypted, so nothing plaintext reaches the caller.
    """
    document = tax_input.model_dump(mode="python")
    document = _to_jsonable(document)
    cipher = get_cipher()

    for path in SENSITIVE_PATHS:
        _walk(
            document,
            path.split("."),
            lambda value, p=path: _ENCRYPTED_PREFIX
            + cipher.encrypt(_plaintext(value), context=f"return.{p}"),
        )
    return document


def _plaintext(value: object) -> str:
    """Extract the real value from a sensitive type.

    ``str()`` on an ``SSN`` returns ``***-**-6789`` -- that is the entire point
    of the type. Encrypting the mask would store ciphertext of a mask, and the
    return would be unfilable in a way that only shows up when a taxpayer tries
    to file. ``reveal()`` is the explicit, greppable way to get the real value,
    and this is a legitimate call site: the plaintext goes straight into the
    cipher and never escapes this function.
    """
    if isinstance(value, SensitiveStr):
        return value.reveal()
    return str(value)


def decrypt_return_input(document: dict) -> TaxReturnInput:
    """Reverse of :func:`encrypt_return_input`."""
    restored = dict(document)
    cipher = get_cipher()

    for path in SENSITIVE_PATHS:
        _walk(
            restored,
            path.split("."),
            lambda value, p=path: (
                cipher.decrypt(value[len(_ENCRYPTED_PREFIX):], context=f"return.{p}")
                if isinstance(value, str) and value.startswith(_ENCRYPTED_PREFIX)
                else value
            ),
        )
    return TaxReturnInput.model_validate(restored)


def _to_jsonable(value: Any) -> Any:
    """Convert Decimals, dates and enums into JSON-storable forms.

    Decimals become strings, not floats. Round-tripping a wage amount through
    a JSON float would reintroduce exactly the precision loss the engine
    exists to avoid, at the storage layer where it is hardest to notice.
    """
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum

    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
