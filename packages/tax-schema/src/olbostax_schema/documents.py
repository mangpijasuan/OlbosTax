"""Income document models -- the structured form of a W-2 or 1099.

Each model mirrors the boxes on the real paper form, using the box numbers as
field names where the box number is what a taxpayer sees.  Resisting the urge
to "clean up" the naming keeps the UI, the OCR extractor, the validation rules
and the return serializer all speaking the same language as the document in
the taxpayer's hand.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import IncomeDocumentType
from .money import ZERO, to_cents
from .provenance import Provenance

MoneyField = Annotated[Decimal, Field(default=ZERO)]


class _DocumentBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    document_id: str = Field(description="Stable id used to trace a value back to its source")
    document_type: IncomeDocumentType
    provenance: Provenance = Field(
        default_factory=Provenance,
        description="Where these values came from and whether a human confirmed them",
    )

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_money(cls, value: object, info) -> object:  # type: ignore[no-untyped-def]
        """Normalise every ``Decimal`` field to two decimal places on input."""
        field = cls.model_fields.get(info.field_name or "")
        if field is None or field.annotation is not Decimal:
            return value
        return to_cents(value) if value is not None else value


class W2(_DocumentBase):
    """Form W-2, Wage and Tax Statement."""

    document_type: IncomeDocumentType = IncomeDocumentType.W2

    employer_name: str = ""
    employer_ein: str = ""
    employee_is_spouse: bool = False

    box1_wages: MoneyField
    box2_federal_income_tax_withheld: MoneyField
    box3_social_security_wages: MoneyField
    box4_social_security_tax_withheld: MoneyField
    box5_medicare_wages: MoneyField
    box6_medicare_tax_withheld: MoneyField
    box7_social_security_tips: MoneyField
    box8_allocated_tips: MoneyField
    box10_dependent_care_benefits: MoneyField
    box11_nonqualified_plans: MoneyField
    box12: list["W2Box12Entry"] = Field(default_factory=list)
    box13_statutory_employee: bool = False
    box13_retirement_plan: bool = False
    box13_third_party_sick_pay: bool = False

    box15_state: str = ""
    box16_state_wages: MoneyField
    box17_state_income_tax: MoneyField
    box18_local_wages: MoneyField
    box19_local_income_tax: MoneyField
    box20_locality_name: str = ""


class W2Box12Entry(BaseModel):
    """One Box 12 code/amount pair.

    Box 12 is a list, not a set of fixed fields: a single W-2 can carry several
    entries and the same code can legitimately appear twice (e.g. two 401(k)
    plans).  Modelling it as a list avoids losing data the taxpayer may need.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=2)
    amount: Decimal = ZERO

    @field_validator("code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("amount", mode="before")
    @classmethod
    def _cents(cls, value: object) -> Decimal:
        return to_cents(value)


class Form1099INT(_DocumentBase):
    """Form 1099-INT, Interest Income."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_INT

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1_interest_income: MoneyField
    box2_early_withdrawal_penalty: MoneyField
    box3_interest_on_us_savings_bonds: MoneyField
    box4_federal_income_tax_withheld: MoneyField
    box8_tax_exempt_interest: MoneyField
    box9_specified_private_activity_bond_interest: MoneyField
    box13_bond_premium_on_tax_exempt_bond: MoneyField
    box15_state: str = ""
    box17_state_tax_withheld: MoneyField


class Form1099DIV(_DocumentBase):
    """Form 1099-DIV, Dividends and Distributions."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_DIV

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1a_total_ordinary_dividends: MoneyField
    box1b_qualified_dividends: MoneyField
    box2a_total_capital_gain_distributions: MoneyField
    box2b_unrecaptured_section_1250_gain: MoneyField
    box3_nondividend_distributions: MoneyField
    box4_federal_income_tax_withheld: MoneyField
    box5_section_199a_dividends: MoneyField
    box12_exempt_interest_dividends: MoneyField
    box15_state: str = ""
    box16_state_tax_withheld: MoneyField


class Form1099NEC(_DocumentBase):
    """Form 1099-NEC, Nonemployee Compensation."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_NEC

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1_nonemployee_compensation: MoneyField
    box4_federal_income_tax_withheld: MoneyField
    box5_state_tax_withheld: MoneyField
    box6_state: str = ""


class Form1099MISC(_DocumentBase):
    """Form 1099-MISC, Miscellaneous Information."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_MISC

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1_rents: MoneyField
    box2_royalties: MoneyField
    box3_other_income: MoneyField
    box4_federal_income_tax_withheld: MoneyField
    box8_substitute_payments: MoneyField
    box16_state_tax_withheld: MoneyField
    box17_state: str = ""


class Form1099R(_DocumentBase):
    """Form 1099-R, Distributions From Pensions, Annuities, Retirement Plans."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_R

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1_gross_distribution: MoneyField
    box2a_taxable_amount: MoneyField
    box2b_taxable_amount_not_determined: bool = False
    box2b_total_distribution: bool = False
    box4_federal_income_tax_withheld: MoneyField
    box5_employee_contributions: MoneyField
    box7_distribution_codes: str = ""
    box7_ira_sep_simple: bool = False
    box14_state_tax_withheld: MoneyField
    box15_state: str = ""

    @field_validator("box7_distribution_codes")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class Form1099G(_DocumentBase):
    """Form 1099-G, Certain Government Payments (unemployment, state refunds)."""

    document_type: IncomeDocumentType = IncomeDocumentType.FORM_1099_G

    payer_name: str = ""
    payer_tin: str = ""
    recipient_is_spouse: bool = False

    box1_unemployment_compensation: MoneyField
    box2_state_or_local_refunds: MoneyField
    box4_federal_income_tax_withheld: MoneyField
    box11_state_income_tax_withheld: MoneyField
    box10a_state: str = ""


W2.model_rebuild()
