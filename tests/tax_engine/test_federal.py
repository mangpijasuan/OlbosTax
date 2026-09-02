"""Federal calculation tests.

Two kinds of assertion appear here and the difference matters:

*   **Structural** tests assert relationships that hold regardless of what the
    rule values are -- a refund equals payments minus tax, the capital gains
    worksheet never produces more tax than ordinary rates would, a phase-out
    never goes negative.  These stay valid when the rule set is updated.

*   **Value** tests assert a specific dollar amount computed from the current
    DRAFT rule set.  They are regression locks, not correctness proofs: they
    detect unintended change, and they will legitimately need updating when the
    rules are verified and corrected.  They are marked ``regression`` so they
    can be run and reviewed as a group when a rule set changes.
"""

from datetime import date
from decimal import Decimal

import pytest

from olbostax_engine import TaxEngine, load_rule_set
from olbostax_schema import (
    W2,
    Address,
    DeductionsSection,
    Dependent,
    DependentRelationship,
    FilingStatus,
    Form1099DIV,
    IncomeSection,
    Spouse,
    TaxReturnInput,
    Taxpayer,
)

D = Decimal
pytestmark = pytest.mark.tax_engine


def close(actual: Decimal, expected: Decimal, tolerance: str = "0.01") -> bool:
    """Compare two Decimals within a tolerance.

    ``pytest.approx`` converts to float, which defeats the entire reason the
    engine uses Decimal. Comparing in Decimal keeps the test honest about what
    the engine actually produced.
    """
    return abs(Decimal(actual) - Decimal(expected)) <= Decimal(tolerance)

ADDRESS = Address(line1="1 Main St", city="Tulsa", state="OK", zip_code="74103")


@pytest.fixture(scope="module")
def engine() -> TaxEngine:
    return TaxEngine.for_tax_year(2025)


@pytest.fixture(scope="module")
def federal_rules():
    return load_rule_set("federal", 2025)


def make_return(
    *,
    filing_status: FilingStatus = FilingStatus.SINGLE,
    wages: str = "0",
    withheld: str = "0",
    dob: date = date(1990, 6, 15),
    **kwargs,
) -> TaxReturnInput:
    """Build a minimal return. Keyword arguments override any top-level field."""
    income = kwargs.pop("income", None) or IncomeSection(
        w2s=[
            W2(
                document_id="w2",
                box1_wages=D(wages),
                box2_federal_income_tax_withheld=D(withheld),
                box3_social_security_wages=D(wages),
                box5_medicare_wages=D(wages),
                box15_state="OK",
            )
        ]
        if wages != "0" or withheld != "0"
        else []
    )
    return TaxReturnInput(
        tax_year=2025,
        filing_status=filing_status,
        taxpayer=Taxpayer(
            first_name="Test",
            last_name="Person",
            ssn="900-00-0001",
            date_of_birth=dob,
            email="t@example.com",
            address=ADDRESS,
        ),
        income=income,
        files_oklahoma=kwargs.pop("files_oklahoma", False),
        **kwargs,
    )


class TestStructuralInvariants:
    """Properties that must hold whatever the rule values are."""

    @pytest.mark.parametrize("wages", ["0", "12000", "45000", "180000", "900000"])
    def test_refund_and_amount_owed_are_never_both_positive(self, engine, wages):
        result = engine.compute(make_return(wages=wages, withheld="5000")).federal
        assert result.refund == 0 or result.amount_owed == 0

    @pytest.mark.parametrize("wages", ["0", "12000", "45000", "180000"])
    def test_bottom_line_reconciles(self, engine, wages):
        """Refund minus amount owed must equal payments minus total tax."""
        r = engine.compute(make_return(wages=wages, withheld="4000")).federal
        net = r.total_payments - r.total_tax
        # Tolerance of $1: the bottom line is rounded to whole dollars, the
        # components are not.
        assert close(r.refund - r.amount_owed, net, tolerance="1.00")

    @pytest.mark.parametrize("wages", ["0", "30000", "150000"])
    def test_taxable_income_is_never_negative(self, engine, wages):
        assert engine.compute(make_return(wages=wages)).federal.taxable_income >= 0

    def test_more_income_never_reduces_take_home(self, engine):
        """No income level may make a taxpayer worse off in absolute terms.

        Graduated brackets guarantee this for tax alone, but phase-outs of
        credits can create genuine cliffs. This checks the property across a
        sweep so that a badly implemented phase-out is caught.
        """
        previous = None
        for wages in range(0, 200_000, 5_000):
            r = engine.compute(make_return(wages=str(wages), withheld="0")).federal
            after_tax = D(wages) - r.total_tax + r.refundable_credits
            if previous is not None:
                assert after_tax >= previous - D("1"), f"cliff at ${wages:,}"
            previous = after_tax

    def test_zero_income_produces_zero_tax(self, engine):
        r = engine.compute(make_return(wages="0")).federal
        assert r.total_tax == 0
        assert r.taxable_income == 0

    def test_withholding_with_no_income_is_fully_refunded(self, engine):
        r = engine.compute(make_return(wages="0", withheld="1500")).federal
        assert r.refund == D("1500")


class TestDeductionElection:
    def test_standard_deduction_used_when_larger(self, engine):
        r = engine.compute(make_return(wages="60000")).federal
        assert not r.deduction_is_itemized
        assert r.deduction_taken >= r.standard_deduction

    def test_itemizing_used_when_larger(self, engine):
        r = engine.compute(
            make_return(
                wages="120000",
                deductions=DeductionsSection(
                    home_mortgage_interest=D("22000"),
                    charitable_cash=D("8000"),
                    real_estate_taxes=D("6000"),
                ),
            )
        ).federal
        assert r.deduction_is_itemized
        assert r.itemized_deductions > r.standard_deduction

    def test_force_itemize_overrides_the_larger_standard_deduction(self, engine):
        """A married-filing-separately taxpayer must itemize if their spouse does."""
        r = engine.compute(
            make_return(
                filing_status=FilingStatus.MARRIED_FILING_SEPARATELY,
                wages="60000",
                spouse=Spouse(
                    first_name="Sp", last_name="Ouse", ssn="900-00-0002",
                    date_of_birth=date(1991, 2, 2),
                ),
                deductions=DeductionsSection(force_itemize=True, charitable_cash=D("100")),
            )
        ).federal
        assert r.deduction_is_itemized
        assert r.deduction_taken < r.standard_deduction

    def test_salt_cap_limits_state_and_local_taxes(self, engine, federal_rules):
        cap = federal_rules.decimal("itemized_deductions.salt_cap")
        r = engine.compute(
            make_return(
                wages="150000",
                deductions=DeductionsSection(
                    state_and_local_income_taxes=cap * 3,
                    home_mortgage_interest=D("1000"),
                ),
            )
        ).federal
        assert r.itemized_deductions <= cap + D("1000")

    def test_medical_expenses_below_the_floor_are_not_deductible(self, engine):
        """7.5% of AGI must be exceeded before any medical expense counts."""
        with_small = engine.compute(
            make_return(wages="100000", deductions=DeductionsSection(medical_expenses=D("2000")))
        ).federal
        without = engine.compute(make_return(wages="100000")).federal
        assert with_small.itemized_deductions == without.itemized_deductions


class TestAgeAndBlindness:
    def test_additional_standard_deduction_for_age_65(self, engine):
        young = engine.compute(make_return(wages="50000", dob=date(1990, 1, 1))).federal
        old = engine.compute(make_return(wages="50000", dob=date(1955, 1, 1))).federal
        assert old.standard_deduction > young.standard_deduction

    def test_age_measured_at_january_first_of_following_year(self, engine):
        """A taxpayer born 1 January 1961 is treated as 65 for tax year 2025.

        This is in the Form 1040 instructions and is easy to implement as
        "age on 31 December", which would deny the deduction.
        """
        born_jan_1 = engine.compute(make_return(wages="50000", dob=date(1961, 1, 1))).federal
        born_jan_2 = engine.compute(make_return(wages="50000", dob=date(1961, 1, 2))).federal
        assert born_jan_1.standard_deduction > born_jan_2.standard_deduction

    def test_blind_and_aged_stack(self, engine):
        base = make_return(wages="50000", dob=date(1950, 1, 1))
        aged_only = engine.compute(base).federal
        blind = base.model_copy(deep=True)
        blind.taxpayer.is_blind = True
        both = engine.compute(blind).federal
        assert both.standard_deduction > aged_only.standard_deduction


class TestCapitalGainsWorksheet:
    def _with_dividends(self, wages: str, qualified: str) -> TaxReturnInput:
        return make_return(
            wages=wages,
            income=IncomeSection(
                w2s=[W2(document_id="w2", box1_wages=D(wages), box3_social_security_wages=D(wages),
                        box5_medicare_wages=D(wages))],
                form_1099_divs=[
                    Form1099DIV(
                        document_id="div",
                        box1a_total_ordinary_dividends=D(qualified),
                        box1b_qualified_dividends=D(qualified),
                    )
                ],
            ),
        )

    def test_qualified_dividends_are_taxed_more_lightly_than_wages(self, engine):
        """The whole point of the worksheet."""
        as_dividends = engine.compute(self._with_dividends("60000", "10000")).federal
        as_wages = engine.compute(make_return(wages="70000")).federal
        assert as_dividends.taxable_income == as_wages.taxable_income
        assert as_dividends.tax_before_credits < as_wages.tax_before_credits

    def test_low_income_taxpayer_pays_zero_on_qualified_dividends(self, engine):
        """Below the first capital gains threshold the rate is 0%."""
        no_dividends = engine.compute(make_return(wages="20000")).federal
        with_dividends = engine.compute(self._with_dividends("20000", "5000")).federal
        assert with_dividends.tax_before_credits == no_dividends.tax_before_credits

    def test_worksheet_never_produces_more_tax_than_ordinary_rates(self, engine):
        for wages, qualified in [("40000", "5000"), ("90000", "20000"), ("300000", "80000")]:
            preferential = engine.compute(self._with_dividends(wages, qualified)).federal
            ordinary = engine.compute(
                make_return(wages=str(int(wages) + int(qualified)))
            ).federal
            assert preferential.tax_before_credits <= ordinary.tax_before_credits


class TestCreditOrdering:
    def _parent(self, wages: str, children: int = 2) -> TaxReturnInput:
        return make_return(
            filing_status=FilingStatus.HEAD_OF_HOUSEHOLD,
            wages=wages,
            dependents=[
                Dependent(
                    first_name=f"Kid{i}", last_name="Person",
                    ssn=f"90000001{i}", date_of_birth=date(2018, 3, 3),
                    relationship=DependentRelationship.SON,
                )
                for i in range(children)
            ],
        )

    def test_refundable_credits_are_paid_with_no_tax_liability(self, engine):
        """The central promise of the EITC and Additional CTC."""
        r = engine.compute(self._parent("24000")).federal
        assert r.total_tax == 0
        assert r.refundable_credits > 0
        assert r.refund > 0

    def test_nonrefundable_credits_cannot_create_a_refund(self, engine):
        """A credit that is not refundable stops at zero tax."""
        from olbostax_schema import CreditsSection

        r = engine.compute(
            make_return(
                wages="15000",
                credits=CreditsSection(retirement_savings_contributions=D("4000")),
            )
        ).federal
        assert r.credit_detail.get("savers", 0) <= r.tax_before_credits

    def test_child_tax_credit_phases_out_at_high_income(self, engine):
        low = engine.compute(self._parent("60000")).federal
        high = engine.compute(self._parent("450000")).federal
        assert low.credit_detail["child_tax_credit"] > 0
        assert (
            high.credit_detail["child_tax_credit"]
            + high.credit_detail["additional_child_tax_credit"]
            == 0
        )

    def test_child_without_work_authorized_ssn_gets_other_dependent_credit(self, engine):
        base = self._parent("60000", children=1)
        base.dependents[0].has_valid_ssn_for_employment = False
        r = engine.compute(base).federal
        assert r.credit_detail["child_tax_credit"] > 0  # falls back to the $500 ODC
        assert r.credit_detail["additional_child_tax_credit"] == 0

    def test_eitc_denied_when_investment_income_too_high(self, engine, federal_rules):
        from olbostax_schema import Form1099INT

        limit = federal_rules.decimal("earned_income_credit.investment_income_limit")
        base = self._parent("24000")
        base.income.form_1099_ints = [
            Form1099INT(document_id="int", box1_interest_income=limit + 1)
        ]
        assert engine.compute(base).federal.credit_detail["earned_income_credit"] == 0

    def test_eitc_denied_for_married_filing_separately(self, engine):
        r = engine.compute(
            make_return(
                filing_status=FilingStatus.MARRIED_FILING_SEPARATELY,
                wages="24000",
                spouse=Spouse(first_name="S", last_name="P", ssn="900000099",
                              date_of_birth=date(1990, 1, 1)),
            )
        ).federal
        assert r.credit_detail["earned_income_credit"] == 0


class TestSelfEmploymentTax:
    def _self_employed(self, nec: str, wages: str = "0") -> TaxReturnInput:
        from olbostax_schema import Form1099NEC

        w2s = (
            [W2(document_id="w2", box1_wages=D(wages), box3_social_security_wages=D(wages),
                box5_medicare_wages=D(wages))]
            if wages != "0"
            else []
        )
        return make_return(
            income=IncomeSection(
                w2s=w2s,
                form_1099_necs=[
                    Form1099NEC(document_id="nec", box1_nonemployee_compensation=D(nec))
                ],
            )
        )

    def test_no_se_tax_below_the_minimum(self, engine):
        assert engine.compute(self._self_employed("300")).federal.self_employment_tax == 0

    def test_se_tax_applies_to_92_35_percent_of_profit(self, engine):
        r = engine.compute(self._self_employed("50000")).federal
        expected = D("50000") * D("0.9235") * (D("0.124") + D("0.029"))
        assert close(r.self_employment_tax, expected)

    def test_half_of_se_tax_is_deductible(self, engine):
        r = engine.compute(self._self_employed("50000")).federal
        assert close(r.adjustments_to_income, r.self_employment_tax / 2)

    def test_w2_wages_consume_the_social_security_wage_base(self, engine, federal_rules):
        """A taxpayer already over the wage base pays only Medicare on SE income."""
        base = federal_rules.decimal("self_employment_tax.social_security_wage_base")
        r = engine.compute(self._self_employed("20000", wages=str(int(base) + 10000))).federal
        medicare_only = D("20000") * D("0.9235") * D("0.029")
        assert close(r.self_employment_tax, medicare_only)


class TestSocialSecurityTaxability:
    def _retiree(self, benefits: str, other: str) -> TaxReturnInput:
        from olbostax_schema import Form1099R

        return make_return(
            dob=date(1955, 1, 1),
            income=IncomeSection(
                social_security_benefits_received=D(benefits),
                form_1099_rs=[
                    Form1099R(
                        document_id="r", box1_gross_distribution=D(other),
                        box2a_taxable_amount=D(other),
                    )
                ]
                if other != "0"
                else [],
            ),
        )

    def test_benefits_alone_are_not_taxable(self, engine):
        """Someone whose only income is Social Security owes no federal tax."""
        r = engine.compute(self._retiree("24000", "0")).federal
        assert r.total_income == D("24000") or r.taxable_income == 0
        assert r.total_tax == 0

    def test_at_most_85_percent_is_ever_taxable(self, engine):
        r = engine.compute(self._retiree("40000", "500000")).federal
        from olbostax_engine import compute

        trace = compute(self._retiree("40000", "500000")).trace
        assert trace.amount("FED_SS_TAXABLE") <= D("40000") * D("0.85")

    def test_more_other_income_increases_the_taxable_portion(self, engine):
        from olbostax_engine import compute

        low = compute(self._retiree("30000", "10000")).trace.amount("FED_SS_TAXABLE")
        high = compute(self._retiree("30000", "60000")).trace.amount("FED_SS_TAXABLE")
        assert high > low


class TestRequiredForms:
    def test_only_form_1040_for_a_simple_return(self, engine):
        assert engine.compute(make_return(wages="40000")).federal.required_forms == ["Form 1040"]

    def test_schedule_a_only_when_itemizing(self, engine):
        r = engine.compute(
            make_return(
                wages="120000",
                deductions=DeductionsSection(home_mortgage_interest=D("30000")),
            )
        ).federal
        assert "Schedule A" in r.required_forms

    def test_schedule_se_when_self_employed(self, engine):
        from olbostax_schema import Form1099NEC

        r = engine.compute(
            make_return(
                income=IncomeSection(
                    form_1099_necs=[
                        Form1099NEC(document_id="n", box1_nonemployee_compensation=D("30000"))
                    ]
                )
            )
        ).federal
        assert "Schedule SE" in r.required_forms
        assert "Schedule C" in r.required_forms
