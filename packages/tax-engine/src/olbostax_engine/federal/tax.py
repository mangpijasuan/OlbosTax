"""Computing tax on taxable income -- Form 1040 line 16.

Two paths exist and picking the wrong one is a classic error:

*   No qualified dividends and no net long-term capital gain: run taxable
    income through the ordinary rate schedule.
*   Otherwise: the Qualified Dividends and Capital Gain Tax Worksheet, which
    carves the preferentially-taxed income out of taxable income, taxes the
    remainder at ordinary rates, and taxes the carved-out portion at the
    0%/15%/20% capital gains rates.

Implementing only the first path silently overtaxes anyone holding an index
fund that paid a qualified dividend -- which is most people with a brokerage
account.
"""

from __future__ import annotations

from decimal import Decimal

from olbostax_schema import FilingStatus, Jurisdiction, StepKind, TraceStep
from olbostax_schema.money import ZERO, clamp_non_negative
from olbostax_schema.trace import CalculationTrace

from ..calculations import apply_bracket_schedule, marginal_rate
from ..rules import RuleSet

__all__ = ["calculate_tax_on_taxable_income", "net_investment_income_tax"]


def calculate_tax_on_taxable_income(
    taxable_income: Decimal,
    qualified_dividends: Decimal,
    net_long_term_capital_gain: Decimal,
    filing_status: FilingStatus,
    rules: RuleSet,
    trace: CalculationTrace,
) -> Decimal:
    """Form 1040 line 16."""
    taxable_income = clamp_non_negative(taxable_income)
    ordinary_brackets = rules.brackets(f"ordinary_income_brackets.{filing_status.value}")

    preferential = clamp_non_negative(qualified_dividends + net_long_term_capital_gain)
    preferential = min(preferential, taxable_income)

    if preferential <= 0:
        tax = apply_bracket_schedule(taxable_income, ordinary_brackets)
        trace.add(
            TraceStep(
                code="FED_TAX_BEFORE_CREDITS",
                label="Tax",
                amount=tax,
                kind=StepKind.RESULT,
                jurisdiction=Jurisdiction.FEDERAL,
                form_line="Form 1040, line 16",
                inputs={"taxable_income": taxable_income},
                rule_citation=rules.citation("ordinary_income_brackets"),
                detail=(
                    f"Your taxable income of ${taxable_income:,.2f} taxed at the "
                    f"{filing_status.label.lower()} rates. Your top rate is "
                    f"{marginal_rate(taxable_income, ordinary_brackets):.0%}, but only the "
                    "part of your income in that bracket is taxed at it."
                ),
            )
        )
        return tax

    return _qualified_dividends_worksheet(
        taxable_income, preferential, filing_status, rules, trace
    )


def _qualified_dividends_worksheet(
    taxable_income: Decimal,
    preferential_income: Decimal,
    filing_status: FilingStatus,
    rules: RuleSet,
    trace: CalculationTrace,
) -> Decimal:
    """The Qualified Dividends and Capital Gain Tax Worksheet.

    Mechanically: ordinary income (taxable income less the preferential slice)
    is taxed at ordinary rates.  The preferential slice is then stacked *on top*
    of ordinary income and taxed through the capital gains schedule, so a
    taxpayer with low ordinary income gets the 0% rate on gains that fit below
    the first capital gains threshold, and only the part above it is taxed at
    15%.

    The stacking is the whole point.  Applying the capital gains rate to the
    gain in isolation gives the wrong answer for anyone near a threshold.
    """
    cg_brackets = rules.brackets(f"capital_gains_brackets.{filing_status.value}")
    ordinary_brackets = rules.brackets(f"ordinary_income_brackets.{filing_status.value}")

    ordinary_income = clamp_non_negative(taxable_income - preferential_income)
    tax_on_ordinary = apply_bracket_schedule(ordinary_income, ordinary_brackets)

    # Walk the capital gains schedule over the slice from ordinary_income to
    # taxable_income. Each bracket taxes only the part of the preferential
    # income that falls inside it.
    tax_on_preferential = ZERO
    position = ordinary_income
    remaining = preferential_income
    zero_rate_amount = ZERO

    for upper, rate in cg_brackets:
        if remaining <= 0:
            break
        room = upper - position
        if room <= 0:
            continue
        taxed_here = min(remaining, room)
        tax_on_preferential += taxed_here * rate
        if rate == 0:
            zero_rate_amount += taxed_here
        position += taxed_here
        remaining -= taxed_here

    total = tax_on_ordinary + tax_on_preferential

    trace.add(
        TraceStep(
            code="FED_TAX_ORDINARY_PORTION",
            label="Tax on income other than dividends and long-term gains",
            amount=tax_on_ordinary,
            kind=StepKind.SUBTOTAL,
            jurisdiction=Jurisdiction.FEDERAL,
            inputs={"ordinary_taxable_income": ordinary_income},
        )
    )
    trace.add(
        TraceStep(
            code="FED_TAX_PREFERENTIAL_PORTION",
            label="Tax on qualified dividends and long-term capital gains",
            amount=tax_on_preferential,
            kind=StepKind.SUBTOTAL,
            jurisdiction=Jurisdiction.FEDERAL,
            inputs={"preferential_income": preferential_income, "taxed_at_zero": zero_rate_amount},
            rule_citation="IRC s.1(h)",
            detail=(
                (
                    f"${zero_rate_amount:,.2f} of your dividends and long-term gains was "
                    "taxed at 0%. "
                    if zero_rate_amount > 0
                    else ""
                )
                + "Long-term gains and qualified dividends are taxed at lower rates than "
                "wages."
            ),
        )
    )
    trace.add(
        TraceStep(
            code="FED_TAX_BEFORE_CREDITS",
            label="Tax",
            amount=total,
            kind=StepKind.RESULT,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Form 1040, line 16",
            inputs={"ordinary": tax_on_ordinary, "capital_gains": tax_on_preferential},
            rule_citation="IRC s.1(h)",
            detail=(
                "We used the Qualified Dividends and Capital Gain Tax Worksheet, which "
                "taxes your investment income at lower rates than your other income."
            ),
        )
    )
    return total


def net_investment_income_tax(
    modified_agi: Decimal,
    net_investment_income: Decimal,
    filing_status: FilingStatus,
    rules: RuleSet,
    trace: CalculationTrace,
) -> Decimal:
    """Form 8960 -- 3.8% on the lesser of net investment income or MAGI excess.

    The "lesser of" is what keeps a taxpayer just over the threshold with a
    small amount of investment income from being taxed on all of it.
    """
    threshold = rules.decimal(f"net_investment_income_tax.threshold.{filing_status.value}")
    excess = clamp_non_negative(modified_agi - threshold)
    base = min(clamp_non_negative(net_investment_income), excess)
    if base <= 0:
        return ZERO

    rate = rules.decimal("net_investment_income_tax.rate")
    tax = base * rate
    trace.add(
        TraceStep(
            code="FED_NIIT",
            label="Net investment income tax",
            amount=tax,
            kind=StepKind.RESULT,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule 2, line 12",
            inputs={
                "net_investment_income": net_investment_income,
                "income_over_threshold": excess,
                "taxed_amount": base,
            },
            rule_citation="IRC s.1411",
            detail=(
                f"A 3.8% tax applies to investment income once total income passes "
                f"${threshold:,.0f}. It applies to the smaller of your investment income "
                "or the amount you are over the threshold."
            ),
        )
    )
    return tax
