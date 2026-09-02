"""Session tokens, MFA and login throttling."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt
import pyotp

__all__ = [
    "SessionToken",
    "SessionManager",
    "TOTPEnrollment",
    "create_totp_secret",
    "totp_provisioning_uri",
    "verify_totp",
    "LoginThrottle",
    "hash_ip",
]

_ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class SessionToken:
    token: str
    session_id: str
    expires_at: datetime


class SessionManager:
    """Issues and validates signed session tokens.

    Tokens carry a ``session_id`` that identifies a row in the sessions table.
    That indirection is what makes revocation possible: a stateless JWT cannot
    be withdrawn before it expires, and "sign out all devices" has to actually
    sign out all devices when a taxpayer suspects their account is compromised.

    Access tokens are short-lived and refreshed against the session row, so a
    revoked session stops working within the access token's lifetime rather
    than at its natural expiry.
    """

    def __init__(
        self,
        secret: str,
        *,
        access_ttl: timedelta = timedelta(minutes=15),
        session_ttl: timedelta = timedelta(hours=12),
    ) -> None:
        if len(secret) < 32:
            raise ValueError("session secret must be at least 32 characters")
        self._secret = secret
        self.access_ttl = access_ttl
        self.session_ttl = session_ttl

    def issue(self, user_id: str, session_id: str, *, mfa_satisfied: bool) -> SessionToken:
        now = datetime.now(UTC)
        expires = now + self.access_ttl
        payload = {
            "sub": user_id,
            "sid": session_id,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "amr": ["pwd", "mfa"] if mfa_satisfied else ["pwd"],
            "iss": "olbostax",
        }
        return SessionToken(
            token=jwt.encode(payload, self._secret, algorithm=_ALGORITHM),
            session_id=session_id,
            expires_at=expires,
        )

    def verify(self, token: str) -> dict[str, object]:
        """Decode and validate a token. Raises ``jwt.PyJWTError`` when invalid.

        The algorithm is pinned. Accepting the algorithm named in the token's
        own header is the classic JWT vulnerability: an attacker sets it to
        ``none`` and signs nothing.
        """
        return jwt.decode(
            token,
            self._secret,
            algorithms=[_ALGORITHM],
            issuer="olbostax",
            options={"require": ["exp", "iat", "sub", "sid"]},
        )

    @staticmethod
    def new_session_id() -> str:
        return secrets.token_urlsafe(24)


# ---------------------------------------------------------------------------
# Multi-factor authentication
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TOTPEnrollment:
    secret: str
    provisioning_uri: str
    recovery_codes: tuple[str, ...]


def create_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account_email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name="OlbosTax")


def verify_totp(secret: str, code: str, *, valid_window: int = 1) -> bool:
    """Verify a TOTP code.

    ``valid_window=1`` accepts the adjacent 30-second steps, which absorbs
    clock skew between the user's phone and the server. Widening it further
    trades security for convenience and should not be done casually.
    """
    if not code or not code.strip().isdigit():
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window)


def generate_recovery_codes(count: int = 10) -> tuple[str, ...]:
    """Single-use codes for a user who has lost their authenticator.

    Stored hashed, exactly like passwords: a database leak must not hand over
    working second factors.
    """
    return tuple(
        f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        for _ in range(count)
    )


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


@dataclass
class _Attempts:
    count: int = 0
    first_at: float = field(default_factory=time.monotonic)
    locked_until: float = 0.0


class LoginThrottle:
    """Per-identifier login attempt limiting with exponential backoff.

    Keyed by account *and* by source address, because the two attacks differ:
    credential stuffing spreads a few attempts across many accounts (caught by
    the address key), while a targeted attack hammers one account (caught by
    the account key).

    This in-memory implementation is correct for a single process. A
    multi-process deployment needs the same logic backed by Redis, or an
    attacker simply spreads attempts across workers -- noted here because it
    is the kind of gap that silently appears at the first horizontal scale-up.
    """

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 900.0,
        base_lockout_seconds: float = 60.0,
        max_lockout_seconds: float = 3600.0,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.base_lockout_seconds = base_lockout_seconds
        self.max_lockout_seconds = max_lockout_seconds
        self._state: dict[str, _Attempts] = {}

    def is_locked(self, key: str) -> tuple[bool, float]:
        """Returns ``(locked, seconds_remaining)``."""
        record = self._state.get(key)
        if record is None:
            return False, 0.0
        remaining = record.locked_until - time.monotonic()
        return (True, remaining) if remaining > 0 else (False, 0.0)

    def record_failure(self, key: str) -> tuple[bool, float]:
        """Record a failed attempt. Returns ``(now_locked, lockout_seconds)``."""
        now = time.monotonic()
        record = self._state.get(key)
        if record is None or now - record.first_at > self.window_seconds:
            record = _Attempts(count=0, first_at=now)
            self._state[key] = record

        record.count += 1
        if record.count < self.max_attempts:
            return False, 0.0

        # Backoff doubles with each lockout beyond the threshold, so a
        # persistent attacker faces rapidly growing delays while a user who
        # mistyped twice more is inconvenienced for a minute.
        excess = record.count - self.max_attempts
        lockout = min(
            self.base_lockout_seconds * (2**excess), self.max_lockout_seconds
        )
        record.locked_until = now + lockout
        return True, lockout

    def record_success(self, key: str) -> None:
        self._state.pop(key, None)

    def prune(self) -> None:
        """Drop expired records so the map does not grow without bound."""
        now = time.monotonic()
        self._state = {
            key: record
            for key, record in self._state.items()
            if record.locked_until > now or now - record.first_at <= self.window_seconds
        }


def hash_ip(address: str, salt: str) -> str:
    """A stable, non-reversible identifier for a source address.

    Audit logs need to answer "were these two events from the same place?"
    without storing the address itself, which is personal data with its own
    retention obligations. The salt is per-deployment and secret, so the
    hashes cannot be reversed with a rainbow table over the IPv4 space --
    which is entirely feasible against an unsalted hash.
    """
    return hmac.new(salt.encode(), address.encode(), hashlib.sha256).hexdigest()[:32]
