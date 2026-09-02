"""Above-the-line adjustments -- Schedule 1, Part II.

Adjustments reduce total income to arrive at adjusted gross income.  AGI is
the single most consequential number on a federal return: it drives phase-outs
for nearly every credit, it is where Oklahoma's return begins, and it
determines the medical expense floor.  Getting it right matters more than
getting any individual credit right.
"""

from __future__ import annotations

from decimal import Decimal

from olbostax_schema import Jurisdiction, StepKind, TaxReturnInput, TraceStep
from olbostax_schema.money import ZERO
from olbostax_schema.trace import CalculationTrace

from ..calculations import phase_out_ratably
from ..rules import RuleSet

__all__ = ["total_adjustments", "adjustments_before_se_tax"]


def _fed(code: str, label: str, amount: Decimal, **kw: object) -> TraceStep:
    return TraceStep(
        code=code, label=label, amount=amount, jurisdiction=Jurisdiction.FEDERAL, **kw  # type: ignore[arg-type]
    )


def _educator_expenses(
    tax_return: TaxReturnInput, rules: RuleSet, trace: CalculationTrace
) -> Decimal:
    claimed = tax_return.adjustments.educator_expenses
    if claimed <= 0:
        return ZERO
    per_person = rules.decimal("adjustments.educator_expense_limit")
    # A joint return where both spouses are educators may deduct twice the
    # per-person limit. OlbosTax does not ask which spouse incurred which
    # expense, so the joint cap is applied and the interview explains it.
    cap = per_person * 2 if tax_return.filing_status.is_joint else per_person
    allowed = min(claimed, cap)
    if allowed < claimed:
        trace.add(
            _fed(
                "FED_EDUCATOR_LIMIT",
                "Educator expenses limited",
                allowed,
                kind=StepKind.LIMITATION,
                inputs={"claimed": claimed, "limit": cap},
                rule_citation="IRC s.62(a)(2)(D)",
                detail=(
                    f"Educator expenses are capped at ${cap:,.0f}. You entered "
                    f"${claimed:,.2f}."
                ),
            )
        )
    return allowed


def _student_loan_interest(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    modified_agi: Decimal,
    trace: CalculationTrace,
) -> Decimal:
    """IRC s.221 -- capped, then phased out over a MAGI range.

    Married-filing-separately taxpayers are barred entirely, which is a common
    and expensive surprise; the trace says so explicitly rather than silently
    returning zero.
    """
    paid = tax_return.adjustments.student_loan_interest_paid
    if paid <= 0:
        return ZERO

    status = tax_return.filing_status.value
    if status == "MARRIED_FILING_SEPARATELY" and rules.boolean(
        "adjustments.student_loan_interest_disallowed_for_mfs"
    ):
        trace.add(
            _fed(
                "FED_STUDENT_LOAN_MFS",
                "Student loan interest not allowed",
                ZERO,
                kind=StepKind.LIMITATION,
                rule_citation="IRC s.221(e)(2)",
                detail=(
                    "The student loan interest deduction is not available on a married "
                    "filing separately return."
                ),
            )
        )
        return ZERO

    cap = rules.decimal("adjustments.student_loan_interest_limit")
    capped = min(paid, cap)
    start = rules.decimal(f"adjustments.student_loan_interest_phaseout.{status}.start")
    end = rules.decimal(f"adjustments.student_loan_interest_phaseout.{status}.end")
    allowed = phase_out_ratably(capped, modified_agi, start, end)

    if allowed < capped:
        trace.add(
            _fed(
                "FED_STUDENT_LOAN_PHASEOUT",
                "Student loan interest reduced by income",
                allowed,
                kind=StepKind.LIMITATION,
                inputs={"before_phaseout": capped, "modified_agi": modified_agi},
                rule_citation="IRC s.221(b)(2)",
                detail=(
                    f"This deduction phases out between ${start:,.0f} and ${end:,.0f} of "
                    "income."
                ),
            )
        )
    return allowed


def _alimony_paid(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """IRC s.215, as repealed by TCJA s.11051 for post-2018 agreements.

    Alimony under an agreement executed after 31 December 2018 is neither
    deductible by the payer nor taxable to the recipient.  The engine needs the
    agreement date to apply this, and refuses to deduct when the date is
    missing rather than defaulting to the taxpayer-favourable answer.
    """
    paid = tax_return.adjustments.alimony_paid
    if paid <= 0:
        return ZERO
    agreement_date = tax_return.adjustments.alimony_paid_agreement_date
    if agreement_date is None or agreement_date.year > 2018:
        trace.add(
            _fed(
                "FED_ALIMONY_NOT_DEDUCTIBLE",
                "Alimony paid not deductible",
                ZERO,
                kind=StepKind.LIMITATION,
                rule_citation="TCJA s.11051",
                detail=(
                    "Alimony under agreements executed after 2018 is not deductible."
                    if agreement_date
                    else "We need the date of your divorce or separation agreement to "
                    "know whether this alimony is deductible."
                ),
            )
        )
        return ZERO
    return paid


def adjustments_before_se_tax(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    provisional_agi: Decimal,
    trace: CalculationTrace,
) -> Decimal:
    """Every adjustment that does not depend on self-employment tax.

    Split out because the deductible half of SE tax is itself an adjustment,
    and SE tax depends on net earnings, which depend on income -- so the
    calculation order is income, then these, then SE tax, then the total.
    """
    adj = tax_return.adjustments
    total = (
        _educator_expenses(tax_return, rules, trace)
        + adj.hsa_deduction
        + adj.self_employed_health_insurance
        + adj.ira_deduction
        + _student_loan_interest(tax_return, rules, provisional_agi, trace)
        + _alimony_paid(tax_return, trace)
    )
    return total


def total_adjustments(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    provisional_agi: Decimal,
    deductible_se_tax: Decimal,
    trace: CalculationTrace,
) -> Decimal:
    """Form 1040 line 10 -- total adjustments to income."""
    base = adjustments_before_se_tax(tax_return, rules, provisional_agi, trace)
    total = base + deductible_se_tax
    trace.add(
        _fed(
            "FED_TOTAL_ADJUSTMENTS",
            "Adjustments to income",
            total,
            kind=StepKind.RESULT,
            form_line="Form 1040, line 10",
            inputs={"other_adjustments": base, "deductible_self_employment_tax": deductible_se_tax},
            detail="Deductions you get whether or not you itemize.",
        )
    )
    return total
