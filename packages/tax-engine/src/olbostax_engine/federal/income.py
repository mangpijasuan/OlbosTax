"""Federal income: Form 1040 lines 1 through 9, plus Schedule 1 Part I.

Each function takes the return and the rule set and returns a Decimal, adding
trace steps as it goes.  They are pure: the same inputs and the same rule
version always produce the same output, which is what makes stored
computations reproducible and regression testing meaningful.
"""

from __future__ import annotations

from decimal import Decimal

from olbostax_schema import Jurisdiction, StepKind, TaxReturnInput, TraceStep
from olbostax_schema.money import ZERO, clamp_non_negative
from olbostax_schema.trace import CalculationTrace

from ..rules import RuleSet

__all__ = [
    "wages",
    "taxable_interest",
    "tax_exempt_interest",
    "ordinary_dividends",
    "qualified_dividends",
    "self_employment_net_profit",
    "retirement_distributions",
    "unemployment_compensation",
    "capital_gain_or_loss",
    "taxable_social_security",
    "total_income",
    "earned_income",
]


def _step(
    trace: CalculationTrace,
    code: str,
    label: str,
    amount: Decimal,
    *,
    kind: StepKind = StepKind.SUBTOTAL,
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


def wages(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Form 1040 line 1a -- total W-2 Box 1 wages."""
    total = sum((w.box1_wages for w in tax_return.income.w2s), ZERO)
    return _step(
        trace,
        "FED_WAGES",
        "Wages, salaries and tips",
        total,
        form_line="Form 1040, line 1a",
        inputs={f"w2_{i + 1}": w.box1_wages for i, w in enumerate(tax_return.income.w2s)},
        detail=(
            f"Box 1 of your {len(tax_return.income.w2s)} W-2 form(s), added together."
            if tax_return.income.w2s
            else "You did not report any W-2 wages."
        ),
    )


def taxable_interest(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Form 1040 line 2b.

    Box 3 (US savings bond and Treasury interest) is federally taxable and is
    included here; it is separately identified because Oklahoma subtracts it.
    """
    income = tax_return.income
    from_1099 = sum(
        (f.box1_interest_income + f.box3_interest_on_us_savings_bonds
         for f in income.form_1099_ints),
        ZERO,
    )
    total = from_1099 + income.other_interest_income
    return _step(
        trace,
        "FED_TAXABLE_INTEREST",
        "Taxable interest",
        total,
        form_line="Form 1040, line 2b",
        inputs={"from_1099_int": from_1099, "other": income.other_interest_income},
    )


def tax_exempt_interest(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Form 1040 line 2a.

    Not taxable, but reported, and it counts toward modified AGI in the Social
    Security taxability calculation -- which is why it is computed rather than
    ignored.
    """
    income = tax_return.income
    total = sum((f.box8_tax_exempt_interest for f in income.form_1099_ints), ZERO) + sum(
        (f.box12_exempt_interest_dividends for f in income.form_1099_divs), ZERO
    )
    return _step(
        trace,
        "FED_TAX_EXEMPT_INTEREST",
        "Tax-exempt interest",
        total,
        kind=StepKind.INPUT,
        form_line="Form 1040, line 2a",
    )


def ordinary_dividends(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Form 1040 line 3b."""
    total = sum(
        (f.box1a_total_ordinary_dividends for f in tax_return.income.form_1099_divs), ZERO
    )
    return _step(
        trace,
        "FED_ORDINARY_DIVIDENDS",
        "Ordinary dividends",
        total,
        form_line="Form 1040, line 3b",
    )


def qualified_dividends(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Form 1040 line 3a -- taxed at capital gains rates, not ordinary rates."""
    total = sum((f.box1b_qualified_dividends for f in tax_return.income.form_1099_divs), ZERO)
    return _step(
        trace,
        "FED_QUALIFIED_DIVIDENDS",
        "Qualified dividends",
        total,
        kind=StepKind.INPUT,
        form_line="Form 1040, line 3a",
        detail="Qualified dividends are taxed at long-term capital gains rates.",
    )


def self_employment_net_profit(
    tax_return: TaxReturnInput, trace: CalculationTrace
) -> Decimal:
    """Schedule C net profit -- 1099-NEC and 1099-MISC income less expenses.

    Reported as a single trade or business.  A taxpayer with two genuinely
    separate businesses needs two Schedule Cs, which the MVP does not support;
    the capability matrix flags it.
    """
    income = tax_return.income
    gross = sum((f.box1_nonemployee_compensation for f in income.form_1099_necs), ZERO)
    gross += sum(
        (f.box3_other_income + f.box2_royalties for f in income.form_1099_miscs), ZERO
    )
    net = gross - income.self_employment_expenses
    return _step(
        trace,
        "FED_SE_NET_PROFIT",
        "Business income or loss",
        net,
        form_line="Schedule 1, line 3",
        inputs={"gross_receipts": gross, "expenses": income.self_employment_expenses},
        detail="Your self-employment income minus the business expenses you entered.",
    )


def retirement_distributions(
    tax_return: TaxReturnInput, trace: CalculationTrace
) -> Decimal:
    """Form 1040 lines 4b and 5b -- the taxable part of retirement income.

    Box 2a is used as the taxable amount.  When the payer checked "taxable
    amount not determined", the taxable portion depends on basis the payer does
    not know, so the engine does not guess: capability.py raises a finding and
    the return is not filable until a human resolves it.
    """
    total = sum((f.box2a_taxable_amount for f in tax_return.income.form_1099_rs), ZERO)
    return _step(
        trace,
        "FED_RETIREMENT_TAXABLE",
        "Taxable pension, annuity and IRA distributions",
        total,
        form_line="Form 1040, lines 4b and 5b",
    )


def unemployment_compensation(
    tax_return: TaxReturnInput, trace: CalculationTrace
) -> Decimal:
    """Schedule 1 line 7 -- fully taxable at the federal level."""
    total = sum(
        (f.box1_unemployment_compensation for f in tax_return.income.form_1099_gs), ZERO
    )
    return _step(
        trace,
        "FED_UNEMPLOYMENT",
        "Unemployment compensation",
        total,
        form_line="Schedule 1, line 7",
    )


def capital_gain_or_loss(
    tax_return: TaxReturnInput, rules: RuleSet, trace: CalculationTrace
) -> tuple[Decimal, Decimal]:
    """Form 1040 line 7. Returns ``(reportable_amount, net_long_term_gain)``.

    A net capital *loss* is deductible against ordinary income only up to the
    annual limit of IRC s.1211(b); the excess carries forward.  The engine
    applies the limit and records it as a limitation step so the taxpayer can
    see why their $9,000 loss produced a $3,000 deduction.
    """
    income = tax_return.income
    short_term = income.net_short_term_capital_gain_loss
    long_term = income.net_long_term_capital_gain_loss

    # Capital gain distributions from 1099-DIV box 2a are long-term by statute.
    distributions = sum(
        (f.box2a_total_capital_gain_distributions for f in income.form_1099_divs), ZERO
    )
    long_term += distributions
    net = short_term + long_term

    if net >= 0:
        _step(
            trace,
            "FED_CAPITAL_GAIN",
            "Capital gain",
            net,
            form_line="Form 1040, line 7",
            inputs={"short_term": short_term, "long_term": long_term},
        )
        return net, clamp_non_negative(long_term)

    limit_path = (
        "capital_loss.annual_deduction_limit_mfs"
        if tax_return.filing_status.value == "MARRIED_FILING_SEPARATELY"
        else "capital_loss.annual_deduction_limit"
    )
    limit = rules.decimal(limit_path)
    allowed = -min(-net, limit)
    if allowed != net:
        _step(
            trace,
            "FED_CAPITAL_LOSS_LIMIT",
            "Capital loss limited",
            allowed,
            kind=StepKind.LIMITATION,
            form_line="Form 1040, line 7",
            inputs={"net_loss": net, "annual_limit": limit},
            citation="IRC s.1211(b)",
            detail=(
                f"Your net capital loss was ${-net:,.2f}, but only ${limit:,.2f} can be "
                "deducted this year. The rest carries forward to future years."
            ),
        )
    else:
        _step(
            trace,
            "FED_CAPITAL_GAIN",
            "Capital loss",
            allowed,
            form_line="Form 1040, line 7",
        )
    return allowed, ZERO


def taxable_social_security(
    tax_return: TaxReturnInput,
    other_income: Decimal,
    adjustments: Decimal,
    tax_exempt: Decimal,
    rules: RuleSet,
    trace: CalculationTrace,
) -> Decimal:
    """Form 1040 line 6b -- the taxable portion of Social Security under IRC s.86.

    The statute is a two-tier formula, not a percentage:

    *   Provisional income = other income + tax-exempt interest + half the
        benefits, less above-the-line adjustments.
    *   Below the base amount, none of the benefits are taxable.
    *   Between the base and adjusted base, 50% of the excess is taxable.
    *   Above the adjusted base, 85% of the further excess is taxable, plus the
        lesser of the first-tier amount or a statutory cap.
    *   The total is capped at 85% of benefits received.

    Married-filing-separately taxpayers who lived with their spouse have base
    amounts of zero, which is why the rule file carries an explicit ``0``
    rather than omitting the status.
    """
    benefits = tax_return.income.social_security_benefits_received
    if benefits <= 0:
        return ZERO

    status = tax_return.filing_status.value
    base = rules.decimal(f"social_security_taxability.base_amount.{status}")
    adjusted_base = rules.decimal(f"social_security_taxability.adjusted_base_amount.{status}")
    first_rate = rules.decimal("social_security_taxability.first_tier_rate")
    second_rate = rules.decimal("social_security_taxability.second_tier_rate")

    provisional = other_income + tax_exempt + (benefits * first_rate) - adjustments
    _step(
        trace,
        "FED_SS_PROVISIONAL_INCOME",
        "Provisional income for Social Security",
        provisional,
        kind=StepKind.COMPUTED,
        inputs={
            "other_income": other_income,
            "tax_exempt_interest": tax_exempt,
            "half_of_benefits": benefits * first_rate,
            "adjustments": adjustments,
        },
        citation="IRC s.86(b)(2)",
    )

    if provisional <= base:
        return _step(
            trace,
            "FED_SS_TAXABLE",
            "Taxable Social Security benefits",
            ZERO,
            form_line="Form 1040, line 6b",
            citation="IRC s.86(a)",
            detail=(
                "None of your Social Security benefits are taxable because your other "
                f"income is below ${base:,.0f}."
            ),
        )

    if provisional <= adjusted_base:
        taxable = min(first_rate * (provisional - base), first_rate * benefits)
    else:
        first_tier = min(first_rate * (adjusted_base - base), first_rate * benefits)
        taxable = second_rate * (provisional - adjusted_base) + first_tier

    taxable = min(taxable, second_rate * benefits)
    return _step(
        trace,
        "FED_SS_TAXABLE",
        "Taxable Social Security benefits",
        taxable,
        form_line="Form 1040, line 6b",
        inputs={"benefits_received": benefits, "provisional_income": provisional},
        citation="IRC s.86(a)",
        detail=(
            f"${taxable:,.2f} of your ${benefits:,.2f} in benefits is taxable. At most "
            "85% of Social Security benefits can ever be taxed."
        ),
    )


def earned_income(tax_return: TaxReturnInput, trace: CalculationTrace) -> Decimal:
    """Earned income for the EITC and the refundable Child Tax Credit.

    Wages plus net self-employment earnings.  Investment, retirement and
    unemployment income are not earned income, which is why this cannot simply
    reuse total income.
    """
    wage_total = sum((w.box1_wages for w in tax_return.income.w2s), ZERO)
    se_net = clamp_non_negative(
        sum((f.box1_nonemployee_compensation for f in tax_return.income.form_1099_necs), ZERO)
        + sum((f.box3_other_income for f in tax_return.income.form_1099_miscs), ZERO)
        - tax_return.income.self_employment_expenses
    )
    total = wage_total + se_net
    return _step(
        trace,
        "FED_EARNED_INCOME",
        "Earned income",
        total,
        kind=StepKind.COMPUTED,
        inputs={"wages": wage_total, "self_employment": se_net},
        detail="Money you earned by working. Used to figure work-based credits.",
    )


def total_income(
    tax_return: TaxReturnInput,
    rules: RuleSet,
    trace: CalculationTrace,
    adjustments_total: Decimal,
) -> tuple[Decimal, Decimal]:
    """Form 1040 line 9. Returns ``(total_income, net_long_term_capital_gain)``.

    Social Security is computed last because its taxable portion depends on
    every other income item and on the above-the-line adjustments, which is
    why ``adjustments_total`` is a parameter here rather than being computed
    after income.
    """
    # Qualified dividends are a *subset* of ordinary dividends, not an
    # addition to them: they are recorded so the tax computation can apply
    # capital gains rates to that slice, but only the ordinary total is added
    # to income. Omitting this call leaves the worksheet with nothing to work
    # on and silently taxes investment income at ordinary rates.
    qualified_dividends(tax_return, trace)

    non_ss = (
        wages(tax_return, trace)
        + taxable_interest(tax_return, trace)
        + ordinary_dividends(tax_return, trace)
        + self_employment_net_profit(tax_return, trace)
        + retirement_distributions(tax_return, trace)
        + unemployment_compensation(tax_return, trace)
        + tax_return.income.other_taxable_income
    )
    capital, long_term = capital_gain_or_loss(tax_return, rules, trace)
    non_ss += capital

    exempt = tax_exempt_interest(tax_return, trace)
    social_security = taxable_social_security(
        tax_return, non_ss, adjustments_total, exempt, rules, trace
    )

    total = non_ss + social_security
    _step(
        trace,
        "FED_TOTAL_INCOME",
        "Total income",
        total,
        kind=StepKind.RESULT,
        form_line="Form 1040, line 9",
        inputs={"income_other_than_social_security": non_ss, "taxable_social_security": social_security},
    )
    return total, long_term
