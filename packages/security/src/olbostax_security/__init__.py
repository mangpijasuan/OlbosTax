"""Security primitives shared across OlbosTax services.

Centralised so that there is one implementation of each control to review,
test and update -- and so that a change to password parameters or redaction
patterns takes effect everywhere at once.
"""

from .encryption import (
    DecryptionError,
    EncryptedValue,
    FieldCipher,
    Keyring,
    LocalKeyring,
    generate_key,
)
from .passwords import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    generate_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from .redaction import REDACTED, RedactingFilter, install, redact_mapping, redact_text
from .sessions import (
    LoginThrottle,
    SessionManager,
    SessionToken,
    TOTPEnrollment,
    create_totp_secret,
    generate_recovery_codes,
    hash_ip,
    totp_provisioning_uri,
    verify_totp,
)

__all__ = [
    "DecryptionError", "EncryptedValue", "FieldCipher", "Keyring", "LocalKeyring",
    "LoginThrottle", "PASSWORD_MAX_LENGTH", "PASSWORD_MIN_LENGTH", "REDACTED",
    "RedactingFilter", "SessionManager", "SessionToken", "TOTPEnrollment",
    "create_totp_secret", "generate_key", "generate_recovery_codes", "generate_token",
    "hash_ip", "hash_password", "install", "needs_rehash", "redact_mapping",
    "redact_text", "totp_provisioning_uri", "verify_password", "verify_totp",
]
