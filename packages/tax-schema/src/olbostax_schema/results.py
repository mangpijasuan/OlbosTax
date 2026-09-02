"""Computed results returned by the tax engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import CapabilityLevel, FilingStatus
from .money import ZERO, to_cents
from .trace import CalculationTrace

__all__ = [
    "FederalResult",
    "OklahomaResult",
    "CapabilityFinding",
    "TaxComputation",
]


class _RoundedResult(BaseModel):
    """A result whose reported lines are quantized to cents.

    Intermediate arithmetic runs at full Decimal precision -- rounding at every
    step would make the total depend on how many steps there happened to be.
    But the *reported* lines are money, and a stored AGI of
    ``55326.15420000000`` is not a dollar amount: it is arithmetic residue that
    would then be rendered inconsistently by the UI, compared unstably in
    tests, and written into the database at whatever scale the column allowed.

    So this is the documented boundary where precision becomes money. The
    calculation trace deliberately keeps full precision, because its job is to
    show the arithmetic, not to report the answer.
    """

    @model_validator(mode="after")
    def _quantize_money_fields(self) -> "_RoundedResult":
        for name, field in type(self).model_fields.items():
            if field.annotation is not Decimal:
                continue
            object.__setattr__(self, name, to_cents(getattr(self, name)))
        for name in ("credit_detail", "subtraction_detail"):
            mapping = getattr(self, name, None)
            if isinstance(mapping, dict):
                object.__setattr__(
                    self, name, {k: to_cents(v) for k, v in mapping.items()}
                )
        return self


class FederalResult(_RoundedResult):
    """Form 1040 bottom line, plus the intermediate lines the UI displays."""

    model_config = ConfigDict(extra="forbid")

    total_income: Decimal = ZERO
    adjustments_to_income: Decimal = ZERO
    adjusted_gross_income: Decimal = ZERO

    standard_deduction: Decimal = ZERO
    itemized_deductions: Decimal = ZERO
    deduction_taken: Decimal = ZERO
    deduction_is_itemized: bool = False
    qualified_business_income_deduction: Decimal = ZERO

    taxable_income: Decimal = ZERO
    tax_before_credits: Decimal = ZERO

    nonrefundable_credits: Decimal = ZERO
    other_taxes: Decimal = ZERO
    self_employment_tax: Decimal = ZERO
    total_tax: Decimal = ZERO

    federal_withholding: Decimal = ZERO
    estimated_payments: Decimal = ZERO
    refundable_credits: Decimal = ZERO
    total_payments: Decimal = ZERO

    refund: Decimal = ZERO
    amount_owed: Decimal = ZERO

    credit_detail: dict[str, Decimal] = Field(default_factory=dict)
    required_forms: list[str] = Field(default_factory=list)

    @property
    def effective_tax_rate(self) -> Decimal:
        """Total tax as a share of AGI. Display only -- never used in a calculation."""
        if self.adjusted_gross_income <= 0:
            return ZERO
        return self.total_tax / self.adjusted_gross_income


class OklahomaResult(_RoundedResult):
    """Oklahoma Form 511 bottom line."""

    model_config = ConfigDict(extra="forbid")

    federal_adjusted_gross_income: Decimal = ZERO
    oklahoma_additions: Decimal = ZERO
    oklahoma_subtractions: Decimal = ZERO
    oklahoma_adjusted_gross_income: Decimal = ZERO

    deduction_taken: Decimal = ZERO
    deduction_is_itemized: bool = False
    exemptions_claimed: int = 0
    exemption_amount: Decimal = ZERO

    oklahoma_taxable_income: Decimal = ZERO
    oklahoma_tax_before_credits: Decimal = ZERO

    credits: Decimal = ZERO
    refundable_credits: Decimal = ZERO
    total_tax: Decimal = ZERO

    oklahoma_withholding: Decimal = ZERO
    estimated_payments: Decimal = ZERO
    total_payments: Decimal = ZERO

    refund: Decimal = ZERO
    amount_owed: Decimal = ZERO

    credit_detail: dict[str, Decimal] = Field(default_factory=dict)
    subtraction_detail: dict[str, Decimal] = Field(default_factory=dict)
    required_forms: list[str] = Field(default_factory=list)


class CapabilityFinding(BaseModel):
    """A situation the engine detected and its support level.

    The engine reports what it found; the caller decides what to do about it.
    A ``blocks_filing`` finding must stop the return from reaching the e-file
    layer -- filing a return the engine knows it computed wrong is worse than
    telling the taxpayer we cannot help them this year.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    level: CapabilityLevel
    title: str
    explanation: str
    guidance: str = ""

    @property
    def blocks_filing(self) -> bool:
        return self.level.blocks_filing


class TaxComputation(BaseModel):
    """The complete, reproducible output of one engine run."""

    model_config = ConfigDict(extra="forbid")

    tax_year: int
    filing_status: FilingStatus

    engine_version: str
    federal_rule_version: str
    oklahoma_rule_version: str | None = None
    computed_at: datetime

    federal: FederalResult | None = None
    oklahoma: OklahomaResult | None = None

    trace: CalculationTrace = Field(default_factory=CalculationTrace)
    findings: list[CapabilityFinding] = Field(default_factory=list)

    rule_sets_certified_for_filing: bool = Field(
        default=False,
        description=(
            "True only when every rule set used carries PRODUCTION certification. "
            "The e-file layer refuses to submit when this is False."
        ),
    )

    @property
    def blocking_findings(self) -> list[CapabilityFinding]:
        return [f for f in self.findings if f.blocks_filing]

    @property
    def can_be_filed(self) -> bool:
        """Whether this computation is eligible to proceed toward e-file.

        Deliberately conservative: an uncertified rule set or any blocking
        capability finding stops the return.  Validation and payment add
        further gates on top of this one.
        """
        return self.rule_sets_certified_for_filing and not self.blocking_findings

    @property
    def combined_refund(self) -> Decimal:
        total = ZERO
        if self.federal:
            total += self.federal.refund - self.federal.amount_owed
        if self.oklahoma:
            total += self.oklahoma.refund - self.oklahoma.amount_owed
        return total
