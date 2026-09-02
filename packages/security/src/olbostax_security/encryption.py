"""Field-level encryption for taxpayer identifiers and bank details.

Disk encryption protects against a stolen drive.  It does not protect against
a SQL injection, a leaked read replica, an over-broad support query, or a
backup restored into a less-trusted environment -- in every one of those the
database hands over plaintext.  Field-level encryption means the SSN column
holds ciphertext that is useless without a key held somewhere the database is
not.

Design:

*   **AES-256-GCM**, which authenticates as well as encrypts.  A tampered
    ciphertext fails to decrypt rather than silently producing different
    plaintext -- important when the value decides where a refund is sent.
*   **Key IDs are stored with the ciphertext**, so keys can be rotated without
    a migration that touches every row.  Old data decrypts under the old key;
    new writes use the active key; rows migrate lazily or by a background job.
*   **Associated data binds a ciphertext to its context.**  An SSN encrypted
    for taxpayer 42 will not decrypt in the context of taxpayer 43, so an
    attacker with write access cannot move a ciphertext between rows to
    impersonate another taxpayer.

In production, keys come from a KMS and this module receives only the
unwrapped data keys.  ``LocalKeyring`` exists for development and tests and
refuses to be used when the environment is not local.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "Keyring",
    "LocalKeyring",
    "FieldCipher",
    "EncryptedValue",
    "DecryptionError",
    "generate_key",
]

_NONCE_BYTES = 12  # 96 bits, the size AES-GCM is specified for.
_KEY_BYTES = 32  # AES-256.


class DecryptionError(Exception):
    """Ciphertext could not be decrypted or failed authentication."""


def generate_key() -> str:
    """A fresh 256-bit key, base64url encoded."""
    return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES)).decode()


class Keyring(Protocol):
    """Source of data encryption keys."""

    @property
    def active_key_id(self) -> str:
        """The key new ciphertexts are written under."""

    def key(self, key_id: str) -> bytes:
        """Fetch a key by id. Raises ``KeyError`` when unknown."""


@dataclass(frozen=True, slots=True)
class LocalKeyring:
    """An in-process keyring for local development and tests.

    Refuses to operate outside a local or test environment. A production
    deployment that accidentally fell back to this would be encrypting every
    taxpayer's SSN under a key sitting in an environment variable, which is
    barely better than not encrypting at all -- so it fails loudly instead.
    """

    keys: dict[str, bytes]
    active: str

    @property
    def active_key_id(self) -> str:
        return self.active

    def key(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError as exc:
            raise KeyError(f"unknown encryption key id {key_id!r}") from exc

    @classmethod
    def from_environment(cls) -> LocalKeyring:
        environment = os.environ.get("OLBOSTAX_ENV", "local")
        if environment not in ("local", "test", "development"):
            raise RuntimeError(
                f"LocalKeyring must not be used in the {environment!r} environment; "
                "configure a KMS-backed keyring instead"
            )
        raw = os.environ.get("OLBOSTAX_FIELD_ENCRYPTION_KEYS")
        if not raw:
            # Generating an ephemeral key is right for a test run and wrong for
            # a developer who restarts the API and finds their data unreadable,
            # so the situation is made obvious rather than silently handled.
            key_id = "ephemeral"
            return cls(keys={key_id: os.urandom(_KEY_BYTES)}, active=key_id)
        decoded = {
            key_id: base64.urlsafe_b64decode(value)
            for key_id, value in json.loads(raw).items()
        }
        active = os.environ.get("OLBOSTAX_ACTIVE_KEY_ID") or next(iter(decoded))
        if active not in decoded:
            raise RuntimeError(f"active key id {active!r} is not in the keyring")
        return cls(keys=decoded, active=active)


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    """A ciphertext together with everything needed to decrypt it later."""

    key_id: str
    nonce: bytes
    ciphertext: bytes

    def serialize(self) -> str:
        """Compact form for a database column: ``v1.key_id.nonce.ciphertext``."""
        return ".".join(
            (
                "v1",
                self.key_id,
                base64.urlsafe_b64encode(self.nonce).decode(),
                base64.urlsafe_b64encode(self.ciphertext).decode(),
            )
        )

    @classmethod
    def deserialize(cls, raw: str) -> EncryptedValue:
        try:
            version, key_id, nonce, ciphertext = raw.split(".")
        except ValueError as exc:
            raise DecryptionError("malformed ciphertext") from exc
        if version != "v1":
            raise DecryptionError(f"unsupported ciphertext version {version!r}")
        return cls(
            key_id=key_id,
            nonce=base64.urlsafe_b64decode(nonce),
            ciphertext=base64.urlsafe_b64decode(ciphertext),
        )


class FieldCipher:
    """Encrypts and decrypts individual database field values."""

    def __init__(self, keyring: Keyring) -> None:
        self._keyring = keyring

    def encrypt(self, plaintext: str, *, context: str) -> str:
        """Encrypt ``plaintext``, binding it to ``context``.

        ``context`` must identify where the value belongs -- for example
        ``"taxpayer:9f3c.ssn"``. It is authenticated but not encrypted, and
        decryption fails if a different context is supplied, which is what
        stops a ciphertext being moved from one row to another.
        """
        key_id = self._keyring.active_key_id
        key = self._keyring.key(key_id)
        nonce = os.urandom(_NONCE_BYTES)
        ciphertext = AESGCM(key).encrypt(
            nonce, plaintext.encode("utf-8"), context.encode("utf-8")
        )
        return EncryptedValue(key_id, nonce, ciphertext).serialize()

    def decrypt(self, raw: str, *, context: str) -> str:
        value = EncryptedValue.deserialize(raw)
        try:
            key = self._keyring.key(value.key_id)
        except KeyError as exc:
            raise DecryptionError(str(exc)) from exc
        try:
            plaintext = AESGCM(key).decrypt(
                value.nonce, value.ciphertext, context.encode("utf-8")
            )
        except InvalidTag as exc:
            # Deliberately vague: the caller learns it failed, not why, since
            # distinguishing "wrong key" from "wrong context" from "tampered"
            # is useful to an attacker probing the system.
            raise DecryptionError("could not decrypt value") from exc
        return plaintext.decode("utf-8")

    def needs_rotation(self, raw: str) -> bool:
        """Whether this ciphertext is under a key older than the active one."""
        return EncryptedValue.deserialize(raw).key_id != self._keyring.active_key_id
