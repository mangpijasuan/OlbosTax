"""E-file and payment endpoints.

The ordering enforced here is the product's core commitment: payment is taken
before submission, the taxpayer signs before anything is transmitted, and
nothing is transmitted at all while the tax rules are unverified.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from olbostax_efile import (
    EFileError,
    NotAuthorizedToTransmit,
    get_provider,
    unimplemented_validation_levels,
)
from olbostax_schema import Jurisdiction, ReturnStatus
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..database import get_db
from ..dependencies import CurrentUser, client_ip, current_user, require_mfa
from ..models import (
    Consent,
    EFileAcknowledgment,
    EFileSubmission,
    Payment,
    Signature,
)
from ..services import audit, returns as returns_service
from ..services.crypto import decrypt_return_input
from ..settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["efile"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="That return was not found."
    )


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    return_id: str


class CheckoutResponse(BaseModel):
    payment_id: str
    amount_cents: int
    amount_display: str
    currency: str = "USD"
    description: str
    processor_client_secret: str | None = None


@router.post("/payments/checkout")
def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> CheckoutResponse:
    """Begin payment for a return.

    One price, disclosed in full, with nothing conditional on the refund
    amount. The description is returned from the server so the checkout screen
    cannot show a different figure from the one that will be charged.
    """
    try:
        tax_return = returns_service.get_return(db, payload.return_id, user_id=user.id)
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    existing = db.execute(
        select(Payment).where(
            Payment.tax_return_id == tax_return.id, Payment.status == "PAID"
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already paid for this return.",
        )

    payment = Payment(
        user_id=user.id,
        tax_return_id=tax_return.id,
        amount=settings.filing_price_cents / 100,
        status="PENDING",
        processor_name=settings.payment_provider,
    )
    db.add(payment)
    db.flush()

    audit.record(
        db,
        audit.AuditEvent.PAYMENT_INITIATED,
        user_id=user.id,
        tax_return_id=tax_return.id,
        target_type="payment",
        target_id=payment.id,
        ip=client_ip(request),
        metadata={"amount_cents": settings.filing_price_cents},
    )
    db.commit()

    scope = []
    if tax_return.files_federal:
        scope.append("federal")
    if tax_return.files_oklahoma:
        scope.append("Oklahoma")

    return CheckoutResponse(
        payment_id=payment.id,
        amount_cents=settings.filing_price_cents,
        amount_display=settings.price_display,
        description=(
            f"OlbosTax {tax_return.tax_year} {' and '.join(scope)} tax filing. "
            f"{settings.price_display} total. No additional fees."
        ),
    )


class PaymentConfirmation(BaseModel):
    payment_id: str
    processor_reference: str = Field(min_length=1, max_length=128)


@router.post("/payments/confirm")
def confirm_payment(
    payload: PaymentConfirmation,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Record a completed payment.

    Spec section 20: a payment is never marked paid on the client's say-so. In
    production this endpoint verifies the reference against the processor's API,
    or -- better -- is replaced entirely by a signed processor webhook. The mock
    provider is the only case where the reference is accepted as given, and it
    is refused outside development.
    """
    payment = db.execute(
        select(Payment).where(
            Payment.id == payload.payment_id, Payment.user_id == user.id
        )
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found."
        )

    if settings.payment_provider != "mock":
        # No processor adapter is implemented. Failing here is correct: the
        # alternative is trusting a client-supplied reference in production,
        # which would let anyone mark their own return paid.
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Payment confirmation must be verified with the payment processor. "
                "No processor integration is configured."
            ),
        )
    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="No payment processor is configured for this environment.",
        )

    payment.status = "PAID"
    payment.paid_at = datetime.now(UTC)
    payment.processor_reference = payload.processor_reference
    audit.record(
        db,
        audit.AuditEvent.PAYMENT_COMPLETED,
        user_id=user.id,
        tax_return_id=payment.tax_return_id,
        target_type="payment",
        target_id=payment.id,
        ip=client_ip(request),
    )
    db.commit()
    return {"status": payment.status}


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


class SignRequest(BaseModel):
    return_id: str
    consent_to_file: bool
    consent_document_version: str
    typed_name: str = Field(min_length=1, max_length=128)
    self_select_pin: str | None = Field(default=None, min_length=5, max_length=5)


@router.post("/efile/sign")
def sign_return(
    payload: SignRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(require_mfa),
) -> dict[str, str]:
    """Record the taxpayer's authorization to file.

    Requires MFA. The signature is bound to a hash of the exact return content,
    so a return modified after signing no longer matches its signature -- which
    is the only thing that makes an electronic signature mean anything.
    """
    if not payload.consent_to_file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="We cannot file your return without your authorization.",
        )

    try:
        tax_return = returns_service.get_return(db, payload.return_id, user_id=user.id)
        version = returns_service.get_version(
            db, tax_return.current_version_id or "", user_id=user.id
        )
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    if version.status != ReturnStatus.READY_FOR_FILING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Please complete the review step before signing.",
        )

    content_hash = hashlib.sha256(
        repr(sorted(version.return_input.items())).encode()
    ).hexdigest()

    consent = Consent(
        user_id=user.id,
        tax_return_version_id=version.id,
        consent_type="AUTHORIZATION_TO_FILE",
        document_version=payload.consent_document_version,
        document_sha256=hashlib.sha256(
            payload.consent_document_version.encode()
        ).hexdigest(),
        granted=True,
        granted_at=datetime.now(UTC),
    )
    db.add(consent)

    signature = Signature(
        tax_return_version_id=version.id,
        user_id=user.id,
        signature_method="SELF_SELECT_PIN" if payload.self_select_pin else "TYPED_NAME",
        signed_at=datetime.now(UTC),
        signed_content_sha256=content_hash,
        self_select_pin_last_four=(
            payload.self_select_pin[-4:] if payload.self_select_pin else None
        ),
    )
    db.add(signature)
    db.flush()

    audit.record(
        db,
        audit.AuditEvent.RETURN_SIGNED,
        user_id=user.id,
        tax_return_id=tax_return.id,
        target_type="signature",
        target_id=signature.id,
        ip=client_ip(request),
    )
    audit.record(
        db,
        audit.AuditEvent.CONSENT_GRANTED,
        user_id=user.id,
        tax_return_id=tax_return.id,
        target_type="consent",
        target_id=consent.id,
        ip=client_ip(request),
    )
    db.commit()
    return {"signature_id": signature.id, "signed_at": signature.signed_at.isoformat()}


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


class SubmitRequest(BaseModel):
    return_id: str


@router.post("/efile/submit")
def submit_return(
    payload: SubmitRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(require_mfa),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Transmit the return through the configured provider.

    Every precondition is checked here, server side. Nothing about this
    decision depends on what the client believes.
    """
    try:
        tax_return = returns_service.get_return(db, payload.return_id, user_id=user.id)
        version = returns_service.get_version(
            db, tax_return.current_version_id or "", user_id=user.id
        )
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    if version.status == ReturnStatus.SUBMITTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This return has already been submitted.",
        )
    if version.status != ReturnStatus.READY_FOR_FILING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This return is not ready to file yet.",
        )

    payment = db.execute(
        select(Payment).where(
            Payment.tax_return_id == tax_return.id, Payment.status == "PAID"
        )
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Please complete payment of {settings.price_display} before filing.",
        )

    signature = db.execute(
        select(Signature).where(Signature.tax_return_version_id == version.id)
    ).scalar_one_or_none()
    if signature is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please sign your return before we can file it.",
        )

    # The signature must still match the return. If the content changed after
    # signing, the taxpayer authorized something other than what would be sent.
    current_hash = hashlib.sha256(
        repr(sorted(version.return_input.items())).encode()
    ).hexdigest()
    if current_hash != signature.signed_content_sha256:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Your return has changed since you signed it. Please review and sign "
                "again."
            ),
        )

    tax_input = decrypt_return_input(version.return_input)
    computation, _ = returns_service.calculate(
        db, version, user_id=user.id, ip=client_ip(request)
    )

    provider = get_provider(settings.efile_provider)
    jurisdictions = []
    if tax_return.files_federal:
        jurisdictions.append(Jurisdiction.FEDERAL)
    if tax_return.files_oklahoma and computation.oklahoma is not None:
        jurisdictions.append(Jurisdiction.OKLAHOMA)

    receipts = []
    for jurisdiction in jurisdictions:
        try:
            receipt = provider.submit_return(
                tax_input,
                computation,
                jurisdiction,
                signature_reference=signature.id,
                payment_reference=payment.id,
            )
        except NotAuthorizedToTransmit as exc:
            audit.record(
                db,
                audit.AuditEvent.EFILE_BLOCKED,
                user_id=user.id,
                tax_return_id=tax_return.id,
                ip=client_ip(request),
                metadata={"reason": str(exc), "jurisdiction": jurisdiction.value},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "We cannot file this return yet. "
                    + _explain_block(str(exc))
                ),
            ) from None
        except EFileError as exc:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Your return did not pass our checks: {exc}",
            ) from None

        submission = EFileSubmission(
            tax_return_version_id=version.id,
            jurisdiction=jurisdiction.value,
            submission_id=receipt.submission_id,
            provider_name=receipt.provider_name,
            provider_reference=receipt.provider_reference,
            channel=receipt.channel.value,
            status=receipt.status.value,
            submitted_at=receipt.submitted_at,
            payment_id=payment.id,
            signature_id=signature.id,
        )
        db.add(submission)
        receipts.append(receipt)

        audit.record(
            db,
            audit.AuditEvent.EFILE_SUBMITTED,
            user_id=user.id,
            tax_return_id=tax_return.id,
            target_type="efile_submission",
            target_id=receipt.submission_id,
            ip=client_ip(request),
            metadata={"jurisdiction": jurisdiction.value, "channel": receipt.channel.value},
        )

    version.status = ReturnStatus.SUBMITTED.value
    db.commit()

    return {
        "status": version.status,
        "submissions": [
            {
                "jurisdiction": r.jurisdiction.value,
                "submission_id": r.submission_id,
                "channel": r.channel.value,
                # Always present, always shown. A simulated submission that
                # looks like a real one is the failure this whole layer exists
                # to prevent.
                "disclaimer": r.channel.disclaimer,
                "is_real_filing": r.is_real_filing,
            }
            for r in receipts
        ],
    }


def _explain_block(reason: str) -> str:
    """Turn an internal refusal into something a taxpayer can act on."""
    if "not certified for filing" in reason:
        return (
            "The tax rules for this year are still being verified against official IRS "
            "and Oklahoma Tax Commission sources. We will email you as soon as filing "
            "opens. Everything you have entered is saved."
        )
    if "cannot file" in reason:
        return (
            "Your return includes something we do not support yet. Check the review "
            "page for details."
        )
    return "Please contact support."


@router.get("/efile/status/{return_id}")
def efile_status(
    return_id: str,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Filing status per jurisdiction, in plain language."""
    try:
        tax_return = returns_service.get_return(db, return_id, user_id=user.id)
        version = returns_service.get_version(
            db, tax_return.current_version_id or "", user_id=user.id
        )
    except returns_service.ReturnNotFound:
        raise _not_found() from None

    submissions = db.execute(
        select(EFileSubmission).where(EFileSubmission.tax_return_version_id == version.id)
    ).scalars()

    provider = get_provider(settings.efile_provider)
    result = []
    for submission in submissions:
        acknowledgment = db.execute(
            select(EFileAcknowledgment)
            .where(EFileAcknowledgment.submission_id == submission.id)
            .order_by(EFileAcknowledgment.received_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        result.append(
            {
                "jurisdiction": submission.jurisdiction,
                "status": acknowledgment.status if acknowledgment else submission.status,
                "channel": submission.channel,
                "is_real_filing": submission.channel == "LIVE",
                "submitted_at": submission.submitted_at,
                "authority_reference": (
                    acknowledgment.authority_reference if acknowledgment else None
                ),
                "rejection_issues": (
                    acknowledgment.rejection_issues if acknowledgment else None
                ),
            }
        )

    return {"return_id": return_id, "overall_status": version.status, "filings": result}


@router.get("/efile/validation-coverage")
def validation_coverage() -> dict:
    """What OlbosTax validates and what it does not.

    Public because a taxpayer deciding whether to trust this software with
    their return deserves to know that federal and state schema validation are
    not implemented.
    """
    return {
        "implemented": [
            {"level": "1", "name": "Input validation", "status": "IMPLEMENTED"},
            {"level": "2", "name": "Tax validation", "status": "IMPLEMENTED"},
            {"level": "3", "name": "Cross-form validation", "status": "IMPLEMENTED"},
        ],
        "not_implemented": unimplemented_validation_levels(),
    }
