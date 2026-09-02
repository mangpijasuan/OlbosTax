"""Authentication endpoints: /api/v1/auth."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from olbostax_security import (
    PASSWORD_MIN_LENGTH,
    LoginThrottle,
    SessionManager,
    create_totp_secret,
    generate_recovery_codes,
    generate_token,
    hash_password,
    needs_rehash,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..database import get_db
from ..dependencies import CurrentUser, client_ip, current_user, session_manager
from ..models import Session, User
from ..services import audit
from ..services.crypto import get_cipher
from ..settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Process-local. A multi-process deployment needs a shared store, or an
# attacker distributes attempts across workers and never trips the limit.
# Flagged in docs/THREAT_MODEL.md rather than left as a silent assumption.
_throttle = LoginThrottle()

GENERIC_LOGIN_FAILURE = "That email address and password combination is not correct."


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=1024)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)
    totp_code: str | None = Field(default=None, max_length=10)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_at: datetime
    mfa_required: bool = False
    mfa_enabled: bool = False


class MfaEnrollResponse(BaseModel):
    provisioning_uri: str
    recovery_codes: list[str]


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    db: DbSession = Depends(get_db),
) -> dict[str, str]:
    """Create an account.

    Always reports success, even when the address is already registered.
    Telling an anonymous caller "that email is taken" turns registration into
    an account-existence oracle, which is how a credential-stuffing list gets
    filtered down to accounts worth attacking. The person who actually owns
    the address is told by email.
    """
    email = payload.email.lower().strip()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    if existing is None:
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            password_changed_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        audit.record(
            db,
            audit.AuditEvent.USER_REGISTERED,
            user_id=user.id,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        # TODO(notifications): send the verification email. Deliberately not
        # stubbed with a fake send, so that a missing notification service is a
        # visible gap rather than a silent one.
        db.commit()
    else:
        audit.record_security_event(
            db,
            "REGISTRATION_ATTEMPT_EXISTING_EMAIL",
            audit.SecuritySeverity.LOW,
            user_id=existing.id,
            ip=client_ip(request),
        )
        db.commit()

    return {
        "message": (
            "Check your email. If an account can be created for that address, "
            "we have sent a link to verify it."
        )
    }


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    manager: SessionManager = Depends(session_manager),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    email = payload.email.lower().strip()
    ip = client_ip(request)

    for key in (f"email:{email}", f"ip:{ip}"):
        locked, remaining = _throttle.is_locked(key)
        if locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Too many sign-in attempts. Please wait "
                    f"{int(remaining // 60) + 1} minute(s) and try again."
                ),
                headers={"Retry-After": str(int(remaining) + 1)},
            )

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # Passing None spends the same work as a real verification, so response
    # timing does not reveal whether the account exists.
    if not verify_password(user.password_hash if user else None, payload.password):
        for key in (f"email:{email}", f"ip:{ip}"):
            _throttle.record_failure(key)
        if user is not None:
            user.failed_login_count += 1
            audit.record(
                db, audit.AuditEvent.LOGIN_FAILED, user_id=user.id, ip=ip
            )
        audit.record_security_event(
            db, "LOGIN_FAILED", audit.SecuritySeverity.LOW, user_id=user.id if user else None, ip=ip
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_FAILURE
        )

    assert user is not None  # verify_password only succeeds with a real hash

    if user.status not in ("ACTIVE", "DELETION_REQUESTED"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account is not active."
        )

    mfa_satisfied = True
    if user.mfa_enabled:
        if not payload.totp_code:
            # A session is not issued here. Returning a token that merely lacks
            # the mfa claim would leave a password-only credential in the
            # client's hands, and a bug in one endpoint's mfa check would then
            # be enough to bypass the second factor entirely.
            return TokenResponse(
                access_token="",
                expires_at=datetime.now(UTC),
                mfa_required=True,
                mfa_enabled=True,
            )
        secret = get_cipher().decrypt(
            user.mfa_secret_encrypted, context=f"user:{user.id}.mfa_secret"
        )
        if not verify_totp(secret, payload.totp_code):
            _throttle.record_failure(f"email:{email}")
            audit.record_security_event(
                db, "MFA_FAILED", audit.SecuritySeverity.MEDIUM, user_id=user.id, ip=ip
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That authentication code is not correct.",
            )

    # Argon2 parameters are raised over time; migrate on the one occasion the
    # plaintext is legitimately available.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    _throttle.record_success(f"email:{email}")
    _throttle.record_success(f"ip:{ip}")
    user.failed_login_count = 0
    user.last_login_at = datetime.now(UTC)

    session = Session(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        mfa_satisfied=mfa_satisfied and user.mfa_enabled,
        ip_hash=None,
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
    )
    db.add(session)
    db.flush()

    token = manager.issue(user.id, session.id, mfa_satisfied=session.mfa_satisfied)
    audit.record(
        db,
        audit.AuditEvent.USER_LOGGED_IN,
        user_id=user.id,
        target_type="session",
        target_id=session.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    return TokenResponse(
        access_token=token.token,
        expires_at=token.expires_at,
        mfa_enabled=user.mfa_enabled,
    )


@router.post("/logout")
def logout(
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    user.session.revoked_at = datetime.now(UTC)
    audit.record(
        db,
        audit.AuditEvent.USER_LOGGED_OUT,
        user_id=user.id,
        target_type="session",
        target_id=user.session.id,
        ip=client_ip(request),
    )
    db.commit()
    return {"message": "Signed out."}


@router.get("/sessions")
def list_sessions(
    db: DbSession = Depends(get_db), user: CurrentUser = Depends(current_user)
) -> list[dict]:
    """Active sessions, so a taxpayer can see where their account is signed in."""
    rows = db.execute(
        select(Session)
        .where(Session.user_id == user.id, Session.revoked_at.is_(None))
        .order_by(Session.created_at.desc())
    ).scalars()
    return [
        {
            "id": s.id,
            "created_at": s.created_at,
            "expires_at": s.expires_at,
            "device": s.device_label or s.user_agent or "Unknown device",
            "current": s.id == user.session.id,
        }
        for s in rows
    ]


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    """Sign out everywhere, including the current session.

    Includes the current session on purpose: this is what a taxpayer clicks
    when they think someone else is in their account, and leaving the calling
    session alive would leave the attacker signed in if they are the caller.
    """
    now = datetime.now(UTC)
    sessions = db.execute(
        select(Session).where(Session.user_id == user.id, Session.revoked_at.is_(None))
    ).scalars()
    count = 0
    for session in sessions:
        session.revoked_at = now
        count += 1

    audit.record(
        db,
        audit.AuditEvent.SESSION_REVOKED,
        user_id=user.id,
        ip=client_ip(request),
        metadata={"revoked_count": count},
    )
    db.commit()
    return {"message": f"Signed out of {count} session(s). Please sign in again."}


@router.post("/mfa/enroll")
def enroll_mfa(
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> MfaEnrollResponse:
    """Begin MFA enrolment. Not active until a code is confirmed."""
    if user.user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Multi-factor authentication is already set up on this account.",
        )

    secret = create_totp_secret()
    codes = generate_recovery_codes()
    user.user.mfa_secret_encrypted = get_cipher().encrypt(
        secret, context=f"user:{user.id}.mfa_secret"
    )
    # Recovery codes are hashed exactly like passwords: a database leak must
    # not hand over working second factors.
    user.user.mfa_recovery_hashes = [hash_password(code + "padding-to-length") for code in codes]
    db.commit()

    return MfaEnrollResponse(
        provisioning_uri=totp_provisioning_uri(secret, user.user.email),
        recovery_codes=list(codes),
    )


class MfaConfirmRequest(BaseModel):
    totp_code: str = Field(max_length=10)


@router.post("/mfa/confirm")
def confirm_mfa(
    payload: MfaConfirmRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    if not user.user.mfa_secret_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Start MFA setup first."
        )
    secret = get_cipher().decrypt(
        user.user.mfa_secret_encrypted, context=f"user:{user.id}.mfa_secret"
    )
    if not verify_totp(secret, payload.totp_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code is not correct. Check your authenticator app and try again.",
        )

    user.user.mfa_enabled = True
    user.session.mfa_satisfied = True
    audit.record(
        db, audit.AuditEvent.MFA_ENABLED, user_id=user.id, ip=client_ip(request)
    )
    db.commit()
    return {"message": "Multi-factor authentication is now protecting your account."}


class PasswordResetRequest(BaseModel):
    email: EmailStr


@router.post("/password/reset-request")
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: DbSession = Depends(get_db),
) -> dict[str, str]:
    """Always reports the same thing, whether or not the account exists."""
    email = payload.email.lower().strip()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is not None:
        generate_token()  # TODO(notifications): store and email the reset link
        audit.record(
            db,
            audit.AuditEvent.PASSWORD_RESET_REQUESTED,
            user_id=user.id,
            ip=client_ip(request),
        )
        db.commit()
    return {
        "message": "If there is an account for that address, we have sent a reset link."
    }
