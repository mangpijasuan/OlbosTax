"""The federal return calculator -- orchestration and ordering.

Calculation order is not arbitrary and is the part most easily got wrong:

1.  Income other than Social Security.
2.  Adjustments that do not depend on SE tax -- needed because the taxable
    portion of Social Security is computed net of them.
3.  Self-employment tax, which depends on business income.
4.  Total adjustments, now including the deductible half of SE tax.
5.  AGI.
6.  Deduction (standard or itemized), which depends on AGI through the medical
    floor and the SALT phase-down.
7.  Taxable income.
8.  Tax, using the capital gains worksheet when applicable.
9.  Other taxes (Additional Medicare, NIIT).
10. Credits, nonrefundable first, then refundable.
11. Payments, then refund or balance due.

Each step is traced, so the stored computation can be replayed and explained
without re-running the engine.
"""

from __future__ import annotations

from decimal import Decimal

from olbostax_schema import (
    FederalResult,
    Jurisdiction,
    StepKind,
    TaxReturnInput,
    TraceStep,
)
from olbostax_schema.money import ZERO, clamp_non_negative, to_whole_dollars
from olbostax_schema.trace import CalculationTrace

from ..rules import RuleSet
from . import adjustments as adj_mod
from . import credits as credits_mod
from . import income as income_mod
from . import se_tax as se_mod
from . import tax as tax_mod
from .deductions import calculate_deduction
from .dependents import DependentStatus

__all__ = ["calculate_federal_return"]


def _step(
    trace: CalculationTrace,
    code: str,
    label: str,
    amount: Decimal,
    *,
    kind: StepKind = StepKind.RESULT,
    form_line: str | None = None,
    inputs: dict[str, Decimal] | None = None,
    detail: str | None = None,
) -> Decimal:
    return trace.add(
        TraceStep(
            code=code,
            label=label,
            amount=amount,
            kind=kind,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line=form_line,
            inputs=inputs or {},
            detail=detail,
        )
    )


def _federal_withholding(tax_return: TaxReturnInput) -> Decimal:
    income = tax_return.income
    return (
        sum((w.box2_federal_income_tax_withheld for w in income.w2s), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_ints), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_divs), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_necs), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_miscs), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_rs), ZERO)
        + sum((f.box4_federal_income_tax_withheld for f in income.form_1099_gs), ZERO)
    )


def _investment_income(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Investment income for the EITC disqualification test, IRC s.32(i)."""
    return (
        trace.amount("FED_TAXABLE_INTEREST")
        + trace.amount("FED_TAX_EXEMPT_INTEREST")
        + trace.amount("FED_ORDINARY_DIVIDENDS")
        + clamp_non_negative(trace.amount("FED_CAPITAL_GAIN"))
    )


def _required_forms(
    tax_return: TaxReturnInput, se_result: se_mod.SelfEmploymentTax, is_itemized: bool
) -> list[str]:
    """Only the forms the return actually needs.

    Spec section 17: do not generate forms that are not needed. An empty
    Schedule B attached to a return with no interest is noise for the taxpayer
    and extra surface for a schema validation rejection.
    """
    forms = ["Form 1040"]
    income = tax_return.income

    interest = (
        sum((f.box1_interest_income for f in income.form_1099_ints), ZERO)
        + income.other_interest_income
    )
    dividends = sum((f.box1a_total_ordinary_dividends for f in income.form_1099_divs), ZERO)
    # Schedule B is required above the reporting threshold or when there is
    # tax-exempt or foreign account interest to disclose.
    if interest > 1500 or dividends > 1500:
        forms.append("Schedule B")

    if is_itemized:
        forms.append("Schedule A")

    has_unemployment = any(f.box1_unemployment_compensation > 0 for f in income.form_1099_gs)
    has_adjustments = any(
        v > 0
        for v in (
            tax_return.adjustments.educator_expenses,
            tax_return.adjustments.hsa_deduction,
            tax_return.adjustments.ira_deduction,
            tax_return.adjustments.student_loan_interest_paid,
            tax_return.adjustments.self_employed_health_insurance,
        )
    )
    if has_unemployment or has_adjustments or se_result.total > 0:
        forms.append("Schedule 1")

    if se_result.total > 0:
        forms.extend(["Schedule C", "Schedule SE", "Schedule 2"])

    if (
        income.net_short_term_capital_gain_loss != 0
        or income.net_long_term_capital_gain_loss != 0
    ):
        forms.append("Schedule D")

    if tax_return.dependents:
        forms.append("Schedule 8812")

    if tax_return.credits.child_care_expenses > 0:
        forms.extend(["Form 2441", "Schedule 3"])
    if (
        tax_return.credits.education_expenses_aotc > 0
        or tax_return.credits.education_expenses_llc > 0
    ):
        forms.extend(["Form 8863", "Schedule 3"])

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(forms))


def calculate_federal_return(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    dependent_statuses: list[DependentStatus],
    trace: CalculationTrace,
) -> FederalResult:
    """Compute the complete federal return."""
    result = FederalResult()

    # -- Steps 1-2: income, and the adjustments Social Security depends on ---
    # A provisional AGI is needed to phase out the student loan interest
    # deduction, which is itself part of AGI. The Form 1040 instructions
    # resolve this circularity by using a modified AGI computed without that
    # deduction, which is what the provisional figure is.
    provisional_income, _ = income_mod.total_income(tax_return, rules, CalculationTrace(), ZERO)
    early_adjustments = adj_mod.adjustments_before_se_tax(
        tax_return, rules, provisional_income, CalculationTrace()
    )

    total_income, long_term_gain = income_mod.total_income(
        tax_return, rules, trace, early_adjustments
    )
    result.total_income = total_income

    earned = income_mod.earned_income(tax_return, trace)

    # -- Step 3: self-employment tax ---------------------------------------
    se_profit = trace.amount("FED_SE_NET_PROFIT")
    w2_social_security_wages = sum(
        (w.box3_social_security_wages + w.box7_social_security_tips for w in tax_return.income.w2s),
        ZERO,
    )
    se_result = se_mod.calculate_self_employment_tax(
        se_profit, w2_social_security_wages, rules, trace
    )
    result.self_employment_tax = se_result.total

    # -- Steps 4-5: adjustments and AGI ------------------------------------
    total_adjustments = adj_mod.total_adjustments(
        tax_return, rules, provisional_income, se_result.deductible_half, trace
    )
    result.adjustments_to_income = total_adjustments

    agi = total_income - total_adjustments
    result.adjusted_gross_income = agi
    _step(
        trace, "FED_AGI", "Adjusted gross income", agi,
        form_line="Form 1040, line 11",
        inputs={"total_income": total_income, "adjustments": total_adjustments},
        detail=(
            "Your adjusted gross income. Most tax benefits are based on this number, "
            "not on your gross pay."
        ),
    )

    # -- Step 6: deduction --------------------------------------------------
    deduction = calculate_deduction(tax_return, rules, agi, earned, trace)
    result.standard_deduction = deduction.standard
    result.itemized_deductions = deduction.itemized
    result.deduction_taken = deduction.amount
    result.deduction_is_itemized = deduction.is_itemized

    # -- Step 7: taxable income --------------------------------------------
    taxable_income = clamp_non_negative(agi - deduction.amount)
    result.taxable_income = taxable_income
    _step(
        trace, "FED_TAXABLE_INCOME", "Taxable income", taxable_income,
        form_line="Form 1040, line 15",
        inputs={"adjusted_gross_income": agi, "deduction": deduction.amount},
        detail="The income your tax is actually calculated on.",
    )

    # -- Step 8: tax --------------------------------------------------------
    qualified_dividends = trace.amount("FED_QUALIFIED_DIVIDENDS")
    tax_before_credits = tax_mod.calculate_tax_on_taxable_income(
        taxable_income, qualified_dividends, long_term_gain,
        tax_return.filing_status, rules, trace,
    )
    result.tax_before_credits = tax_before_credits

    # -- Step 9: other taxes ------------------------------------------------
    medicare_wages = sum((w.box5_medicare_wages for w in tax_return.income.w2s), ZERO)
    additional_medicare = se_mod.additional_medicare_tax(
        tax_return, medicare_wages, se_result.net_earnings, rules, trace
    )
    net_investment_income = (
        trace.amount("FED_TAXABLE_INTEREST")
        + trace.amount("FED_ORDINARY_DIVIDENDS")
        + clamp_non_negative(trace.amount("FED_CAPITAL_GAIN"))
    )
    niit = tax_mod.net_investment_income_tax(
        agi, net_investment_income, tax_return.filing_status, rules, trace
    )
    result.other_taxes = se_result.total + additional_medicare + niit

    # -- Step 10: credits ---------------------------------------------------
    credit_result = credits_mod.calculate_credits(
        tax_return, dependent_statuses, rules, agi, tax_before_credits,
        earned, _investment_income(tax_return, trace), trace,
    )
    result.nonrefundable_credits = credit_result.nonrefundable_total
    result.refundable_credits = credit_result.refundable_total
    result.credit_detail = credit_result.detail

    total_tax = clamp_non_negative(
        tax_before_credits - credit_result.nonrefundable_total
    ) + result.other_taxes
    result.total_tax = total_tax
    _step(
        trace, "FED_TOTAL_TAX", "Total tax", total_tax,
        form_line="Form 1040, line 24",
        inputs={
            "tax": tax_before_credits,
            "nonrefundable_credits": credit_result.nonrefundable_total,
            "other_taxes": result.other_taxes,
        },
    )

    # -- Step 11: payments, refund or balance due ---------------------------
    withholding = _federal_withholding(tax_return)
    estimated = (
        tax_return.payments.estimated_tax_payments_federal
        + tax_return.payments.prior_year_overpayment_applied_federal
        + tax_return.payments.extension_payment_federal
    )
    result.federal_withholding = withholding
    result.estimated_payments = estimated
    total_payments = withholding + estimated + credit_result.refundable_total
    result.total_payments = total_payments

    _step(
        trace, "FED_TOTAL_PAYMENTS", "Total payments", total_payments,
        form_line="Form 1040, line 33",
        inputs={
            "withholding": withholding,
            "estimated_payments": estimated,
            "refundable_credits": credit_result.refundable_total,
        },
        detail="Tax already paid on your behalf, plus refundable credits.",
    )

    net = total_payments - total_tax
    result.refund = to_whole_dollars(clamp_non_negative(net))
    result.amount_owed = to_whole_dollars(clamp_non_negative(-net))

    _step(
        trace,
        "FED_REFUND" if net >= 0 else "FED_AMOUNT_OWED",
        "Federal refund" if net >= 0 else "Federal amount you owe",
        result.refund if net >= 0 else result.amount_owed,
        form_line="Form 1040, line 34" if net >= 0 else "Form 1040, line 37",
        inputs={"total_payments": total_payments, "total_tax": total_tax},
        detail=(
            "You paid more than you owed, so this comes back to you."
            if net >= 0
            else "You owe this much more than you have already paid."
        ),
    )

    result.required_forms = _required_forms(tax_return, se_result, deduction.is_itemized)
    return result
