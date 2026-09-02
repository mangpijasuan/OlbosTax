"""Self-employment tax -- Schedule SE, IRC s.1401 and s.1402.

Self-employment tax is where a taxpayer who moved from a W-2 job to contract
work gets an unpleasant surprise, so the trace here is deliberately explicit
about each step.

The structure of the computation:

1.  Net earnings from self-employment = net profit x 92.35%.  The 7.65%
    haircut exists because an employee's share of FICA is not itself subject to
    FICA, and this puts the self-employed on comparable footing.
2.  If net earnings are under the statutory minimum, no SE tax is due at all.
3.  Social Security portion: 12.4% on net earnings, but only up to the annual
    wage base -- and W-2 wages already subject to Social Security tax consume
    that base first.
4.  Medicare portion: 2.9% on all net earnings, with no cap.
5.  Half of the total is deductible as an above-the-line adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from olbostax_schema import Jurisdiction, StepKind, TaxReturnInput, TraceStep
from olbostax_schema.money import ZERO, clamp_non_negative
from olbostax_schema.trace import CalculationTrace

from ..rules import RuleSet

__all__ = ["SelfEmploymentTax", "calculate_self_employment_tax", "additional_medicare_tax"]


@dataclass(frozen=True, slots=True)
class SelfEmploymentTax:
    net_earnings: Decimal
    social_security_portion: Decimal
    medicare_portion: Decimal
    total: Decimal
    deductible_half: Decimal


def calculate_self_employment_tax(
    net_profit: Decimal,
    social_security_wages_already_taxed: Decimal,
    rules: RuleSet,
    trace: CalculationTrace,
) -> SelfEmploymentTax:
    """Compute Schedule SE tax on ``net_profit``.

    ``social_security_wages_already_taxed`` is W-2 Box 3 plus Box 7, which
    consume the Social Security wage base before self-employment earnings do.
    Omitting this coordination over-taxes anyone who has both a job and a side
    business -- a large share of the taxpayers this product is built for.
    """
    if net_profit <= 0:
        return SelfEmploymentTax(ZERO, ZERO, ZERO, ZERO, ZERO)

    factor = rules.decimal("self_employment_tax.net_earnings_factor")
    minimum = rules.decimal("self_employment_tax.minimum_net_earnings")
    ss_rate = rules.decimal("self_employment_tax.social_security_rate")
    wage_base = rules.decimal("self_employment_tax.social_security_wage_base")
    medicare_rate = rules.decimal("self_employment_tax.medicare_rate")
    deductible_share = rules.decimal("self_employment_tax.deductible_share")

    net_earnings = net_profit * factor
    trace.add(
        TraceStep(
            code="FED_SE_NET_EARNINGS",
            label="Net earnings from self-employment",
            amount=net_earnings,
            kind=StepKind.COMPUTED,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule SE, line 4",
            inputs={"net_profit": net_profit, "factor": factor},
            rule_citation="IRC s.1402(a)(12)",
            detail=(
                "Self-employment tax applies to 92.35% of your net business profit, "
                "not to all of it."
            ),
        )
    )

    if net_earnings < minimum:
        trace.add(
            TraceStep(
                code="FED_SE_TAX_BELOW_MINIMUM",
                label="No self-employment tax due",
                amount=ZERO,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                rule_citation="IRC s.1402(b)(2)",
                detail=(
                    f"Net earnings under ${minimum:,.0f} do not owe self-employment tax."
                ),
            )
        )
        return SelfEmploymentTax(net_earnings, ZERO, ZERO, ZERO, ZERO)

    remaining_base = clamp_non_negative(wage_base - social_security_wages_already_taxed)
    ss_earnings = min(net_earnings, remaining_base)
    ss_tax = ss_earnings * ss_rate
    medicare_tax = net_earnings * medicare_rate
    total = ss_tax + medicare_tax
    deductible = total * deductible_share

    if remaining_base < wage_base:
        trace.add(
            TraceStep(
                code="FED_SE_WAGE_BASE_COORDINATION",
                label="Social Security wage base reduced by your W-2 wages",
                amount=remaining_base,
                kind=StepKind.LIMITATION,
                jurisdiction=Jurisdiction.FEDERAL,
                inputs={"wage_base": wage_base, "w2_social_security_wages": social_security_wages_already_taxed},
                rule_citation="IRC s.1402(b)(1)",
                detail=(
                    "Wages from your job already used part of this year's Social "
                    "Security limit, so less of your self-employment income is subject "
                    "to that part of the tax."
                ),
            )
        )

    trace.add(
        TraceStep(
            code="FED_SE_TAX",
            label="Self-employment tax",
            amount=total,
            kind=StepKind.RESULT,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule 2, line 4",
            inputs={"social_security": ss_tax, "medicare": medicare_tax},
            rule_citation="IRC s.1401",
            detail=(
                "Because you were self-employed, you pay both the employee and the "
                "employer share of Social Security and Medicare. Half of it is "
                "deductible."
            ),
        )
    )
    trace.add(
        TraceStep(
            code="FED_SE_TAX_DEDUCTION",
            label="Deductible part of self-employment tax",
            amount=deductible,
            kind=StepKind.SUBTOTAL,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule 1, line 15",
            rule_citation="IRC s.164(f)",
        )
    )

    return SelfEmploymentTax(net_earnings, ss_tax, medicare_tax, total, deductible)


def additional_medicare_tax(
    tax_return: TaxReturnInput,
    medicare_wages: Decimal,
    se_net_earnings: Decimal,
    rules: RuleSet,
    trace: CalculationTrace,
) -> Decimal:
    """Form 8959 -- 0.9% on wages and SE earnings above an unindexed threshold.

    Employers withhold this once an individual employee crosses $200,000, which
    means a married couple each earning $150,000 will owe it at filing with
    nothing withheld.  Computing it is what keeps that couple from an
    underpayment notice.
    """
    status = tax_return.filing_status.value
    threshold = rules.decimal(f"additional_medicare_tax.threshold.{status}")
    rate = rules.decimal("additional_medicare_tax.rate")

    combined = medicare_wages + clamp_non_negative(se_net_earnings)
    excess = clamp_non_negative(combined - threshold)
    if excess == 0:
        return ZERO

    tax = excess * rate
    trace.add(
        TraceStep(
            code="FED_ADDITIONAL_MEDICARE_TAX",
            label="Additional Medicare tax",
            amount=tax,
            kind=StepKind.RESULT,
            jurisdiction=Jurisdiction.FEDERAL,
            form_line="Schedule 2, line 11",
            inputs={"wages_and_self_employment": combined, "threshold": threshold},
            rule_citation="IRC s.3101(b)(2)",
            detail=(
                f"An extra 0.9% Medicare tax applies to earnings above ${threshold:,.0f} "
                f"for {tax_return.filing_status.label.lower()} filers."
            ),
        )
    )
    return tax
