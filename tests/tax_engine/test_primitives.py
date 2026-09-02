"""Tests for the calculation primitives.

These are tested directly rather than only through the engine because they are
the shared foundation: a bug in ``apply_bracket_schedule`` is wrong on every
return in both jurisdictions, and diagnosing it through a full return
calculation is far harder than catching it here.
"""

from decimal import Decimal

import pytest

from olbostax_engine.calculations import (
    apply_bracket_schedule,
    marginal_rate,
    phase_out_by_increment,
    phase_out_ratably,
    rate_for_amount,
)

INF = Decimal("Infinity")
D = Decimal

SIMPLE = [(D("10000"), D("0.10")), (D("40000"), D("0.20")), (INF, D("0.30"))]

pytestmark = pytest.mark.tax_engine


class TestBracketSchedule:
    def test_zero_income_is_zero_tax(self):
        assert apply_bracket_schedule(D("0"), SIMPLE) == 0

    def test_negative_income_is_zero_tax(self):
        assert apply_bracket_schedule(D("-5000"), SIMPLE) == 0

    def test_within_first_bracket(self):
        assert apply_bracket_schedule(D("5000"), SIMPLE) == D("500")

    def test_exactly_at_bracket_edge(self):
        assert apply_bracket_schedule(D("10000"), SIMPLE) == D("1000")

    def test_spans_two_brackets(self):
        # 10,000 at 10% + 5,000 at 20%
        assert apply_bracket_schedule(D("15000"), SIMPLE) == D("2000")

    def test_spans_all_brackets(self):
        # 1,000 + 6,000 + 3,000
        assert apply_bracket_schedule(D("50000"), SIMPLE) == D("10000")

    def test_only_the_slice_in_a_bracket_is_taxed_at_its_rate(self):
        """The single most common tax bug: applying the top rate to everything."""
        tax = apply_bracket_schedule(D("40001"), SIMPLE)
        naive_flat_rate = D("40001") * D("0.30")
        assert tax < naive_flat_rate
        assert tax == D("1000") + D("6000") + D("0.30")

    def test_no_intermediate_rounding(self):
        """Bracket-by-bracket rounding would make the result depend on the split."""
        assert apply_bracket_schedule(D("15000.005"), SIMPLE) == D("2000.001")

    def test_one_dollar_more_costs_at_most_the_marginal_rate(self):
        """A bracket edge must not produce a cliff."""
        for edge in (D("10000"), D("40000")):
            before = apply_bracket_schedule(edge, SIMPLE)
            after = apply_bracket_schedule(edge + 1, SIMPLE)
            assert after - before <= D("0.30")


class TestMarginalRate:
    def test_reports_bracket_containing_income(self):
        assert marginal_rate(D("5000"), SIMPLE) == D("0.10")
        assert marginal_rate(D("20000"), SIMPLE) == D("0.20")
        assert marginal_rate(D("400000"), SIMPLE) == D("0.30")


class TestRateForAmount:
    """Cliff-edge tables (Saver's Credit) pick one rate, they do not blend."""

    TIERS = [(D("20000"), D("0.50")), (D("30000"), D("0.20")), (INF, D("0.00"))]

    def test_picks_single_rate(self):
        assert rate_for_amount(D("19999"), self.TIERS) == D("0.50")
        assert rate_for_amount(D("20000"), self.TIERS) == D("0.50")
        assert rate_for_amount(D("20001"), self.TIERS) == D("0.20")

    def test_falls_off_a_cliff(self):
        """Unlike a graduated schedule, one dollar can cost the whole benefit."""
        assert rate_for_amount(D("30000"), self.TIERS) == D("0.20")
        assert rate_for_amount(D("30001"), self.TIERS) == D("0.00")


class TestRatablePhaseOut:
    def test_full_benefit_below_start(self):
        assert phase_out_ratably(D("2500"), D("50000"), D("80000"), D("90000")) == D("2500")

    def test_nothing_above_end(self):
        assert phase_out_ratably(D("2500"), D("95000"), D("80000"), D("90000")) == 0

    def test_half_at_midpoint(self):
        assert phase_out_ratably(D("2500"), D("85000"), D("80000"), D("90000")) == D("1250")

    def test_never_negative(self):
        assert phase_out_ratably(D("100"), D("999999"), D("80000"), D("90000")) >= 0


class TestIncrementPhaseOut:
    """The '$50 for each $1,000 or fraction thereof' pattern (IRC s.24(b)(2))."""

    ARGS = (D("1000"), D("50"))

    def test_full_benefit_at_threshold(self):
        assert phase_out_by_increment(D("4400"), D("200000"), D("200000"), *self.ARGS) == D("4400")

    def test_one_dollar_over_costs_a_whole_increment(self):
        """'Or fraction thereof' rounds the excess UP to a whole increment.

        Computing this as a smooth ratio would reduce the credit by five cents
        instead of fifty dollars -- a real bug that overstates refunds.
        """
        assert phase_out_by_increment(D("4400"), D("200001"), D("200000"), *self.ARGS) == D("4350")

    def test_exactly_one_increment_over(self):
        assert phase_out_by_increment(D("4400"), D("201000"), D("200000"), *self.ARGS) == D("4350")

    def test_just_past_one_increment(self):
        assert phase_out_by_increment(D("4400"), D("201001"), D("200000"), *self.ARGS) == D("4300")

    def test_floors_at_zero_rather_than_going_negative(self):
        assert phase_out_by_increment(D("4400"), D("900000"), D("200000"), *self.ARGS) == 0
