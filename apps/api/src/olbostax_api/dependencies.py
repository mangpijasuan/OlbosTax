"""Request-scoped dependencies: authentication, authorization and context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from olbostax_security import SessionManager
from sqlalchemy.orm import Session as DbSession

from .database import get_db
from .models import AdminUser, Session, User
from .settings import Settings, get_settings

__all__ = [
    "CurrentUser",
    "current_user",
    "require_mfa",
    "client_ip",
    "session_manager",
    "AdminContext",
    "current_admin",
    "require_role",
]


def session_manager(settings: Settings = Depends(get_settings)) -> SessionManager:
    from datetime import timedelta

    return SessionManager(
        settings.session_secret,
        access_ttl=timedelta(minutes=settings.session_access_ttl_minutes),
        session_ttl=timedelta(hours=settings.session_ttl_hours),
    )


def client_ip(request: Request) -> str | None:
    """The client's address.

    ``X-Forwarded-For`` is trusted only because this service is expected to run
    behind a load balancer that overwrites it. If that ever stops being true,
    this becomes a spoofable input to the throttling and audit layers -- which
    is why it is read in exactly one place rather than at each call site.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user: User
    session: Session
    mfa_satisfied: bool

    @property
    def id(self) -> str:
        return self.user.id


def current_user(
    authorization: str = Header(default=""),
    db: DbSession = Depends(get_db),
    manager: SessionManager = Depends(session_manager),
) -> CurrentUser:
    """Authenticate the request.

    Validates the token's signature *and* the backing session row. Checking
    only the signature would leave a revoked session working until its token
    expired, which defeats "sign out all devices" at exactly the moment a
    taxpayer needs it.
    """
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = manager.verify(authorization[7:].strip())
    except jwt.PyJWTError:
        # Never echo the JWT library's reason: "signature verification failed"
        # versus "expired" tells an attacker which part of their forgery to fix.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    session = db.get(Session, str(claims["sid"]))
    if session is None or session.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has ended."
        )
    if session.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired."
        )
    if session.user_id != str(claims["sub"]):
        # The token's subject and the session's owner disagree. There is no
        # benign cause for this.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")

    user = db.get(User, session.user_id)
    if user is None or user.status not in ("ACTIVE", "DELETION_REQUESTED"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This account is not active."
        )

    return CurrentUser(user=user, session=session, mfa_satisfied=session.mfa_satisfied)


def require_mfa(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """Require a second factor for this operation.

    Applied to the actions worth stealing an account for: changing where a
    refund is deposited, signing a return, and exporting data. A password
    alone should not be enough to redirect someone's tax refund.
    """
    if not user.mfa_satisfied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please confirm your identity with your authenticator app to continue.",
        )
    return user


# ---------------------------------------------------------------------------
# Administrative access
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "SUPER_ADMIN": frozenset({"*"}),
    "TAX_ADMIN": frozenset(
        {"returns.read", "rules.read", "rules.approve", "efile.read", "efile.retry"}
    ),
    "SUPPORT": frozenset({"returns.read.masked", "cases.write", "users.read.masked"}),
    "SECURITY_ADMIN": frozenset(
        {"security.read", "audit.read", "users.suspend", "sessions.revoke"}
    ),
    "FINANCE": frozenset({"payments.read", "payments.refund"}),
    "READ_ONLY": frozenset({"returns.read.masked", "payments.read", "audit.read"}),
}


@dataclass(frozen=True, slots=True)
class AdminContext:
    admin: AdminUser

    @property
    def role(self) -> str:
        return self.admin.role

    def can(self, permission: str) -> bool:
        granted = ROLE_PERMISSIONS.get(self.role, frozenset())
        return "*" in granted or permission in granted


def current_admin(
    authorization: str = Header(default=""),
    db: DbSession = Depends(get_db),
    manager: SessionManager = Depends(session_manager),
) -> AdminContext:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")
    try:
        claims = manager.verify(authorization[7:].strip())
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired."
        ) from None

    admin = db.get(AdminUser, str(claims["sub"]))
    if admin is None or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    # Staff access to taxpayer data always requires a second factor. There is
    # no legitimate workflow where an administrator needs to reach taxpayer
    # records with a password alone.
    if "mfa" not in claims.get("amr", []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative access requires multi-factor authentication.",
        )
    return AdminContext(admin=admin)


def require_role(*permissions: str):
    """Dependency factory enforcing that the admin holds every permission."""

    def check(context: AdminContext = Depends(current_admin)) -> AdminContext:
        missing = [p for p in permissions if not context.can(p)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Your role does not permit this action ({', '.join(missing)}).",
            )
        return context

    return check
