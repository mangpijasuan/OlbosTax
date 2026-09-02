"""Return endpoints: /api/v1/returns and /api/v1/calculations."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from olbostax_engine import CAPABILITY_MATRIX
from olbostax_schema import TaxReturnInput
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..database import get_db
from ..dependencies import CurrentUser, client_ip, current_user, require_mfa
from ..models import Taxpayer
from ..services import audit, returns as returns_service
from ..services.crypto import decrypt_return_input
from ..settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/returns", tags=["returns"])


def _not_found() -> HTTPException:
    """One response for "does not exist" and "not yours".

    Distinguishing them lets an attacker map which return ids are real by
    comparing 403 to 404, which is precisely what an IDOR probe measures.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="That return was not found."
    )


class CreateReturnRequest(BaseModel):
    tax_year: int
    return_input: TaxReturnInput


class ReturnSummary(BaseModel):
    id: str
    tax_year: int
    status: str
    version_number: int
    federal_refund: str | None = None
    federal_amount_owed: str | None = None
    oklahoma_refund: str | None = None
    oklahoma_amount_owed: str | None = None
    can_be_filed: bool = False
    updated_at: datetime


@router.get("")
def list_returns(
    db: DbSession = Depends(get_db), user: CurrentUser = Depends(current_user)
) -> list[ReturnSummary]:
    """Every return this user owns -- the dashboard's data source."""
    summaries: list[ReturnSummary] = []
    for tax_return in returns_service.list_returns(db, user_id=user.id):
        version = returns_service.current_version(db, tax_return)
        if version is None:
            continue
        calculation = returns_service.latest_calculation(db, version)
        summaries.append(
            ReturnSummary(
                id=tax_return.id,
                tax_year=tax_return.tax_year,
                status=version.status,
                version_number=version.version_number,
                federal_refund=str(calculation.federal_refund) if calculation else None,
                federal_amount_owed=(
                    str(calculation.federal_amount_owed) if calculation else None
                ),
                oklahoma_refund=str(calculation.oklahoma_refund) if calculation else None,
                oklahoma_amount_owed=(
                    str(calculation.oklahoma_amount_owed) if calculation else None
                ),
                can_be_filed=bool(calculation and calculation.rule_sets_certified),
                updated_at=version.updated_at,
            )
        )
    return summaries


@router.post("", status_code=status.HTTP_201_CREATED)
def create_return(
    payload: CreateReturnRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    taxpayer = (
        db.query(Taxpayer).filter(Taxpayer.user_id == user.id).one_or_none()
    )
    if taxpayer is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please complete your personal information before starting a return.",
        )

    tax_return, version = returns_service.create_return(
        db,
        user_id=user.id,
        taxpayer_id=taxpayer.id,
        tax_year=payload.tax_year,
        initial_input=payload.return_input,
        files_oklahoma=payload.return_input.files_oklahoma,
        ip=client_ip(request),
    )
    db.commit()
    return {"return_id": tax_return.id, "version_id": version.id}


@router.get("/capability-matrix")
def capability_matrix() -> dict[str, dict[str, str]]:
    """What OlbosTax can and cannot handle, for the marketing and help pages.

    Declared before ``/{return_id}``: FastAPI matches routes in declaration
    order, so a literal path that shares a prefix with a parameterised one must
    come first or it is swallowed as an id.
    """
    return {
        code: {"level": level.value, "description": description}
        for code, (level, description) in CAPABILITY_MATRIX.items()
    }


@router.get("/{return_id}")
def get_return(
    return_id: str,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict:
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    version = returns_service.current_version(db, tax_return)
    if version is None:
        raise _not_found()

    tax_input = decrypt_return_input(version.return_input)
    return {
        "id": tax_return.id,
        "tax_year": tax_return.tax_year,
        "version_id": version.id,
        "version_number": version.version_number,
        "status": version.status,
        # model_dump(mode="json") applies the masking serializers on the
        # sensitive value types, so the SSN leaves as ***-**-6789. The client
        # never needs the real digits: it renders a mask, and the only
        # consumer of the plaintext is the e-file serializer, server side.
        "return_input": tax_input.model_dump(mode="json"),
    }


class UpdateReturnRequest(BaseModel):
    return_input: TaxReturnInput


@router.put("/{return_id}")
def update_return(
    return_id: str,
    payload: UpdateReturnRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """Save interview progress and recalculate.

    Recalculation happens on every save so the taxpayer always sees a refund
    figure consistent with what they have entered. A stale number that updates
    only at review time is how people are surprised at the last step.
    """
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
        version = returns_service.mutable_version(
            db, tax_return.current_version_id or "", user_id=user.id
        )
    except returns_service.ReturnNotFound:
        raise _not_found() from None
    except returns_service.ReturnImmutable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None

    # Changing where a refund goes is the single most valuable thing an account
    # takeover can do, so it requires a second factor even mid-interview.
    previous = decrypt_return_input(version.return_input)
    if _deposit_changed(previous, payload.return_input) and not user.mfa_satisfied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Please confirm your identity with your authenticator app before "
                "changing your bank details."
            ),
        )

    returns_service.save_input(
        db, version, payload.return_input, user_id=user.id, ip=client_ip(request)
    )
    if _deposit_changed(previous, payload.return_input):
        audit.record(
            db,
            audit.AuditEvent.BANK_INFORMATION_CHANGED,
            user_id=user.id,
            tax_return_id=tax_return.id,
            ip=client_ip(request),
        )
        audit.record_security_event(
            db,
            "BANK_INFORMATION_CHANGED",
            audit.SecuritySeverity.HIGH,
            user_id=user.id,
            ip=client_ip(request),
        )

    computation, _ = returns_service.calculate(
        db, version, user_id=user.id, ip=client_ip(request)
    )
    db.commit()

    return {
        "version_id": version.id,
        "computation": _computation_response(computation),
    }


def _deposit_changed(before: TaxReturnInput, after: TaxReturnInput) -> bool:
    def fingerprint(value: TaxReturnInput) -> tuple[str, str, str] | None:
        deposit = value.direct_deposit
        if deposit is None:
            return None
        return (
            deposit.routing_number.reveal(),
            deposit.account_number.reveal(),
            deposit.account_type.value,
        )

    return fingerprint(before) != fingerprint(after)


@router.get("/{return_id}/calculation")
def get_calculation(
    return_id: str,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict:
    """The latest calculation, including the trace behind 'how was this figured?'."""
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    version = returns_service.current_version(db, tax_return)
    calculation = returns_service.latest_calculation(db, version) if version else None
    if calculation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This return has not been calculated yet.",
        )

    return {
        "computed_at": calculation.computed_at,
        "engine_version": calculation.engine_version,
        "federal_rule_version": calculation.federal_rule_version,
        "oklahoma_rule_version": calculation.oklahoma_rule_version,
        "rule_sets_certified": calculation.rule_sets_certified,
        "federal": calculation.federal_result,
        "oklahoma": calculation.oklahoma_result,
        "findings": calculation.findings,
        "trace": calculation.trace,
    }


@router.post("/{return_id}/finalize")
def finalize_return(
    return_id: str,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(require_mfa),
) -> dict:
    """Mark the return ready to file.

    Requires MFA: this is the step immediately before signing and submitting.
    """
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
        version = returns_service.mutable_version(
            db, tax_return.current_version_id or "", user_id=user.id
        )
    except returns_service.ReturnNotFound:
        raise _not_found() from None
    except returns_service.ReturnImmutable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None

    computation, _ = returns_service.calculate(
        db, version, user_id=user.id, ip=client_ip(request)
    )
    try:
        returns_service.finalize(
            db, version, computation, user_id=user.id, ip=client_ip(request)
        )
    except returns_service.ReturnImmutable as exc:
        db.commit()  # keep the calculation snapshot and audit trail
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None

    db.commit()
    return {"status": version.status, "computation": _computation_response(computation)}


class NewVersionRequest(BaseModel):
    reason: str


@router.post("/{return_id}/versions")
def create_new_version(
    return_id: str,
    payload: NewVersionRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> dict[str, str]:
    """Fork a new draft, e.g. to correct a rejected return."""
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    version = returns_service.create_version(
        db, tax_return, user_id=user.id, reason=payload.reason, ip=client_ip(request)
    )
    db.commit()
    return {"version_id": version.id, "version_number": str(version.version_number)}


def _computation_response(computation) -> dict:
    federal = computation.federal
    oklahoma = computation.oklahoma
    return {
        "federal": federal.model_dump(mode="json") if federal else None,
        "oklahoma": oklahoma.model_dump(mode="json") if oklahoma else None,
        "findings": [f.model_dump(mode="json") for f in computation.findings],
        "breakdown": [
            step.model_dump(mode="json") for step in computation.trace.breakdown()
        ],
        "can_be_filed": computation.can_be_filed,
        "rule_sets_certified": computation.rule_sets_certified_for_filing,
    }
