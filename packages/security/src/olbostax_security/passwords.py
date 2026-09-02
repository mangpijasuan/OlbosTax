"""Password hashing with Argon2id.

Argon2id is chosen because it is memory-hard: an attacker with a stolen
database and a rack of GPUs gains far less advantage than against a purely
compute-bound function like PBKDF2 or a fast hash like SHA-256.

Three properties matter beyond the algorithm choice:

*   **Verification is constant-work regardless of outcome.** A user that does
    not exist still costs a full hash verification, so response timing does
    not reveal whether an email address is registered.
*   **Parameters are recorded in the hash.** Argon2's encoded form carries its
    own cost parameters, so raising them later does not invalidate existing
    hashes -- they are rehashed transparently on the next successful login.
*   **The plaintext never leaves this module.** No logging, no exception
    message, no telemetry.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = [
    "hash_password",
    "verify_password",
    "needs_rehash",
    "generate_token",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
]

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
"""An upper bound is a denial-of-service control: Argon2 on a megabyte of
input is expensive, and an attacker posting such passwords in volume can
exhaust CPU. 1024 characters is far beyond any legitimate passphrase."""

# OWASP Password Storage Cheat Sheet minimum for Argon2id (19 MiB, t=2, p=1).
# Memory cost dominates attacker economics; raise it when infrastructure
# allows and existing hashes will migrate on next login via needs_rehash.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# A valid hash of a random value, used to spend the same work on a login
# attempt for a nonexistent account as for a real one.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Hash a password for storage. Raises on a password outside the length bounds."""
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"password must be between {PASSWORD_MIN_LENGTH} and "
            f"{PASSWORD_MAX_LENGTH} characters"
        )
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Verify a password, spending equal work whether or not the account exists.

    Pass ``None`` as ``stored_hash`` when the account was not found. The dummy
    verification that follows takes the same time as a real one, so an attacker
    cannot enumerate registered email addresses by timing the login endpoint.
    """
    if stored_hash is None:
        try:
            _hasher.verify(_DUMMY_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass
        return False

    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Whether ``stored_hash`` was made with weaker parameters than current."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def generate_token(length: int = 32) -> str:
    """A URL-safe random token for email verification and password reset.

    ``secrets`` rather than ``random``: the latter is a Mersenne Twister whose
    internal state can be recovered from a few outputs, which for a password
    reset token means an attacker who requests a few resets can predict the
    next one.
    """
    return secrets.token_urlsafe(length)
