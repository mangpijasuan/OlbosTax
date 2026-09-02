"""The complete input to the tax engine: everything the taxpayer told us.

``TaxReturnInput`` is a pure data structure.  It contains no computed values,
which is what makes it possible to re-run a return under a different rule
version and get a defensible answer -- the inputs are facts about the
taxpayer's year, and the outputs are consequences of those facts under a
specific set of rules.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .documents import (
    Form1099DIV,
    Form1099G,
    Form1099INT,
    Form1099MISC,
    Form1099NEC,
    Form1099R,
    W2,
)
from .enums import AccountType, FilingStatus, RefundMethod, ResidencyStatus
from .money import ZERO
from .sensitive import BankAccountNumber, RoutingNumber
from .taxpayer import Dependent, Spouse, Taxpayer

MoneyField = Annotated[Decimal, Field(default=ZERO)]

__all__ = [
    "IncomeSection",
    "AdjustmentsSection",
    "DeductionsSection",
    "CreditsSection",
    "PaymentsSection",
    "OklahomaSection",
    "DirectDeposit",
    "TaxReturnInput",
]


class IncomeSection(BaseModel):
    """Income the taxpayer reported, both from documents and typed directly."""

    model_config = ConfigDict(extra="forbid")

    w2s: list[W2] = Field(default_factory=list)
    form_1099_ints: list[Form1099INT] = Field(default_factory=list)
    form_1099_divs: list[Form1099DIV] = Field(default_factory=list)
    form_1099_necs: list[Form1099NEC] = Field(default_factory=list)
    form_1099_miscs: list[Form1099MISC] = Field(default_factory=list)
    form_1099_rs: list[Form1099R] = Field(default_factory=list)
    form_1099_gs: list[Form1099G] = Field(default_factory=list)

    # Amounts with no supporting document in the MVP document set.
    other_interest_income: MoneyField
    other_taxable_income: MoneyField
    other_taxable_income_description: str = ""

    # Capital gains.  The MVP accepts a net figure the taxpayer computed or
    # imported; per-lot Schedule D basis tracking is out of scope and the
    # capability matrix says so.
    net_short_term_capital_gain_loss: MoneyField
    net_long_term_capital_gain_loss: MoneyField

    # Self-employment expenses offsetting 1099-NEC / 1099-MISC income.
    self_employment_expenses: MoneyField
    self_employment_business_description: str = ""

    social_security_benefits_received: MoneyField
    """Gross Social Security from Form SSA-1099 box 5. The taxable portion is
    computed by the engine under IRC s.86; it is never entered directly."""

    alimony_received: MoneyField
    alimony_received_agreement_date: date | None = Field(
        default=None,
        description=(
            "Alimony under agreements executed after 2018 is not taxable to the "
            "recipient (TCJA s.11051). The engine needs the date to decide."
        ),
    )


class AdjustmentsSection(BaseModel):
    """Above-the-line deductions -- Schedule 1, Part II."""

    model_config = ConfigDict(extra="forbid")

    educator_expenses: MoneyField
    hsa_deduction: MoneyField
    self_employed_health_insurance: MoneyField
    ira_deduction: MoneyField
    student_loan_interest_paid: MoneyField
    alimony_paid: MoneyField
    alimony_paid_recipient_ssn: str = ""
    alimony_paid_agreement_date: date | None = None


class DeductionsSection(BaseModel):
    """Itemized deduction inputs and the standard/itemized election."""

    model_config = ConfigDict(extra="forbid")

    force_itemize: bool = Field(
        default=False,
        description=(
            "Itemize even when the standard deduction is larger. Rare but real: "
            "a MFS taxpayer must itemize if their spouse itemizes."
        ),
    )
    medical_expenses: MoneyField
    state_and_local_income_taxes: MoneyField
    state_and_local_sales_taxes: MoneyField
    real_estate_taxes: MoneyField
    personal_property_taxes: MoneyField
    home_mortgage_interest: MoneyField
    investment_interest: MoneyField
    charitable_cash: MoneyField
    charitable_noncash: MoneyField
    other_itemized_deductions: MoneyField


class CreditsSection(BaseModel):
    """Credit inputs. Eligibility is decided by the engine, not by the UI."""

    model_config = ConfigDict(extra="forbid")

    child_care_expenses: MoneyField
    child_care_provider_count: int = 0
    education_expenses_aotc: MoneyField
    education_expenses_llc: MoneyField
    retirement_savings_contributions: MoneyField
    energy_efficient_home_improvements: MoneyField

    taxpayer_had_foreign_income: bool = False
    foreign_tax_paid: MoneyField


class PaymentsSection(BaseModel):
    """Tax already paid, other than withholding shown on income documents."""

    model_config = ConfigDict(extra="forbid")

    estimated_tax_payments_federal: MoneyField
    estimated_tax_payments_oklahoma: MoneyField
    prior_year_overpayment_applied_federal: MoneyField
    prior_year_overpayment_applied_oklahoma: MoneyField
    extension_payment_federal: MoneyField
    extension_payment_oklahoma: MoneyField


class OklahomaSection(BaseModel):
    """Oklahoma-specific inputs that have no federal analogue.

    Every field here is governed by Oklahoma statute and the values that drive
    them live in the Oklahoma rule set, not in this model.  See
    ``docs/TAX_ENGINE_DESIGN.md`` on why the amounts are not fields here.
    """

    model_config = ConfigDict(extra="forbid")

    residency: ResidencyStatus = ResidencyStatus.FULL_YEAR_RESIDENT
    months_resident: int = Field(default=12, ge=0, le=12)

    # Subtractions the taxpayer asserts they qualify for.  The engine applies
    # the statutory caps; the taxpayer supplies the underlying amount.
    us_government_retirement_benefits: MoneyField
    oklahoma_government_retirement_benefits: MoneyField
    military_retirement_benefits: MoneyField
    social_security_included_in_federal_agi: MoneyField
    oklahoma_529_contributions: MoneyField

    # Additions.
    out_of_state_losses: MoneyField
    federal_net_operating_loss: MoneyField

    # Credit inputs.
    claims_sales_tax_relief_credit: bool = False
    household_members_for_sales_tax_credit: int = 0
    other_state_tax_paid: MoneyField
    other_state_income: MoneyField


class DirectDeposit(BaseModel):
    """Refund destination.

    Changing these values on an existing return requires re-authentication at
    the API layer (see ``apps/api/.../routers/returns.py``); redirecting a
    refund is the highest-value action an account takeover can perform.
    """

    model_config = ConfigDict(extra="forbid")

    routing_number: RoutingNumber
    account_number: BankAccountNumber
    account_type: AccountType


class TaxReturnInput(BaseModel):
    """Everything the engine needs, and nothing it computes."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tax_year: int = Field(ge=2024, le=2035)
    filing_status: FilingStatus

    taxpayer: Taxpayer
    spouse: Spouse | None = None
    dependents: list[Dependent] = Field(default_factory=list)

    income: IncomeSection = Field(default_factory=IncomeSection)
    adjustments: AdjustmentsSection = Field(default_factory=AdjustmentsSection)
    deductions: DeductionsSection = Field(default_factory=DeductionsSection)
    credits: CreditsSection = Field(default_factory=CreditsSection)
    payments: PaymentsSection = Field(default_factory=PaymentsSection)
    oklahoma: OklahomaSection = Field(default_factory=OklahomaSection)

    refund_method: RefundMethod = RefundMethod.DIRECT_DEPOSIT
    direct_deposit: DirectDeposit | None = None

    files_federal: bool = True
    files_oklahoma: bool = True

    @model_validator(mode="after")
    def _spouse_consistency(self) -> TaxReturnInput:
        """A joint return needs a spouse; a single return must not have one.

        This is a structural invariant rather than a tax rule, so it belongs in
        the schema: code downstream may assume ``spouse is not None`` whenever
        the status is joint, and that assumption should be enforced once here
        rather than re-checked at every use.
        """
        if self.filing_status.is_joint and self.spouse is None:
            raise ValueError("married filing jointly requires spouse information")
        if not self.filing_status.is_married and self.spouse is not None:
            raise ValueError(f"{self.filing_status.label} returns must not include a spouse")
        return self

    @property
    def year_end(self) -> date:
        """31 December of the tax year -- the reference date for most age tests."""
        return date(self.tax_year, 12, 31)

    def all_documents(self) -> list[object]:
        """Every income document on the return, for provenance sweeps."""
        return [
            *self.income.w2s,
            *self.income.form_1099_ints,
            *self.income.form_1099_divs,
            *self.income.form_1099_necs,
            *self.income.form_1099_miscs,
            *self.income.form_1099_rs,
            *self.income.form_1099_gs,
        ]
