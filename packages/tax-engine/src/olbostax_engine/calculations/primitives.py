"""Primitive operations that recur throughout federal and state tax law.

Two shapes account for most of the arithmetic in an individual return: walking
a graduated rate schedule, and reducing an amount as income rises.  Both are
easy to write and easy to write *subtly* wrong, so they are implemented once,
tested directly, and reused rather than re-derived in each credit.

The classic errors these avoid:

*   Applying the marginal rate to the whole amount instead of to the slice
    inside the bracket.
*   Rounding at each bracket, which makes the total depend on bracket count.
*   Phase-outs that go negative and quietly turn a credit into a charge.
*   Phase-outs measured in whole increments (the "$50 for each $1,000 or
    fraction thereof" pattern) computed as a smooth ratio, which understates
    the reduction for every taxpayer between two increments.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from olbostax_schema.money import ZERO, clamp_non_negative, money

__all__ = [
    "apply_bracket_schedule",
    "marginal_rate",
    "phase_out_ratably",
    "phase_out_by_increment",
    "rate_for_amount",
]

Bracket = tuple[Decimal, Decimal]


def apply_bracket_schedule(amount: Decimal, brackets: list[Bracket]) -> Decimal:
    """Tax ``amount`` under a graduated schedule of ``(upper_bound, rate)`` pairs.

    Each slice of income is taxed at its own bracket's rate.  ``brackets`` must
    be ordered ascending with the final bound ``Decimal("Infinity")``.

    No rounding happens here.  The caller rounds once, at the point the result
    lands on a form line, which is the only place rounding is authorised.
    """
    taxable = clamp_non_negative(amount)
    if taxable == 0:
        return ZERO

    tax = ZERO
    lower = ZERO
    for upper, rate in brackets:
        if taxable <= lower:
            break
        slice_top = taxable if taxable < upper else upper
        tax += (slice_top - lower) * rate
        lower = upper
    return tax


def marginal_rate(amount: Decimal, brackets: list[Bracket]) -> Decimal:
    """The rate applying to the next dollar of income. Display only."""
    taxable = clamp_non_negative(amount)
    for upper, rate in brackets:
        if taxable < upper:
            return rate
    return brackets[-1][1] if brackets else ZERO


def rate_for_amount(amount: Decimal, tiers: list[Bracket]) -> Decimal:
    """Pick a single rate from a cliff-edge table (not a graduated schedule).

    The Saver's Credit works this way: the whole contribution is multiplied by
    one rate determined by where AGI falls, with no blending between tiers.
    Using :func:`apply_bracket_schedule` for such a table would be wrong.
    """
    value = clamp_non_negative(amount)
    for upper, rate in tiers:
        if value <= upper:
            return rate
    return tiers[-1][1] if tiers else ZERO


def phase_out_ratably(
    benefit: Decimal, income: Decimal, start: Decimal, end: Decimal
) -> Decimal:
    """Reduce ``benefit`` linearly to zero as ``income`` moves from ``start`` to ``end``.

    The pattern used by the student loan interest deduction and the education
    credits.  Below ``start`` the full benefit survives; at or above ``end``
    nothing does.
    """
    benefit = money(benefit)
    if benefit <= 0:
        return ZERO
    income = money(income)
    if income <= start:
        return benefit
    if income >= end:
        return ZERO
    span = end - start
    if span <= 0:
        return ZERO
    return benefit * (end - income) / span


def phase_out_by_increment(
    benefit: Decimal,
    income: Decimal,
    threshold: Decimal,
    increment: Decimal,
    reduction_per_increment: Decimal,
) -> Decimal:
    """Reduce ``benefit`` in discrete steps -- the Child Tax Credit pattern.

    IRC s.24(b)(2) reduces the credit by ``$50`` for each ``$1,000`` "or
    fraction thereof" by which modified AGI exceeds the threshold.  "Or
    fraction thereof" means the excess is rounded *up* to a whole increment, so
    a taxpayer $1 over a $1,000 boundary loses the full $50, not five cents.
    Computing this as a smooth ratio is a real and commonly shipped bug.
    """
    benefit = money(benefit)
    if benefit <= 0:
        return ZERO
    excess = clamp_non_negative(money(income) - threshold)
    if excess == 0:
        return benefit
    steps = (excess / increment).to_integral_value(rounding=ROUND_CEILING)
    return clamp_non_negative(benefit - steps * reduction_per_increment)
