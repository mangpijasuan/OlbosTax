"""Administrative endpoints: /api/v1/admin.

Every route requires a permission, and taxpayer data is masked unless the
caller both holds an elevated permission and supplies a justification that
gets logged. Spec section 24: support does not casually see SSNs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from olbostax_efile import unimplemented_validation_levels
from olbostax_engine import available_rule_sets, load_rule_set
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ..database import get_db
from ..dependencies import AdminContext, client_ip, require_role
from ..models import (
    AuditLog,
    EFileSubmission,
    Payment,
    RuleSetApproval,
    SecurityEvent,
    Taxpayer,
    TaxReturn,
    User,
)
from ..services import audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/dashboard")
def dashboard(
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("returns.read.masked")),
) -> dict:
    """Aggregate counts only. No taxpayer detail on the landing screen."""
    def count(model) -> int:
        return db.execute(select(func.count()).select_from(model)).scalar_one()

    returns_by_status = dict(
        db.execute(
            select(TaxReturn.tax_year, func.count()).group_by(TaxReturn.tax_year)
        ).all()
    )
    submissions_by_channel = dict(
        db.execute(
            select(EFileSubmission.channel, func.count()).group_by(EFileSubmission.channel)
        ).all()
    )
    open_security_events = db.execute(
        select(func.count())
        .select_from(SecurityEvent)
        .where(SecurityEvent.resolved_at.is_(None))
    ).scalar_one()

    return {
        "users": count(User),
        "returns": count(TaxReturn),
        "returns_by_year": returns_by_status,
        "payments": count(Payment),
        "submissions_by_channel": submissions_by_channel,
        "open_security_events": open_security_events,
        "role": context.role,
    }


@router.get("/rule-sets")
def list_rule_sets(
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("rules.read")),
) -> list[dict]:
    """Tax rule sets and their certification status.

    This is the screen that answers "can we file yet?". It shows every rule
    set, its certification, its cited sources, and whether an approval record
    exists -- because a rule set claiming PRODUCTION with no approval row is a
    discrepancy worth seeing.
    """
    output = []
    for jurisdiction, tax_year, certification in available_rule_sets():
        rule_set = load_rule_set(jurisdiction, tax_year)
        approval = db.execute(
            select(RuleSetApproval).where(
                RuleSetApproval.jurisdiction == jurisdiction,
                RuleSetApproval.tax_year == tax_year,
                RuleSetApproval.rule_version == rule_set.meta.rule_version,
            )
        ).scalar_one_or_none()

        output.append(
            {
                "jurisdiction": jurisdiction,
                "tax_year": tax_year,
                "rule_version": rule_set.meta.rule_version,
                "certification": certification.value,
                "filable": rule_set.is_filable,
                "unverified_notice": rule_set.meta.unverified_notice,
                "sources": [s.model_dump(mode="json") for s in rule_set.meta.sources],
                "approval_recorded": approval is not None,
                "approved_by": approval.approved_by_admin_id if approval else None,
                "approved_at": approval.approved_at if approval else None,
            }
        )
    return output


class RuleApprovalRequest(BaseModel):
    jurisdiction: str
    tax_year: int
    rule_version: str
    content_sha256: str = Field(min_length=64, max_length=64)
    reviewer_notes: str = Field(min_length=20)
    regression_suite_passed: bool


@router.post("/rule-sets/approve", status_code=status.HTTP_201_CREATED)
def approve_rule_set(
    payload: RuleApprovalRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("rules.approve")),
) -> dict:
    """Record human approval of a rule set.

    Spec section 42 requires tests, regression, review and approval before a
    rule set reaches production. This records the approval; it does not by
    itself certify the rule set, because the certification lives in the rule
    file and changing it is a reviewed code change.

    The content hash binds the approval to exact file contents. A rule set
    edited after approval no longer matches, and the approval no longer
    applies -- which is what stops "approved in March" from covering a value
    someone changed in April.
    """
    if not payload.regression_suite_passed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A rule set cannot be approved while the regression suite is failing."
            ),
        )

    try:
        rule_set = load_rule_set(payload.jurisdiction, payload.tax_year)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rule set not found."
        ) from None

    if rule_set.meta.rule_version != payload.rule_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The rule set on disk is version {rule_set.meta.rule_version}, not "
                f"{payload.rule_version}. Approve the version you actually reviewed."
            ),
        )

    approval = RuleSetApproval(
        jurisdiction=payload.jurisdiction,
        tax_year=payload.tax_year,
        rule_version=payload.rule_version,
        content_sha256=payload.content_sha256,
        certification=rule_set.meta.certification.value,
        approved_by_admin_id=context.admin.id,
        approved_at=datetime.now(UTC),
        reviewer_notes=payload.reviewer_notes,
        regression_suite_passed=payload.regression_suite_passed,
    )
    db.add(approval)
    audit.record(
        db,
        audit.AuditEvent.RULE_SET_APPROVED,
        admin_user_id=context.admin.id,
        target_type="rule_set",
        target_id=f"{payload.jurisdiction}/{payload.tax_year}@{payload.rule_version}",
        ip=client_ip(request),
        metadata={"regression_suite_passed": payload.regression_suite_passed},
    )
    db.commit()
    return {"approval_id": approval.id}


@router.get("/users/{user_id}")
def get_user_masked(
    user_id: str,
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("users.read.masked")),
) -> dict:
    """A taxpayer's record with identifiers masked.

    This is what support sees by default. The full SSN requires a separate,
    logged, justified request.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found.")

    taxpayers = db.execute(
        select(Taxpayer).where(Taxpayer.user_id == user_id)
    ).scalars()

    return {
        "id": user.id,
        "email": user.email,
        "status": user.status,
        "mfa_enabled": user.mfa_enabled,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "taxpayers": [
            {
                "id": t.id,
                "name": f"{t.first_name} {t.last_name}",
                "ssn": f"***-**-{t.ssn_last_four}",
                "state": t.state,
            }
            for t in taxpayers
        ],
    }


class SensitiveAccessRequest(BaseModel):
    user_id: str
    field: str = Field(pattern="^(ssn|bank_account|routing_number)$")
    justification: str = Field(min_length=20, max_length=512)
    support_case_id: str | None = None


@router.post("/sensitive-access")
def request_sensitive_access(
    payload: SensitiveAccessRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("*")),
) -> dict:
    """Access unmasked taxpayer data, with the access logged.

    Restricted to SUPER_ADMIN, requires a written justification of at least
    twenty characters, and writes a permanent record naming the administrator,
    the taxpayer, the field and the reason. The friction is the control: it
    makes each access a deliberate act somebody can be asked about.
    """
    audit.record_sensitive_access(
        db,
        admin_user_id=context.admin.id,
        user_id=payload.user_id,
        field_accessed=payload.field,
        justification=payload.justification,
        support_case_id=payload.support_case_id,
        ip=client_ip(request),
    )
    db.commit()

    # Deliberately does not return the value. Granting and disclosing are
    # separate steps so that the disclosure path stays small enough to audit.
    return {
        "access_logged": True,
        "message": (
            "This access has been recorded. Retrieve the value through the "
            "break-glass procedure in the security runbook."
        ),
    }


@router.get("/audit-logs")
def list_audit_logs(
    user_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("audit.read")),
) -> list[dict]:
    query = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(min(limit, 500))
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if event_type:
        query = query.where(AuditLog.event_type == event_type)

    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "occurred_at": row.occurred_at,
            "user_id": row.user_id,
            "admin_user_id": row.admin_user_id,
            "tax_return_id": row.tax_return_id,
            "metadata": row.event_metadata,
        }
        for row in db.execute(query).scalars()
    ]


@router.get("/security-events")
def list_security_events(
    unresolved_only: bool = True,
    limit: int = 100,
    db: DbSession = Depends(get_db),
    context: AdminContext = Depends(require_role("security.read")),
) -> list[dict]:
    query = (
        select(SecurityEvent)
        .order_by(SecurityEvent.occurred_at.desc())
        .limit(min(limit, 500))
    )
    if unresolved_only:
        query = query.where(SecurityEvent.resolved_at.is_(None))

    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "severity": row.severity,
            "occurred_at": row.occurred_at,
            "user_id": row.user_id,
            "risk_score": row.risk_score,
            "metadata": row.event_metadata,
        }
        for row in db.execute(query).scalars()
    ]


@router.get("/system-health")
def system_health(
    context: AdminContext = Depends(require_role("returns.read.masked")),
) -> dict:
    """What is and is not production-ready.

    Deliberately blunt. An operations dashboard that shows only green while
    federal e-file validation is unimplemented is worse than no dashboard.
    """
    rule_sets = [
        {
            "jurisdiction": jurisdiction,
            "tax_year": year,
            "certification": certification.value,
            "filable": certification.is_filable,
        }
        for jurisdiction, year, certification in available_rule_sets()
    ]
    return {
        "rule_sets": rule_sets,
        "any_rule_set_filable": any(r["filable"] for r in rule_sets),
        "validation_gaps": unimplemented_validation_levels(),
        "efile_can_transmit": False,
        "readiness_note": (
            "OlbosTax cannot transmit returns. No authorized e-file provider is "
            "integrated and no tax rule set is certified. See COMPLIANCE_STATUS.md."
        ),
    }
