"""Regression locks for the synthetic taxpayers.

**These assert current behaviour, not correctness.** Every value below was
produced by the engine running against DRAFT rule sets whose numbers have not
been verified against IRS or Oklahoma Tax Commission publications.

Their job is to make change *visible*. When someone refactors the credit
ordering or corrects a bracket, these fail, and the diff shows exactly which
taxpayers were affected and by how much. That is the point: a tax engine
change that silently moves ten thousand refunds by $40 is the failure mode
worth engineering against.

When a rule set is verified and its values corrected, these expectations will
legitimately change. The process for that is in docs/TAX_RULE_GOVERNANCE.md:
the reviewer confirms each moved number is explained by the rule change before
the new baseline is committed.
"""

from decimal import Decimal

import pytest

from olbostax_engine import compute
from olbostax_schema.money import to_cents
from tests.fixtures.synthetic_taxpayers import ALL_SYNTHETIC_TAXPAYERS

D = Decimal
pytestmark = [pytest.mark.tax_engine, pytest.mark.regression]


# name -> (federal AGI, federal total tax, federal refund, oklahoma refund)
BASELINE: dict[str, tuple[str, str, str, str]] = {
    "SyntheticLowIncomeEITC001": ("26950.00", "0.00", "10021.00", "570.00"),
    "SyntheticMarriedFamily001": ("123660.00", "4735.00", "4815.00", "0.00"),
    "SyntheticRetiree001": ("55662.50", "896.25", "2204.00", "754.00"),
    "SyntheticSelfEmployed001": ("55326.15", "3411.83", "0.00", "0.00"),
    "SyntheticTaxpayer001": ("58552.56", "4897.81", "222.00", "0.00"),
}


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_synthetic_taxpayer_baseline(name: str) -> None:
    expected_agi, expected_tax, expected_fed_refund, expected_ok_refund = BASELINE[name]
    computation = compute(ALL_SYNTHETIC_TAXPAYERS[name]())

    federal = computation.federal
    assert federal is not None
    assert federal.adjusted_gross_income == D(expected_agi), "federal AGI moved"
    assert federal.total_tax == D(expected_tax), "federal total tax moved"
    assert federal.refund == D(expected_fed_refund), "federal refund moved"

    oklahoma = computation.oklahoma
    assert oklahoma is not None
    assert oklahoma.refund == D(expected_ok_refund), "Oklahoma refund moved"


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_computation_is_deterministic(name: str) -> None:
    """The same input must produce the same output, every time.

    Non-determinism in a tax engine -- from dict ordering, floating point, or
    an unseeded value -- makes stored computations unreproducible and would
    quietly break the audit trail the whole design depends on.
    """
    first = compute(ALL_SYNTHETIC_TAXPAYERS[name]())
    second = compute(ALL_SYNTHETIC_TAXPAYERS[name]())

    assert first.federal == second.federal
    assert first.oklahoma == second.oklahoma
    assert [s.amount for s in first.trace.steps] == [s.amount for s in second.trace.steps]
    assert [s.code for s in first.trace.steps] == [s.code for s in second.trace.steps]


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_no_synthetic_return_is_filable_while_rules_are_draft(name: str) -> None:
    """The certification gate must hold for every taxpayer, not just some.

    If this test ever fails, either a rule set was certified (in which case
    update it deliberately) or the gate was bypassed (in which case a return
    computed from unverified rules could reach the IRS).
    """
    computation = compute(ALL_SYNTHETIC_TAXPAYERS[name]())
    assert not computation.rule_sets_certified_for_filing
    assert not computation.can_be_filed


@pytest.mark.parametrize("name", sorted(ALL_SYNTHETIC_TAXPAYERS))
def test_trace_explains_every_headline_number(name: str) -> None:
    """Each figure shown to a taxpayer must be reconstructible from the trace."""
    computation = compute(ALL_SYNTHETIC_TAXPAYERS[name]())
    trace = computation.trace

    # The trace keeps full arithmetic precision while the result reports money,
    # so they are compared at money precision -- the claim under test is that
    # the trace explains the headline number, not that the two carry the same
    # number of decimal places.
    assert to_cents(trace.amount("FED_AGI")) == computation.federal.adjusted_gross_income
    assert to_cents(trace.amount("FED_TAXABLE_INCOME")) == computation.federal.taxable_income
    assert to_cents(trace.amount("FED_TOTAL_TAX")) == computation.federal.total_tax
    assert to_cents(trace.amount("OK_AGI")) == computation.oklahoma.oklahoma_adjusted_gross_income
    assert to_cents(trace.amount("OK_TOTAL_TAX")) == computation.oklahoma.total_tax

    # Every taxpayer-facing step needs a label, and money steps that represent
    # a limitation need an explanation -- an unexplained reduction is exactly
    # what generates a support ticket.
    for step in trace.breakdown():
        assert step.label
        if step.kind.value == "LIMITATION":
            assert step.detail or step.rule_citation, f"{step.code} limits without explaining"


def test_no_sensitive_data_appears_in_the_trace() -> None:
    """The trace is stored, logged and shown in support tooling.

    It must describe money and arithmetic, never people. A trace that carried
    an SSN would put it into every calculation snapshot in the database.
    """
    for factory in ALL_SYNTHETIC_TAXPAYERS.values():
        tax_return = factory()
        computation = compute(tax_return)
        serialized = computation.trace.model_dump_json()

        ssns = [tax_return.taxpayer.ssn.reveal()]
        if tax_return.spouse:
            ssns.append(tax_return.spouse.ssn.reveal())
        ssns.extend(d.ssn.reveal() for d in tax_return.dependents)

        for ssn in ssns:
            assert ssn not in serialized
        assert tax_return.taxpayer.last_name not in serialized
        if tax_return.direct_deposit:
            assert tax_return.direct_deposit.account_number.reveal() not in serialized
