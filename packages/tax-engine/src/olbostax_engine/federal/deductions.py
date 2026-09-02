"""Standard and itemized deductions -- Form 1040 line 12, Schedule A.

The election between standard and itemized is made for the taxpayer: whichever
is larger wins, unless the taxpayer forces itemizing.  Forcing exists for one
real case -- a married-filing-separately taxpayer must itemize if their spouse
does, even when the standard deduction would be larger.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from olbostax_schema import Jurisdiction, StepKind, TaxReturnInput, TraceStep
from olbostax_schema.money import ZERO, clamp_non_negative
from olbostax_schema.trace import CalculationTrace

from ..calculations import phase_out_ratably
from ..rules import RuleSet

__all__ = ["DeductionResult", "calculate_deduction", "count_aged_or_blind", "senior_deduction"]


@dataclass(frozen=True, slots=True)
class DeductionResult:
    amount: Decimal
    is_itemized: bool
    standard: Decimal
    itemized: Decimal
    senior_deduction: Decimal


def count_aged_or_blind(tax_return: TaxReturnInput, rules: RuleSet) -> int:
    """Count the additional standard deduction conditions under IRC s.63(f).

    Each condition counts separately and per person: a blind 70-year-old on a
    joint return with a sighted 66-year-old spouse produces three.

    Age is measured as of 1 January following the tax year, so a taxpayer born
    on 1 January 1961 is treated as 65 for tax year 2025.  That off-by-one-day
    rule is real, is in the Form 1040 instructions, and costs a taxpayer real
    money when implemented as "age on 31 December".
    """
    from datetime import date

    threshold = rules.integer("standard_deduction.age_threshold")
    if rules.boolean("standard_deduction.age_measured_at_start_of_following_year"):
        reference = date(tax_return.tax_year + 1, 1, 1)
    else:
        reference = tax_return.year_end

    count = 0
    if tax_return.taxpayer.age_on(reference) >= threshold:
        count += 1
    if tax_return.taxpayer.is_blind:
        count += 1
    if tax_return.spouse is not None and tax_return.filing_status.is_joint:
        if tax_return.spouse.age_on(reference) >= threshold:
            count += 1
        if tax_return.spouse.is_blind:
            count += 1
    return count


def _standard_deduction(
    tax_return: TaxReturnInput, rules: RuleSet, earned_income: Decimal, trace: CalculationTrace
) -> Decimal:
    status = tax_return.filing_status.value
    base = rules.decimal(f"standard_deduction.base.{status}")

    # A taxpayer who can be claimed as someone else's dependent gets a reduced
    # standard deduction: the greater of a floor or their earned income plus an
    # increment, capped at the normal amount.
    if tax_return.taxpayer.can_be_claimed_as_dependent:
        floor = rules.decimal("standard_deduction.dependent_limit.floor")
        increment = rules.decimal("standard_deduction.dependent_limit.earned_income_increment")
        limited = min(max(floor, earned_income + increment), base)
        trace.add(
            TraceStep(
                code="FED_STD_DEDUCTION_DEPENDENT_LIMIT",
                label="Standard deduction limited (claimed as a dependent)",
                amount=limited,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                inputs={"normal_amount": base, "earned_income": earned_income},
                rule_citation="IRC s.63(c)(5)",
                detail=(
                    "Because someone else can claim you as a dependent, your standard "
                    "deduction is limited."
                ),
            )
        )
        base = limited

    extra_count = count_aged_or_blind(tax_return, rules)
    if extra_count:
        key = "married" if tax_return.filing_status.is_married else "unmarried"
        per = rules.decimal(f"standard_deduction.additional_aged_or_blind.{key}")
        additional = per * extra_count
        trace.add(
            TraceStep(
                code="FED_STD_DEDUCTION_ADDITIONAL",
                label="Additional standard deduction for age or blindness",
                amount=additional,
                kind=StepKind.SUBTOTAL,
                jurisdiction=Jurisdiction.FEDERAL,
                inputs={"per_condition": per, "conditions": Decimal(extra_count)},
                rule_citation="IRC s.63(f)",
                detail=(
                    f"You qualify for {extra_count} additional standard deduction "
                    f"amount(s) of ${per:,.0f} each for being 65 or older or blind."
                ),
            )
        )
        base += additional

    return base


def senior_deduction(
    tax_return: TaxReturnInput, rules: RuleSet, agi: Decimal, trace: CalculationTrace
) -> Decimal:
    """The additional deduction for taxpayers 65 and older.

    REQUIRES OFFICIAL VERIFICATION.  This provision, its amount, its phase-out
    and whether it is available to itemizers all need confirmation against the
    enacted statute and IRS guidance before this rule set can be certified.
    The implementation follows the rule file; the rule file is marked DRAFT.
    """
    from datetime import date

    if not rules.has("senior_deduction.amount_per_qualifying_individual"):
        return ZERO

    threshold_age = rules.integer("senior_deduction.age_threshold")
    reference = date(tax_return.tax_year + 1, 1, 1)

    qualifying = 1 if tax_return.taxpayer.age_on(reference) >= threshold_age else 0
    if (
        tax_return.spouse is not None
        and tax_return.filing_status.is_joint
        and tax_return.spouse.age_on(reference) >= threshold_age
    ):
        qualifying += 1
    if qualifying == 0:
        return ZERO

    per = rules.decimal("senior_deduction.amount_per_qualifying_individual")
    status = tax_return.filing_status.value
    phase_start = rules.decimal(f"senior_deduction.phaseout_threshold.{status}")
    rate = rules.decimal("senior_deduction.phaseout_rate")

    gross = per * qualifying
    excess = clamp_non_negative(agi - phase_start)
    allowed = clamp_non_negative(gross - excess * rate)

    trace.add(
        TraceStep(
            code="FED_SENIOR_DEDUCTION",
            label="Additional deduction for taxpayers 65 and older",
            amount=allowed,
            kind=StepKind.LIMITATION if allowed < gross else StepKind.SUBTOTAL,
            jurisdiction=Jurisdiction.FEDERAL,
            inputs={"before_phaseout": gross, "adjusted_gross_income": agi},
            rule_citation=rules.citation("senior_deduction"),
            detail=(
                f"An extra deduction of ${per:,.0f} per person aged 65 or older."
                + (f" Reduced because your income is above ${phase_start:,.0f}." if allowed < gross else "")
            ),
        )
    )
    return allowed


def _itemized_deductions(
    tax_return: TaxReturnInput, rules: RuleSet, agi: Decimal, trace: CalculationTrace
) -> Decimal:
    """Schedule A total."""
    d = tax_return.deductions

    # Medical: only the excess over a percentage of AGI counts.
    floor_rate = rules.decimal("itemized_deductions.medical_agi_floor_rate")
    medical_floor = agi * floor_rate
    medical = clamp_non_negative(d.medical_expenses - medical_floor)
    if d.medical_expenses > 0:
        trace.add(
            TraceStep(
                code="FED_ITEMIZED_MEDICAL",
                label="Deductible medical expenses",
                amount=medical,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                form_line="Schedule A, line 4",
                inputs={"expenses": d.medical_expenses, "agi_floor": medical_floor},
                rule_citation="IRC s.213(a)",
                detail=(
                    f"Only medical expenses above {floor_rate:.1%} of your income "
                    f"(${medical_floor:,.2f}) can be deducted."
                ),
            )
        )

    # State and local taxes: income OR sales tax, plus property taxes, capped.
    income_or_sales = max(d.state_and_local_income_taxes, d.state_and_local_sales_taxes)
    salt_raw = income_or_sales + d.real_estate_taxes + d.personal_property_taxes
    cap_path = (
        "itemized_deductions.salt_cap_mfs"
        if tax_return.filing_status.value == "MARRIED_FILING_SEPARATELY"
        else "itemized_deductions.salt_cap"
    )
    salt_cap = rules.decimal(cap_path)

    # The cap itself phases down for high incomes but never below a floor.
    if rules.has("itemized_deductions.salt_cap_phasedown_threshold"):
        pd_threshold = rules.decimal("itemized_deductions.salt_cap_phasedown_threshold")
        pd_rate = rules.decimal("itemized_deductions.salt_cap_phasedown_rate")
        pd_floor = rules.decimal("itemized_deductions.salt_cap_phasedown_floor")
        if agi > pd_threshold:
            salt_cap = max(pd_floor, salt_cap - (agi - pd_threshold) * pd_rate)

    salt = min(salt_raw, salt_cap)
    if salt_raw > salt:
        trace.add(
            TraceStep(
                code="FED_ITEMIZED_SALT_CAP",
                label="State and local taxes limited",
                amount=salt,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                form_line="Schedule A, line 5e",
                inputs={"taxes_paid": salt_raw, "cap": salt_cap},
                rule_citation="IRC s.164(b)(6)",
                detail=(
                    f"State and local taxes are capped at ${salt_cap:,.0f} on this return. "
                    f"You paid ${salt_raw:,.2f}."
                ),
            )
        )

    charitable = d.charitable_cash + d.charitable_noncash
    charitable_cap = agi * rules.decimal("itemized_deductions.charitable_agi_limit_cash")
    if charitable > charitable_cap:
        trace.add(
            TraceStep(
                code="FED_ITEMIZED_CHARITABLE_CAP",
                label="Charitable contributions limited",
                amount=charitable_cap,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                inputs={"contributed": charitable, "agi_limit": charitable_cap},
                rule_citation="IRC s.170(b)",
            )
        )
        charitable = charitable_cap

    total = (
        medical
        + salt
        + d.home_mortgage_interest
        + d.investment_interest
        + charitable
        + d.other_itemized_deductions
    )
    trace.add(
        TraceStep(
            code="FED_ITEMIZED_TOTAL",
            label="Total itemized deductions",
            amount=total,
            kind=StepKind.SUBTOTAL,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule A, line 17",
        )
    )
    return total


def calculate_deduction(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    agi: Decimal,
    earned_income: Decimal,
    trace: CalculationTrace,
) -> DeductionResult:
    """Choose and compute the deduction on Form 1040 line 12."""
    standard = _standard_deduction(tax_return, rules, earned_income, trace)
    itemized = _itemized_deductions(tax_return, rules, agi, trace)

    use_itemized = tax_return.deductions.force_itemize or itemized > standard
    chosen = itemized if use_itemized else standard

    senior = senior_deduction(tax_return, rules, agi, trace)
    total = chosen + senior

    trace.add(
        TraceStep(
            code="FED_DEDUCTION",
            label="Itemized deductions" if use_itemized else "Standard deduction",
            amount=total,
            kind=StepKind.RESULT,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Form 1040, line 12",
            inputs={"standard": standard, "itemized": itemized, "senior_deduction": senior},
            rule_citation=rules.citation("standard_deduction"),
            detail=(
                (
                    f"Itemizing saves you more: your itemized deductions of "
                    f"${itemized:,.2f} beat the ${standard:,.2f} standard deduction."
                )
                if use_itemized
                else (
                    f"We used the standard deduction of ${standard:,.2f} because it is "
                    f"larger than your ${itemized:,.2f} of itemized deductions."
                )
            ),
        )
    )
    return DeductionResult(
        amount=total,
        is_itemized=use_itemized,
        standard=standard,
        itemized=itemized,
        senior_deduction=senior,
    )
