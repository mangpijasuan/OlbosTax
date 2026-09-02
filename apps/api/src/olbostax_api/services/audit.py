"""Audit and security event recording.

Spec section 25: immutable audit events, never containing raw SSNs or account
numbers. The guarantee here is structural -- ``record`` passes every metadata
payload through the redaction layer before it reaches the database, so a
caller that carelessly includes taxpayer detail in an audit event produces a
redacted row rather than a permanent disclosure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from olbostax_security import hash_ip, redact_mapping
from sqlalchemy.orm import Session

from ..models import AuditLog, SecurityEvent, SensitiveDataAccess
from ..settings import get_settings

__all__ = ["AuditEvent", "SecuritySeverity", "record", "record_security_event",
           "record_sensitive_access"]


class AuditEvent(str, Enum):
    """The events worth being able to reconstruct later."""

    USER_REGISTERED = "USER_REGISTERED"
    USER_LOGGED_IN = "USER_LOGGED_IN"
    USER_LOGGED_OUT = "USER_LOGGED_OUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    EMAIL_VERIFIED = "EMAIL_VERIFIED"
    MFA_ENABLED = "MFA_ENABLED"
    MFA_DISABLED = "MFA_DISABLED"
    SESSION_REVOKED = "SESSION_REVOKED"

    TAXPAYER_CREATED = "TAXPAYER_CREATED"
    TAXPAYER_UPDATED = "TAXPAYER_UPDATED"

    TAX_RETURN_CREATED = "TAX_RETURN_CREATED"
    TAX_RETURN_UPDATED = "TAX_RETURN_UPDATED"
    TAX_RETURN_VERSION_CREATED = "TAX_RETURN_VERSION_CREATED"
    TAX_RETURN_FINALIZED = "TAX_RETURN_FINALIZED"
    TAX_RETURN_RECALCULATED = "TAX_RETURN_RECALCULATED"

    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_ACCESSED = "DOCUMENT_ACCESSED"
    DOCUMENT_DELETED = "DOCUMENT_DELETED"
    EXTRACTION_VERIFIED = "EXTRACTION_VERIFIED"

    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"

    BANK_INFORMATION_CHANGED = "BANK_INFORMATION_CHANGED"
    CONSENT_GRANTED = "CONSENT_GRANTED"
    RETURN_SIGNED = "RETURN_SIGNED"

    EFILE_SUBMITTED = "EFILE_SUBMITTED"
    EFILE_ACKNOWLEDGMENT_RECEIVED = "EFILE_ACKNOWLEDGMENT_RECEIVED"
    EFILE_BLOCKED = "EFILE_BLOCKED"

    ADMIN_LOGGED_IN = "ADMIN_LOGGED_IN"
    ADMIN_ACCESSED_RETURN = "ADMIN_ACCESSED_RETURN"
    SENSITIVE_DATA_ACCESSED = "SENSITIVE_DATA_ACCESSED"
    RULE_SET_APPROVED = "RULE_SET_APPROVED"

    DATA_EXPORT_REQUESTED = "DATA_EXPORT_REQUESTED"
    ACCOUNT_DELETION_REQUESTED = "ACCOUNT_DELETION_REQUESTED"


class SecuritySeverity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _hashed(ip: str | None) -> str | None:
    if not ip:
        return None
    return hash_ip(ip, get_settings().ip_hash_salt)


def record(
    db: Session,
    event: AuditEvent,
    *,
    user_id: str | None = None,
    admin_user_id: str | None = None,
    tax_return_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an audit event.

    Does not commit: the audit row joins the caller's transaction, so an event
    is recorded if and only if the action it describes actually happened. An
    audit log full of events for transactions that rolled back is worse than
    no log, because it is confidently wrong.
    """
    entry = AuditLog(
        event_type=event.value,
        occurred_at=datetime.now(UTC),
        user_id=user_id,
        admin_user_id=admin_user_id,
        tax_return_id=tax_return_id,
        target_type=target_type,
        target_id=target_id,
        ip_hash=_hashed(ip),
        user_agent=(user_agent or "")[:512] or None,
        event_metadata=redact_mapping(metadata) if metadata else None,
    )
    db.add(entry)
    return entry


def record_security_event(
    db: Session,
    event_type: str,
    severity: SecuritySeverity,
    *,
    user_id: str | None = None,
    ip: str | None = None,
    risk_score: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> SecurityEvent:
    entry = SecurityEvent(
        event_type=event_type,
        severity=severity.value,
        occurred_at=datetime.now(UTC),
        user_id=user_id,
        ip_hash=_hashed(ip),
        risk_score=risk_score,
        event_metadata=redact_mapping(metadata) if metadata else None,
    )
    db.add(entry)
    return entry


def record_sensitive_access(
    db: Session,
    *,
    admin_user_id: str,
    user_id: str,
    field_accessed: str,
    justification: str,
    support_case_id: str | None = None,
    ip: str | None = None,
) -> SensitiveDataAccess:
    """Log a staff member viewing unmasked taxpayer data.

    ``justification`` is required and free text. Requiring a person to type why
    they are looking is a meaningful control: it makes casual browsing
    deliberate, and it gives an investigation something to compare against the
    support case.
    """
    if not justification.strip():
        raise ValueError("a justification is required to access sensitive data")

    entry = SensitiveDataAccess(
        admin_user_id=admin_user_id,
        user_id=user_id,
        field_accessed=field_accessed,
        justification=justification.strip()[:512],
        support_case_id=support_case_id,
        accessed_at=datetime.now(UTC),
        ip_hash=_hashed(ip),
    )
    db.add(entry)
    record(
        db,
        AuditEvent.SENSITIVE_DATA_ACCESSED,
        admin_user_id=admin_user_id,
        user_id=user_id,
        target_type="taxpayer_field",
        target_id=field_accessed,
        ip=ip,
        metadata={"justification": justification.strip()[:512]},
    )
    return entry
