"""Taxpayer, spouse, dependent and address models."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import DependentRelationship, ResidencyStatus
from .sensitive import SSN

__all__ = ["Address", "Person", "Taxpayer", "Spouse", "Dependent"]

ZipCode = Annotated[str, Field(pattern=r"^\d{5}(-\d{4})?$")]
StateCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line1: str = Field(min_length=1, max_length=35)
    line2: str = Field(default="", max_length=35)
    city: str = Field(min_length=1, max_length=22)
    state: StateCode
    zip_code: ZipCode
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")

    @field_validator("state", "country", mode="before")
    @classmethod
    def _upper(cls, value: str) -> str:
        return str(value).strip().upper()


class Person(BaseModel):
    """Fields common to the taxpayer, the spouse and every dependent.

    ``validate_assignment`` is on because these models hold sensitive value
    types. Without it, ``taxpayer.ssn = "123456789"`` stores a plain ``str``,
    silently discarding the masking and reveal() discipline that the ``SSN``
    type exists to enforce -- the field would then render in full in any log
    line or error message that touched it.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    first_name: str = Field(min_length=1, max_length=20)
    middle_initial: str = Field(default="", max_length=1)
    last_name: str = Field(min_length=1, max_length=25)
    ssn: SSN
    date_of_birth: date

    def age_on(self, reference: date) -> int:
        """Age in completed years as of ``reference``.

        Note for callers: several tax provisions measure age as of 1 January of
        the *following* year (a taxpayer who turns 65 on 1 January 2026 is
        treated as 65 for tax year 2025).  That adjustment is applied by the
        rule that needs it, not silently here, so the reference date used is
        always visible at the call site.
        """
        born = self.date_of_birth
        had_birthday = (reference.month, reference.day) >= (born.month, born.day)
        return reference.year - born.year - (0 if had_birthday else 1)


class Taxpayer(Person):
    email: str = Field(max_length=254)
    phone: str = Field(default="", max_length=20)
    address: Address
    is_blind: bool = False
    can_be_claimed_as_dependent: bool = Field(
        default=False,
        description="Whether another taxpayer can claim this person as a dependent",
    )
    state_residency: ResidencyStatus = ResidencyStatus.FULL_YEAR_RESIDENT
    is_us_citizen_or_resident: bool = True


class Spouse(Person):
    is_blind: bool = False
    can_be_claimed_as_dependent: bool = False
    is_us_citizen_or_resident: bool = True
    date_of_death: date | None = Field(
        default=None,
        description="Set when the spouse died during the tax year; drives filing-status rules",
    )


class Dependent(Person):
    """A claimed dependent.

    The boolean tests here are the taxpayer's answers to the qualifying-child
    and qualifying-relative questions.  The engine applies IRC s.152 to these
    answers; it does not infer them.  Storing the raw answers rather than a
    derived "is_qualifying_child" flag means a rule change can be re-applied to
    an existing return without re-interviewing the taxpayer.
    """

    relationship: DependentRelationship
    months_lived_with_taxpayer: int = Field(ge=0, le=12, default=12)
    is_student: bool = Field(default=False, description="Full-time student for 5+ months")
    is_permanently_disabled: bool = False
    is_us_citizen_or_resident: bool = True
    provided_over_half_own_support: bool = False
    taxpayer_provided_over_half_support: bool = True
    files_joint_return_with_spouse: bool = False
    claimed_by_another_taxpayer: bool = False
    has_valid_ssn_for_employment: bool = Field(
        default=True,
        description=(
            "Child Tax Credit requires an SSN valid for employment; "
            "an ITIN does not qualify"
        ),
    )
    is_disabled_and_needs_care: bool = False
    child_care_expenses_paid: bool = False
