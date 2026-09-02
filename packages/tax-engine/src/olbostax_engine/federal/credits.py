"""Federal credits -- Schedule 3, Schedule 8812, Schedule EIC.

Credits divide into two kinds and the distinction is the whole ballgame:

*   **Nonrefundable** credits can reduce tax to zero and no further.  Any
    excess is lost (or, for some, carried forward).
*   **Refundable** credits are paid out even when no tax is owed.  The EITC and
    the Additional Child Tax Credit are how a low-income working family gets a
    refund larger than everything they had withheld.

Ordering matters.  Nonrefundable credits are applied first against tax; only
then is the refundable portion of the Child Tax Credit computed, because that
portion is defined as the part of the credit that the taxpayer's tax liability
could not absorb.  Getting the order wrong understates refunds for exactly the
taxpayers who can least afford it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from olbostax_schema import Jurisdiction, StepKind, TaxReturnInput, TraceStep
from olbostax_schema.money import ZERO, clamp_non_negative
from olbostax_schema.trace import CalculationTrace

from ..calculations import phase_out_by_increment, phase_out_ratably, rate_for_amount
from ..rules import RuleSet
from .dependents import DependentStatus

__all__ = ["CreditResult", "calculate_credits"]


@dataclass(slots=True)
class CreditResult:
    nonrefundable_total: Decimal = ZERO
    refundable_total: Decimal = ZERO
    detail: dict[str, Decimal] = field(default_factory=dict)


def _trace(
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
            jurisdiction=Jurisdiction.FEDERAL,
            form_line=form_line,
            inputs=inputs or {},
            rule_citation=citation,
            detail=detail,
        )
    )


# --------------------------------------------------------------------------
# Child Tax Credit and Credit for Other Dependents (Schedule 8812)
# --------------------------------------------------------------------------


def _child_tax_credit(
    tax_return: TaxReturnInput,
    statuses: list[DependentStatus],
    rules: RuleSet,
    agi: Decimal,
    tax_after_other_nonrefundable: Decimal,
    earned_income: Decimal,
    trace: CalculationTrace,
) -> tuple[Decimal, Decimal]:
    """Returns ``(nonrefundable_ctc, additional_ctc_refundable)``."""
    ctc_children = [s for s in statuses if s.qualifies_for_child_tax_credit]
    odc_dependents = [s for s in statuses if s.qualifies_for_other_dependent_credit]
    if not ctc_children and not odc_dependents:
        return ZERO, ZERO

    per_child = rules.decimal("child_tax_credit.amount_per_child")
    per_other = rules.decimal("other_dependent_credit.amount_per_dependent")
    status = tax_return.filing_status.value
    threshold = rules.decimal(f"child_tax_credit.phaseout_threshold.{status}")
    increment = rules.decimal("child_tax_credit.phaseout_increment")
    reduction = rules.decimal("child_tax_credit.phaseout_reduction_per_increment")

    gross = per_child * len(ctc_children) + per_other * len(odc_dependents)
    after_phaseout = phase_out_by_increment(gross, agi, threshold, increment, reduction)

    if after_phaseout < gross:
        _trace(
            trace,
            "FED_CTC_PHASEOUT",
            "Child Tax Credit reduced by income",
            after_phaseout,
            kind=StepKind.LIMITATION,
            inputs={"before_phaseout": gross, "adjusted_gross_income": agi, "threshold": threshold},
            citation="IRC s.24(b)",
            detail=(
                f"The Child Tax Credit is reduced by ${reduction:,.0f} for every "
                f"${increment:,.0f} your income exceeds ${threshold:,.0f}."
            ),
        )

    if after_phaseout <= 0:
        return ZERO, ZERO

    # The nonrefundable part is limited by remaining tax liability.
    nonrefundable = min(after_phaseout, clamp_non_negative(tax_after_other_nonrefundable))
    _trace(
        trace,
        "FED_CTC",
        "Child Tax Credit and Credit for Other Dependents",
        nonrefundable,
        form_line="Form 1040, line 19",
        inputs={
            "qualifying_children": Decimal(len(ctc_children)),
            "other_dependents": Decimal(len(odc_dependents)),
            "credit_available": after_phaseout,
        },
        citation=rules.citation("child_tax_credit"),
        detail=(
            f"${per_child:,.0f} for each of your {len(ctc_children)} qualifying "
            f"child(ren)"
            + (f" and ${per_other:,.0f} for {len(odc_dependents)} other dependent(s)"
               if odc_dependents else "")
            + "."
        ),
    )

    # Additional Child Tax Credit: the refundable remainder, limited both by the
    # unused credit and by a percentage of earned income above a floor. Only
    # the child portion is refundable; the other-dependent credit never is.
    unused = clamp_non_negative(after_phaseout - nonrefundable)
    child_share_of_unused = min(
        unused, clamp_non_negative(per_child * len(ctc_children) - nonrefundable)
    )
    if child_share_of_unused <= 0 or not ctc_children:
        return nonrefundable, ZERO

    floor = rules.decimal("child_tax_credit.additional_ctc_earned_income_floor")
    rate = rules.decimal("child_tax_credit.additional_ctc_earned_income_rate")
    per_child_refund_cap = rules.decimal("child_tax_credit.refundable_limit_per_child")

    earned_income_limit = clamp_non_negative(earned_income - floor) * rate
    statutory_cap = per_child_refund_cap * len(ctc_children)
    refundable = min(child_share_of_unused, earned_income_limit, statutory_cap)

    _trace(
        trace,
        "FED_ADDITIONAL_CTC",
        "Additional Child Tax Credit (refundable)",
        refundable,
        form_line="Form 1040, line 28",
        inputs={
            "unused_credit": child_share_of_unused,
            "earned_income_limit": earned_income_limit,
            "statutory_cap": statutory_cap,
        },
        citation="IRC s.24(h)(5)",
        detail=(
            "Part of the Child Tax Credit you could not use against your tax is paid to "
            f"you as a refund, limited to {rate:.0%} of the earned income above "
            f"${floor:,.0f} and to ${per_child_refund_cap:,.0f} per child."
        ),
    )
    return nonrefundable, refundable


# --------------------------------------------------------------------------
# Earned Income Credit (IRC s.32)
# --------------------------------------------------------------------------


def _earned_income_credit(
    tax_return: TaxReturnInput,
    statuses: list[DependentStatus],
    rules: RuleSet,
    agi: Decimal,
    earned_income: Decimal,
    investment_income: Decimal,
    trace: CalculationTrace,
) -> Decimal:
    """The EITC: a trapezoid -- phase in, plateau, phase out.

    The credit is the *lesser* of the amount computed on earned income and the
    amount computed on AGI, which matters for a taxpayer whose AGI exceeds
    their earned income (say, wages plus interest).
    """
    if tax_return.filing_status.value == "MARRIED_FILING_SEPARATELY":
        # s.32(d) bars MFS except in narrow separated-spouse circumstances that
        # OlbosTax does not collect facts for. Not claiming it is the safe
        # direction of error; the interview flags it.
        return ZERO

    limit = rules.decimal("earned_income_credit.investment_income_limit")
    if investment_income > limit:
        _trace(
            trace,
            "FED_EITC_INVESTMENT_LIMIT",
            "Earned Income Credit not allowed",
            ZERO,
            kind=StepKind.LIMITATION,
            inputs={"investment_income": investment_income, "limit": limit},
            citation="IRC s.32(i)",
            detail=(
                f"The Earned Income Credit is not available when investment income "
                f"exceeds ${limit:,.0f}."
            ),
        )
        return ZERO

    child_count = min(sum(1 for s in statuses if s.qualifies_for_eitc), 3)

    if child_count == 0:
        min_age = rules.integer("earned_income_credit.childless_min_age")
        max_age = rules.integer("earned_income_credit.childless_max_age")
        age = tax_return.taxpayer.age_on(tax_return.year_end)
        spouse_age = (
            tax_return.spouse.age_on(tax_return.year_end) if tax_return.spouse else None
        )
        eligible_age = min_age <= age <= max_age or (
            spouse_age is not None and min_age <= spouse_age <= max_age
        )
        if not eligible_age:
            return ZERO

    tiers = rules.get("earned_income_credit.tiers")
    tier = next(t for t in tiers if int(t["children"]) == child_count)

    # The tier's "earned income amount" -- the income at which the credit
    # reaches its maximum -- is not read here: capping the phase-in at
    # max_credit produces the same result and uses the published maximum as
    # authoritative rather than deriving it. That the two agree is a property
    # of the rule file, checked in tests/tax_engine/test_rule_files.py.
    max_credit = tier["max_credit"]
    phase_in = tier["phase_in_rate"]
    phase_out_rate = tier["phase_out_rate"]
    threshold = (
        tier["phase_out_threshold_joint"]
        if tax_return.filing_status.is_joint
        else tier["phase_out_threshold_unmarried"]
    )

    def credit_for(income: Decimal) -> Decimal:
        phased_in = min(income * phase_in, max_credit)
        excess = clamp_non_negative(income - threshold)
        return clamp_non_negative(phased_in - excess * phase_out_rate)

    credit = min(credit_for(earned_income), credit_for(agi))
    if credit <= 0:
        return ZERO

    _trace(
        trace,
        "FED_EITC",
        "Earned Income Credit (refundable)",
        credit,
        form_line="Form 1040, line 27",
        inputs={
            "earned_income": earned_income,
            "adjusted_gross_income": agi,
            "qualifying_children": Decimal(child_count),
            "maximum_credit": max_credit,
        },
        citation=rules.citation("earned_income_credit"),
        detail=(
            "The Earned Income Credit rewards work. It grows with your earnings up to a "
            f"maximum of ${max_credit:,.0f} for your situation, then decreases as income "
            f"rises above ${threshold:,.0f}."
        ),
    )
    return credit


# --------------------------------------------------------------------------
# Child and Dependent Care Credit (IRC s.21)
# --------------------------------------------------------------------------


def _child_care_credit(
    tax_return: TaxReturnInput,
    statuses: list[DependentStatus],
    rules: RuleSet,
    agi: Decimal,
    earned_income: Decimal,
    trace: CalculationTrace,
) -> Decimal:
    expenses = tax_return.credits.child_care_expenses
    if expenses <= 0:
        return ZERO

    qualifying = sum(1 for s in statuses if s.qualifies_for_child_care_credit)
    if qualifying == 0:
        return ZERO

    limit = rules.decimal(
        "child_and_dependent_care_credit.expense_limit_multiple_persons"
        if qualifying > 1
        else "child_and_dependent_care_credit.expense_limit_one_person"
    )
    # s.21(d): qualifying expenses cannot exceed earned income. On a joint
    # return the lower-earning spouse's income is the ceiling, which OlbosTax
    # does not separately collect; the single combined figure is used and the
    # interview warns joint filers about the rule.
    qualified = min(expenses, limit, clamp_non_negative(earned_income))

    max_rate = rules.decimal("child_and_dependent_care_credit.max_rate")
    min_rate = rules.decimal("child_and_dependent_care_credit.min_rate")
    start = rules.decimal("child_and_dependent_care_credit.rate_phasedown_agi_start")
    increment = rules.decimal("child_and_dependent_care_credit.rate_phasedown_increment")
    step = rules.decimal("child_and_dependent_care_credit.rate_phasedown_step")

    steps = clamp_non_negative(agi - start) / increment
    rate = max(min_rate, max_rate - steps.to_integral_value() * step)
    credit = qualified * rate

    _trace(
        trace,
        "FED_CHILD_CARE_CREDIT",
        "Child and Dependent Care Credit",
        credit,
        form_line="Schedule 3, line 2",
        inputs={"qualified_expenses": qualified, "rate": rate},
        citation="IRC s.21",
        detail=(
            f"{rate:.0%} of up to ${limit:,.0f} in care expenses for "
            f"{qualifying} qualifying person(s). This credit is not refundable, so it "
            "can only reduce tax you owe."
        ),
    )
    return credit


# --------------------------------------------------------------------------
# Education credits (IRC s.25A)
# --------------------------------------------------------------------------


def _education_credits(
    tax_return: TaxReturnInput, rules: RuleSet, agi: Decimal, trace: CalculationTrace
) -> tuple[Decimal, Decimal]:
    """Returns ``(nonrefundable, refundable)``.

    AOTC and LLC cannot both be claimed for the same student.  OlbosTax
    collects the expenses separately and takes the taxpayer's allocation at
    face value rather than optimising across students, which would require
    per-student data the MVP does not gather.
    """
    status = tax_return.filing_status.value
    nonrefundable = ZERO
    refundable = ZERO

    aotc_expenses = tax_return.credits.education_expenses_aotc
    if aotc_expenses > 0:
        if status == "MARRIED_FILING_SEPARATELY" and rules.boolean(
            "education_credits.aotc.disallowed_for_mfs"
        ):
            _trace(
                trace, "FED_AOTC_MFS", "Education credit not allowed", ZERO,
                kind=StepKind.LIMITATION, citation="IRC s.25A(g)(6)",
                detail="Education credits are not available on a married filing separately return.",
            )
        else:
            full_expenses = rules.decimal("education_credits.aotc.full_rate_expenses")
            partial_expenses = rules.decimal("education_credits.aotc.partial_rate_expenses")
            partial_rate = rules.decimal("education_credits.aotc.partial_rate")
            gross = min(aotc_expenses, full_expenses) + min(
                clamp_non_negative(aotc_expenses - full_expenses), partial_expenses
            ) * partial_rate
            gross = min(gross, rules.decimal("education_credits.aotc.max_credit"))

            start = rules.decimal(f"education_credits.aotc.phaseout.{status}.start")
            end = rules.decimal(f"education_credits.aotc.phaseout.{status}.end")
            allowed = phase_out_ratably(gross, agi, start, end)

            share = rules.decimal("education_credits.aotc.refundable_share")
            cap = rules.decimal("education_credits.aotc.refundable_cap")
            aotc_refundable = min(allowed * share, cap)
            aotc_nonrefundable = allowed - aotc_refundable

            nonrefundable += aotc_nonrefundable
            refundable += aotc_refundable
            _trace(
                trace, "FED_AOTC", "American Opportunity Credit", allowed,
                form_line="Form 1040, lines 29 and Schedule 3 line 3",
                inputs={"qualified_expenses": aotc_expenses, "refundable_part": aotc_refundable},
                citation="IRC s.25A(i)",
                detail=(
                    f"Up to ${cap:,.0f} of this credit is refundable, meaning you can "
                    "receive it even if you owe no tax."
                ),
            )

    llc_expenses = tax_return.credits.education_expenses_llc
    if llc_expenses > 0 and not (
        status == "MARRIED_FILING_SEPARATELY"
        and rules.boolean("education_credits.llc.disallowed_for_mfs")
    ):
        rate = rules.decimal("education_credits.llc.rate")
        limit = rules.decimal("education_credits.llc.expense_limit")
        gross = min(llc_expenses, limit) * rate
        start = rules.decimal(f"education_credits.llc.phaseout.{status}.start")
        end = rules.decimal(f"education_credits.llc.phaseout.{status}.end")
        allowed = phase_out_ratably(gross, agi, start, end)
        nonrefundable += allowed
        _trace(
            trace, "FED_LLC", "Lifetime Learning Credit", allowed,
            form_line="Schedule 3, line 3",
            inputs={"qualified_expenses": llc_expenses},
            citation="IRC s.25A(c)",
            detail=f"{rate:.0%} of up to ${limit:,.0f} in qualified education expenses.",
        )

    return nonrefundable, refundable


def _savers_credit(
    tax_return: TaxReturnInput, rules: RuleSet, agi: Decimal, trace: CalculationTrace
) -> Decimal:
    contributions = tax_return.credits.retirement_savings_contributions
    if contributions <= 0:
        return ZERO
    if tax_return.taxpayer.can_be_claimed_as_dependent:
        return ZERO

    limit = rules.decimal("savers_credit.contribution_limit")
    status = tax_return.filing_status.value
    tiers = rules.brackets(f"savers_credit.tiers.{status}")
    rate = rate_for_amount(agi, tiers)
    if rate == 0:
        return ZERO

    # On a joint return each spouse has their own contribution limit.
    effective_limit = limit * 2 if tax_return.filing_status.is_joint else limit
    credit = min(contributions, effective_limit) * rate

    _trace(
        trace, "FED_SAVERS_CREDIT", "Retirement Savings Contributions Credit", credit,
        form_line="Schedule 3, line 4",
        inputs={"contributions": contributions, "rate": rate},
        citation="IRC s.25B",
        detail=(
            f"A credit of {rate:.0%} of your retirement contributions, up to "
            f"${effective_limit:,.0f} of contributions."
        ),
    )
    return credit


# --------------------------------------------------------------------------


def calculate_credits(
    tax_return: TaxReturnInput,
    statuses: list[DependentStatus],
    rules: RuleSet,
    agi: Decimal,
    tax_before_credits: Decimal,
    earned_income: Decimal,
    investment_income: Decimal,
    trace: CalculationTrace,
) -> CreditResult:
    """Apply every supported credit in statutory order."""
    result = CreditResult()

    care = _child_care_credit(tax_return, statuses, rules, agi, earned_income, trace)
    edu_nonrefundable, edu_refundable = _education_credits(tax_return, rules, agi, trace)
    savers = _savers_credit(tax_return, rules, agi, trace)

    # Nonrefundable credits are applied one at a time against the tax that
    # remains, in Schedule 3 order. Applying them as a single capped sum would
    # produce the right total but the wrong per-credit figures, and those
    # figures are what the taxpayer sees in the review screen and what the
    # Oklahoma return reads to compute its own credits. A taxpayer told they
    # received a $1,000 Saver's Credit that was actually worth nothing to them
    # has been misinformed, even if their refund is correct.
    remaining_tax = clamp_non_negative(tax_before_credits)
    allowed: dict[str, Decimal] = {}
    for key, amount in (
        ("child_and_dependent_care", care),
        ("education_nonrefundable", edu_nonrefundable),
        ("savers", savers),
    ):
        applied = min(amount, remaining_tax)
        allowed[key] = applied
        remaining_tax -= applied

    care = allowed["child_and_dependent_care"]
    edu_nonrefundable = allowed["education_nonrefundable"]
    savers = allowed["savers"]
    other_nonrefundable = care + edu_nonrefundable + savers

    ctc_nonrefundable, ctc_refundable = _child_tax_credit(
        tax_return, statuses, rules, agi, remaining_tax, earned_income, trace
    )

    eitc = _earned_income_credit(
        tax_return, statuses, rules, agi, earned_income, investment_income, trace
    )

    result.nonrefundable_total = other_nonrefundable + ctc_nonrefundable
    result.refundable_total = ctc_refundable + edu_refundable + eitc
    result.detail = {
        "child_and_dependent_care": care,
        "education_nonrefundable": edu_nonrefundable,
        "education_refundable": edu_refundable,
        "savers": savers,
        "child_tax_credit": ctc_nonrefundable,
        "additional_child_tax_credit": ctc_refundable,
        "earned_income_credit": eitc,
    }

    _trace(
        trace, "FED_NONREFUNDABLE_CREDITS", "Nonrefundable credits",
        result.nonrefundable_total, kind=StepKind.SUBTOTAL, form_line="Form 1040, line 21",
    )
    _trace(
        trace, "FED_REFUNDABLE_CREDITS", "Refundable credits",
        result.refundable_total, kind=StepKind.SUBTOTAL, form_line="Form 1040, line 32",
        detail="These credits are paid to you even if you owe no tax.",
    )
    return result
