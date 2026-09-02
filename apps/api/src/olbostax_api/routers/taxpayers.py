"""Taxpayer profile endpoints: /api/v1/taxpayers."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from olbostax_schema import SSN, Address
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..database import get_db
from ..dependencies import CurrentUser, client_ip, current_user
from ..models import Taxpayer
from ..services import audit
from ..services.crypto import get_cipher

router = APIRouter(prefix="/api/v1/taxpayers", tags=["taxpayers"])


class TaxpayerRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=20)
    middle_initial: str = Field(default="", max_length=1)
    last_name: str = Field(min_length=1, max_length=25)
    ssn: SSN
    date_of_birth: date
    email: EmailStr
    phone: str = Field(default="", max_length=20)
    address: Address
    is_us_citizen_or_resident: bool = True


class TaxpayerResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    ssn_masked: str
    date_of_birth: date
    email: str
    address: Address


@router.get("/me")
def get_my_taxpayer(
    db: DbSession = Depends(get_db), user: CurrentUser = Depends(current_user)
) -> TaxpayerResponse:
    taxpayer = db.execute(
        select(Taxpayer).where(Taxpayer.user_id == user.id)
    ).scalar_one_or_none()
    if taxpayer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not entered your personal information yet.",
        )
    return _to_response(taxpayer)


@router.put("/me")
def upsert_my_taxpayer(
    payload: TaxpayerRequest,
    request: Request,
    db: DbSession = Depends(get_db),
    user: CurrentUser = Depends(current_user),
) -> TaxpayerResponse:
    """Create or update the taxpayer profile.

    The SSN is encrypted before it reaches the database, with only the last
    four digits stored in the clear so the UI can render a mask and support can
    confirm identity without a decryption.
    """
    if payload.date_of_birth >= date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please check the date of birth.",
        )

    taxpayer = db.execute(
        select(Taxpayer).where(Taxpayer.user_id == user.id)
    ).scalar_one_or_none()
    is_new = taxpayer is None

    if taxpayer is None:
        # The id is generated here rather than by flushing an empty row: the
        # encryption context needs it before the SSN can be encrypted, and
        # flushing first would attempt an INSERT with the not-null columns
        # still empty.
        taxpayer = Taxpayer(id=str(uuid.uuid4()), user_id=user.id)
        db.add(taxpayer)

    cipher = get_cipher()
    taxpayer.first_name = payload.first_name
    taxpayer.middle_initial = payload.middle_initial
    taxpayer.last_name = payload.last_name
    taxpayer.ssn_encrypted = cipher.encrypt(
        payload.ssn.reveal(), context=f"taxpayer:{taxpayer.id}.ssn"
    )
    taxpayer.ssn_last_four = payload.ssn.reveal()[-4:]
    taxpayer.date_of_birth = payload.date_of_birth
    taxpayer.email = str(payload.email)
    taxpayer.phone = payload.phone
    taxpayer.address_line1 = payload.address.line1
    taxpayer.address_line2 = payload.address.line2
    taxpayer.city = payload.address.city
    taxpayer.state = payload.address.state
    taxpayer.zip_code = payload.address.zip_code
    taxpayer.is_us_citizen_or_resident = payload.is_us_citizen_or_resident

    audit.record(
        db,
        audit.AuditEvent.TAXPAYER_CREATED if is_new else audit.AuditEvent.TAXPAYER_UPDATED,
        user_id=user.id,
        target_type="taxpayer",
        target_id=taxpayer.id,
        ip=client_ip(request),
    )
    db.commit()
    return _to_response(taxpayer)


def _to_response(taxpayer: Taxpayer) -> TaxpayerResponse:
    """Build the response. The SSN never leaves the server unmasked."""
    return TaxpayerResponse(
        id=taxpayer.id,
        first_name=taxpayer.first_name,
        last_name=taxpayer.last_name,
        ssn_masked=f"***-**-{taxpayer.ssn_last_four}",
        date_of_birth=taxpayer.date_of_birth,
        email=taxpayer.email,
        address=Address(
            line1=taxpayer.address_line1 or "unknown",
            line2=taxpayer.address_line2,
            city=taxpayer.city or "unknown",
            state=taxpayer.state or "OK",
            zip_code=taxpayer.zip_code or "00000",
        ),
    )
