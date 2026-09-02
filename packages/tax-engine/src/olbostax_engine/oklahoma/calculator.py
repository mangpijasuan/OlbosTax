"""Oklahoma resident individual income tax -- Form 511.

Oklahoma begins from **federal adjusted gross income**, not federal taxable
income.  From there:

    Federal AGI
      + Oklahoma additions
      - Oklahoma subtractions
      = Oklahoma adjusted gross income
      - deduction (standard or itemized)
      - personal exemptions
      = Oklahoma taxable income
      -> graduated rate schedule
      - credits
      = Oklahoma tax

Every parameter used here lives in the Oklahoma rule file and every one of
them is currently marked ``REQUIRES OFFICIAL VERIFICATION``.  The code is
written to be correct *given* the rule values; it makes no claim that the rule
values are right.  That separation is deliberate: verifying the numbers is a
tax-professional task against Oklahoma Tax Commission publications, and it is
tracked in ``COMPLIANCE_STATUS.md`` rather than being asserted by the code.

Only full-year residents are computed.  Part-year residents and nonresidents
file Form 511-NR, which allocates income between Oklahoma and other states
using rules this module does not implement -- so rather than approximate them,
``capability.py`` blocks those returns outright.
"""

from __future__ import annotations

from decimal import Decimal

from olbostax_schema import (
    FederalResult,
    Jurisdiction,
    OklahomaResult,
    ResidencyStatus,
    StepKind,
    TaxReturnInput,
    TraceStep,
)
from olbostax_schema.money import ZERO, clamp_non_negative, to_whole_dollars
from olbostax_schema.trace import CalculationTrace

from ..calculations import apply_bracket_schedule
from ..federal.dependents import DependentStatus
from ..rules import RuleSet

__all__ = ["calculate_oklahoma_return"]

OK_STATE_CODE = "OK"


def _step(
    trace: CalculationTrace,
    code: str,
    label: str,
    amount: Decimal,
    *,
    kind: StepKind = StepKind.RESULT,
    form_line: str | None = None,
    inputs: dict[str, Decimal] | None = None,
    citation: str | None = None,
    detail: str | None = None,
) -> Decimal:
    return trace.add(
        TraceStep(
            code=code,
            label=label,
            amount=amount,
            kind=kind,
            jurisdiction=Jurisdiction.OKLAHOMA,
            form_line=form_line,
            inputs=inputs or {},
            rule_citation=citation,
            detail=detail,
        )
    )


def _oklahoma_withholding(tax_return: TaxReturnInput) -> Decimal:
    """State withholding, counted only when the document names Oklahoma.

    A taxpayer with a W-2 from a Texas employer has no state withholding to
    claim in Oklahoma; summing every ``state_income_tax`` box regardless of
    state code would hand them a refund they are not entitled to.
    """
    income = tax_return.income
    total = ZERO
    for w2 in income.w2s:
        if w2.box15_state.upper() == OK_STATE_CODE:
            total += w2.box17_state_income_tax
    for f in income.form_1099_ints:
        if f.box15_state.upper() == OK_STATE_CODE:
            total += f.box17_state_tax_withheld
    for f in income.form_1099_divs:
        if f.box15_state.upper() == OK_STATE_CODE:
            total += f.box16_state_tax_withheld
    for f in income.form_1099_necs:
        if f.box6_state.upper() == OK_STATE_CODE:
            total += f.box5_state_tax_withheld
    for f in income.form_1099_miscs:
        if f.box17_state.upper() == OK_STATE_CODE:
            total += f.box16_state_tax_withheld
    for f in income.form_1099_rs:
        if f.box15_state.upper() == OK_STATE_CODE:
            total += f.box14_state_tax_withheld
    for f in income.form_1099_gs:
        if f.box10a_state.upper() == OK_STATE_CODE:
            total += f.box11_state_income_tax_withheld
    return total


def _subtractions(
    tax_return: TaxReturnInput,
    federal: FederalResult,
    rules: RuleSet,
    trace: CalculationTrace,
) -> tuple[Decimal, dict[str, Decimal]]:
    """Oklahoma subtractions from federal AGI."""
    ok = tax_return.oklahoma
    detail: dict[str, Decimal] = {}
    total = ZERO
    citation = rules.citation("subtractions")

    # Social Security included in federal AGI is fully exempt in Oklahoma.
    if rules.boolean("subtractions.social_security_fully_exempt"):
        ss_taxable = trace.amount("FED_SS_TAXABLE")
        if ss_taxable > 0:
            detail["social_security"] = ss_taxable
            total += ss_taxable
            _step(
                trace, "OK_SUB_SOCIAL_SECURITY",
                "Social Security benefits (not taxed by Oklahoma)", ss_taxable,
                kind=StepKind.SUBTOTAL, form_line="Form 511, Schedule 511-B",
                citation=citation,
                detail=(
                    "Oklahoma does not tax Social Security benefits, so the part the "
                    "federal government taxed is subtracted here."
                ),
            )

    def _capped(value: Decimal, cap_path: str, code: str, label: str, key: str) -> None:
        nonlocal total
        if value <= 0:
            return
        cap = rules.decimal(cap_path)
        allowed = min(value, cap)
        detail[key] = allowed
        total += allowed
        _step(
            trace, code, label, allowed,
            kind=StepKind.LIMITATION if allowed < value else StepKind.SUBTOTAL,
            form_line="Form 511, Schedule 511-B",
            inputs={"claimed": value, "cap": cap},
            citation=citation,
            detail=(
                f"Oklahoma limits this subtraction to ${cap:,.0f}."
                if allowed < value
                else None
            ),
        )

    _capped(
        ok.us_government_retirement_benefits,
        "subtractions.us_government_retirement_cap",
        "OK_SUB_US_RETIREMENT",
        "Federal civil service retirement",
        "us_government_retirement",
    )
    _capped(
        ok.oklahoma_government_retirement_benefits,
        "subtractions.oklahoma_government_retirement_cap",
        "OK_SUB_OK_RETIREMENT",
        "Oklahoma government retirement",
        "oklahoma_government_retirement",
    )

    if ok.military_retirement_benefits > 0:
        pct = rules.decimal("subtractions.military_retirement_percentage")
        allowed = ok.military_retirement_benefits * pct
        detail["military_retirement"] = allowed
        total += allowed
        _step(
            trace, "OK_SUB_MILITARY_RETIREMENT", "Military retirement", allowed,
            kind=StepKind.SUBTOTAL, form_line="Form 511, Schedule 511-B",
            citation=citation,
        )

    if ok.oklahoma_529_contributions > 0:
        cap = rules.decimal(
            f"subtractions.oklahoma_529_cap.{tax_return.filing_status.value}"
        )
        allowed = min(ok.oklahoma_529_contributions, cap)
        detail["oklahoma_529"] = allowed
        total += allowed
        _step(
            trace, "OK_SUB_529", "Oklahoma 529 college savings contributions", allowed,
            kind=StepKind.LIMITATION if allowed < ok.oklahoma_529_contributions else StepKind.SUBTOTAL,
            form_line="Form 511, Schedule 511-B",
            inputs={"contributed": ok.oklahoma_529_contributions, "cap": cap},
            citation=citation,
        )

    # US savings bond and Treasury interest is exempt from state taxation
    # under 31 U.S.C. s.3124, which is federal law binding on every state and
    # therefore does not depend on the Oklahoma rule file.
    treasury_interest = sum(
        (f.box3_interest_on_us_savings_bonds for f in tax_return.income.form_1099_ints), ZERO
    )
    if treasury_interest > 0:
        detail["us_treasury_interest"] = treasury_interest
        total += treasury_interest
        _step(
            trace, "OK_SUB_TREASURY_INTEREST", "US government interest", treasury_interest,
            kind=StepKind.SUBTOTAL, form_line="Form 511, Schedule 511-B",
            citation="31 U.S.C. s.3124",
            detail="States may not tax interest on US Treasury obligations.",
        )

    return total, detail


def _exemptions(
    tax_return: TaxReturnInput,
    dependent_statuses: list[DependentStatus],
    rules: RuleSet,
    oklahoma_agi: Decimal,
    trace: CalculationTrace,
) -> tuple[int, Decimal]:
    """Personal exemptions -- taxpayer, spouse, dependents, plus special ones."""
    count = 1
    if tax_return.filing_status.is_joint:
        count += 1
    count += sum(1 for s in dependent_statuses if s.is_dependent)

    # Special exemptions for age 65+ subject to an income test.
    threshold_age = rules.integer("personal_exemption.special_exemption_age_threshold")
    income_limit = rules.decimal(
        f"personal_exemption.special_exemption_income_limit.{tax_return.filing_status.value}"
    )
    if oklahoma_agi <= income_limit:
        if tax_return.taxpayer.age_on(tax_return.year_end) >= threshold_age:
            count += 1
        if (
            tax_return.spouse is not None
            and tax_return.filing_status.is_joint
            and tax_return.spouse.age_on(tax_return.year_end) >= threshold_age
        ):
            count += 1

    per = rules.decimal("personal_exemption.amount_per_exemption")
    amount = per * count
    _step(
        trace, "OK_EXEMPTIONS", "Oklahoma personal exemptions", amount,
        kind=StepKind.SUBTOTAL, form_line="Form 511, line 12",
        inputs={"exemptions": Decimal(count), "amount_each": per},
        citation=rules.citation("personal_exemption"),
        detail=f"Oklahoma allows ${per:,.0f} for each of your {count} exemption(s).",
    )
    return count, amount


def _credits(
    tax_return: TaxReturnInput,
    federal: FederalResult,
    rules: RuleSet,
    oklahoma_agi: Decimal,
    tax_before_credits: Decimal,
    trace: CalculationTrace,
) -> tuple[Decimal, Decimal, dict[str, Decimal]]:
    """Returns ``(nonrefundable, refundable, detail)``."""
    detail: dict[str, Decimal] = {}
    nonrefundable = ZERO
    refundable = ZERO
    citation = rules.citation("credits")

    # Oklahoma EIC as a percentage of the federal credit.
    federal_eitc = federal.credit_detail.get("earned_income_credit", ZERO)
    if federal_eitc > 0:
        rate = rules.decimal("credits.earned_income_credit.rate_of_federal")
        credit = federal_eitc * rate
        detail["earned_income_credit"] = credit
        if rules.boolean("credits.earned_income_credit.refundable"):
            refundable += credit
        else:
            nonrefundable += credit
        _step(
            trace, "OK_EITC", "Oklahoma Earned Income Credit", credit,
            form_line="Form 511, line 29",
            inputs={"federal_credit": federal_eitc, "rate": rate},
            citation=citation,
            detail=f"Oklahoma gives you {rate:.0%} of your federal Earned Income Credit.",
        )

    # Child care / child tax credit: the greater of two percentages, subject to
    # a federal AGI limit.
    agi_limit = rules.decimal("credits.child_care_child_tax_credit.federal_agi_limit")
    if federal.adjusted_gross_income <= agi_limit:
        care_rate = rules.decimal("credits.child_care_child_tax_credit.child_care_rate_of_federal")
        ctc_rate = rules.decimal("credits.child_care_child_tax_credit.child_tax_rate_of_federal")
        federal_care = federal.credit_detail.get("child_and_dependent_care", ZERO)
        federal_ctc = federal.credit_detail.get(
            "child_tax_credit", ZERO
        ) + federal.credit_detail.get("additional_child_tax_credit", ZERO)
        candidate = max(federal_care * care_rate, federal_ctc * ctc_rate)
        if candidate > 0:
            detail["child_care_child_tax"] = candidate
            nonrefundable += candidate
            _step(
                trace, "OK_CHILD_CREDIT", "Oklahoma child care / child tax credit", candidate,
                form_line="Form 511, line 16",
                inputs={"federal_child_care": federal_care, "federal_child_tax": federal_ctc},
                citation=citation,
                detail=(
                    "Oklahoma allows the greater of a share of your federal child care "
                    "credit or a share of your federal child tax credit."
                ),
            )

    # Sales tax relief credit.
    ok = tax_return.oklahoma
    if ok.claims_sales_tax_relief_credit:
        limit = rules.decimal("credits.sales_tax_relief_credit.income_limit_standard")
        if oklahoma_agi <= limit:
            per = rules.decimal("credits.sales_tax_relief_credit.amount_per_household_member")
            members = Decimal(max(ok.household_members_for_sales_tax_credit, 0))
            credit = per * members
            detail["sales_tax_relief"] = credit
            if rules.boolean("credits.sales_tax_relief_credit.refundable"):
                refundable += credit
            else:
                nonrefundable += credit
            _step(
                trace, "OK_SALES_TAX_RELIEF", "Oklahoma sales tax relief credit", credit,
                form_line="Form 511, line 30",
                inputs={"household_members": members, "amount_each": per},
                citation=citation,
            )

    nonrefundable = min(nonrefundable, clamp_non_negative(tax_before_credits))
    return nonrefundable, refundable, detail


def calculate_oklahoma_return(
    tax_return: TaxReturnInput,
    federal: FederalResult,
    rules: RuleSet,
    dependent_statuses: list[DependentStatus],
    trace: CalculationTrace,
) -> OklahomaResult:
    """Compute the Oklahoma resident return."""
    if tax_return.oklahoma.residency is not ResidencyStatus.FULL_YEAR_RESIDENT:
        raise ValueError(
            "calculate_oklahoma_return supports full-year residents only; "
            "part-year and nonresident returns require Form 511-NR, which is not "
            "implemented. capability.py must block this return before it reaches here."
        )

    result = OklahomaResult()
    result.federal_adjusted_gross_income = federal.adjusted_gross_income
    _step(
        trace, "OK_FEDERAL_AGI", "Federal adjusted gross income",
        federal.adjusted_gross_income, kind=StepKind.INPUT, form_line="Form 511, line 1",
        detail="Oklahoma starts from your federal adjusted gross income.",
    )

    ok = tax_return.oklahoma
    additions = ok.out_of_state_losses + ok.federal_net_operating_loss
    result.oklahoma_additions = additions
    if additions > 0:
        _step(
            trace, "OK_ADDITIONS", "Oklahoma additions", additions,
            kind=StepKind.SUBTOTAL, form_line="Form 511, Schedule 511-A",
        )

    subtractions, subtraction_detail = _subtractions(tax_return, federal, rules, trace)
    result.oklahoma_subtractions = subtractions
    result.subtraction_detail = subtraction_detail

    oklahoma_agi = federal.adjusted_gross_income + additions - subtractions
    result.oklahoma_adjusted_gross_income = oklahoma_agi
    _step(
        trace, "OK_AGI", "Oklahoma adjusted gross income", oklahoma_agi,
        form_line="Form 511, line 7",
        inputs={
            "federal_agi": federal.adjusted_gross_income,
            "additions": additions,
            "subtractions": subtractions,
        },
    )

    # Deduction. Oklahoma's standard deduction is set by state law and does not
    # follow the federal amount; itemizing in Oklahoma requires having itemized
    # federally.
    status = tax_return.filing_status.value
    ok_standard = rules.decimal(f"standard_deduction.{status}")
    may_itemize = federal.deduction_is_itemized or not rules.boolean(
        "standard_deduction.itemizing_requires_federal_itemizing"
    )
    use_itemized = may_itemize and federal.itemized_deductions > ok_standard
    deduction = federal.itemized_deductions if use_itemized else ok_standard

    result.deduction_taken = deduction
    result.deduction_is_itemized = use_itemized
    _step(
        trace, "OK_DEDUCTION",
        "Oklahoma itemized deductions" if use_itemized else "Oklahoma standard deduction",
        deduction, form_line="Form 511, line 10",
        inputs={"oklahoma_standard": ok_standard, "federal_itemized": federal.itemized_deductions},
        citation=rules.citation("standard_deduction"),
        detail=(
            "Oklahoma's standard deduction is set by state law and is different from the "
            f"federal amount of ${federal.standard_deduction:,.2f}."
            if not use_itemized
            else "You itemized on your federal return, so Oklahoma uses those deductions."
        ),
    )

    count, exemption_amount = _exemptions(
        tax_return, dependent_statuses, rules, oklahoma_agi, trace
    )
    result.exemptions_claimed = count
    result.exemption_amount = exemption_amount

    taxable = clamp_non_negative(oklahoma_agi - deduction - exemption_amount)
    result.oklahoma_taxable_income = taxable
    _step(
        trace, "OK_TAXABLE_INCOME", "Oklahoma taxable income", taxable,
        form_line="Form 511, line 13",
        inputs={
            "oklahoma_agi": oklahoma_agi,
            "deduction": deduction,
            "exemptions": exemption_amount,
        },
    )

    brackets = rules.brackets(f"tax_brackets.{status}")
    tax_before_credits = apply_bracket_schedule(taxable, brackets)
    result.oklahoma_tax_before_credits = tax_before_credits
    _step(
        trace, "OK_TAX_BEFORE_CREDITS", "Oklahoma tax", tax_before_credits,
        form_line="Form 511, line 14",
        inputs={"taxable_income": taxable},
        citation=rules.citation("tax_brackets"),
        detail="Oklahoma taxes income at graduated rates, like the federal government.",
    )

    nonrefundable, refundable, credit_detail = _credits(
        tax_return, federal, rules, oklahoma_agi, tax_before_credits, trace
    )
    result.credits = nonrefundable
    result.refundable_credits = refundable
    result.credit_detail = credit_detail

    total_tax = clamp_non_negative(tax_before_credits - nonrefundable)
    result.total_tax = total_tax
    _step(
        trace, "OK_TOTAL_TAX", "Total Oklahoma tax", total_tax,
        form_line="Form 511, line 20",
    )

    withholding = _oklahoma_withholding(tax_return)
    estimated = (
        tax_return.payments.estimated_tax_payments_oklahoma
        + tax_return.payments.prior_year_overpayment_applied_oklahoma
        + tax_return.payments.extension_payment_oklahoma
    )
    result.oklahoma_withholding = withholding
    result.estimated_payments = estimated
    total_payments = withholding + estimated + refundable
    result.total_payments = total_payments
    _step(
        trace, "OK_TOTAL_PAYMENTS", "Total Oklahoma payments", total_payments,
        form_line="Form 511, line 31",
        inputs={
            "withholding": withholding,
            "estimated_payments": estimated,
            "refundable_credits": refundable,
        },
    )

    net = total_payments - total_tax
    result.refund = to_whole_dollars(clamp_non_negative(net))
    result.amount_owed = to_whole_dollars(clamp_non_negative(-net))
    _step(
        trace,
        "OK_REFUND" if net >= 0 else "OK_AMOUNT_OWED",
        "Oklahoma refund" if net >= 0 else "Oklahoma amount you owe",
        result.refund if net >= 0 else result.amount_owed,
        form_line="Form 511, line 33" if net >= 0 else "Form 511, line 38",
    )

    result.required_forms = ["Form 511"]
    if subtractions > 0:
        result.required_forms.append("Schedule 511-B")
    if additions > 0:
        result.required_forms.append("Schedule 511-A")
    return result
