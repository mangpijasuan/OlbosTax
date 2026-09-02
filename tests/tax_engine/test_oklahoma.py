"""Oklahoma calculation tests.

Oklahoma's rule values are unverified, so these tests focus on the *structure*
of the Oklahoma calculation -- that it starts from federal AGI, that state
withholding is only counted when the document names Oklahoma, that Social
Security is exempted, that subtraction caps bind. Those relationships stay
true when the rule values are corrected; the specific dollar amounts will not.
"""

from datetime import date
from decimal import Decimal

import pytest
from olbostax_engine import TaxEngine, load_rule_set
from olbostax_schema import (
    W2,
    Address,
    FilingStatus,
    Form1099INT,
    IncomeSection,
    OklahomaSection,
    ResidencyStatus,
    Taxpayer,
    TaxReturnInput,
)

D = Decimal
pytestmark = pytest.mark.tax_engine

ADDRESS = Address(line1="1 Main St", city="Tulsa", state="OK", zip_code="74103")


@pytest.fixture(scope="module")
def engine() -> TaxEngine:
    return TaxEngine.for_tax_year(2025)


@pytest.fixture(scope="module")
def ok_rules():
    return load_rule_set("oklahoma", 2025)


def ok_return(
    *,
    wages: str = "50000",
    state: str = "OK",
    state_withheld: str = "1500",
    **kwargs,
) -> TaxReturnInput:
    income = kwargs.pop("income", None) or IncomeSection(
        w2s=[
            W2(
                document_id="w2",
                box1_wages=D(wages),
                box3_social_security_wages=D(wages),
                box5_medicare_wages=D(wages),
                box15_state=state,
                box16_state_wages=D(wages),
                box17_state_income_tax=D(state_withheld),
            )
        ]
    )
    return TaxReturnInput(
        tax_year=2025,
        filing_status=kwargs.pop("filing_status", FilingStatus.SINGLE),
        taxpayer=Taxpayer(
            first_name="Okie", last_name="Resident", ssn="900-77-0001",
            date_of_birth=kwargs.pop("dob", date(1985, 5, 5)),
            email="ok@example.com", address=ADDRESS,
        ),
        income=income,
        files_oklahoma=True,
        **kwargs,
    )


class TestOklahomaStructure:
    def test_starts_from_federal_agi(self, engine):
        c = engine.compute(ok_return())
        assert c.oklahoma.federal_adjusted_gross_income == c.federal.adjusted_gross_income

    def test_oklahoma_agi_is_federal_agi_plus_additions_less_subtractions(self, engine):
        ok = engine.compute(ok_return()).oklahoma
        assert (
            ok.oklahoma_adjusted_gross_income
            == ok.federal_adjusted_gross_income + ok.oklahoma_additions - ok.oklahoma_subtractions
        )

    def test_taxable_income_is_never_negative(self, engine):
        assert engine.compute(ok_return(wages="1000")).oklahoma.oklahoma_taxable_income >= 0

    def test_refund_and_owed_are_never_both_positive(self, engine):
        for wages in ("0", "25000", "90000", "400000"):
            ok = engine.compute(ok_return(wages=wages)).oklahoma
            assert ok.refund == 0 or ok.amount_owed == 0

    def test_oklahoma_standard_deduction_differs_from_federal(self, engine):
        """Oklahoma sets its own amount; using the federal one would be wrong."""
        c = engine.compute(ok_return())
        assert c.oklahoma.deduction_taken != c.federal.standard_deduction


class TestWithholdingAttribution:
    def test_oklahoma_withholding_counted(self, engine):
        ok = engine.compute(ok_return(state="OK", state_withheld="1500")).oklahoma
        assert ok.oklahoma_withholding == D("1500")

    def test_other_state_withholding_not_counted(self, engine):
        """A Texas W-2's state tax is not an Oklahoma payment.

        Summing every state withholding box regardless of state code would
        hand the taxpayer a refund of money Oklahoma never received.
        """
        ok = engine.compute(ok_return(state="KS", state_withheld="1500")).oklahoma
        assert ok.oklahoma_withholding == 0

    def test_blank_state_code_not_counted(self, engine):
        ok = engine.compute(ok_return(state="", state_withheld="1500")).oklahoma
        assert ok.oklahoma_withholding == 0


class TestSubtractions:
    def test_social_security_is_exempt(self, engine):
        """Oklahoma does not tax Social Security even though the IRS does."""
        r = ok_return(
            wages="40000",
            dob=date(1955, 1, 1),
            income=IncomeSection(
                w2s=[W2(document_id="w2", box1_wages=D("40000"), box15_state="OK",
                        box3_social_security_wages=D("40000"), box5_medicare_wages=D("40000"))],
                social_security_benefits_received=D("30000"),
            ),
        )
        c = engine.compute(r)
        assert c.trace.amount("FED_SS_TAXABLE") > 0
        assert c.oklahoma.subtraction_detail["social_security"] == c.trace.amount("FED_SS_TAXABLE")

    def test_us_treasury_interest_is_exempt(self, engine):
        """31 U.S.C. s.3124 bars states from taxing Treasury interest."""
        r = ok_return(
            income=IncomeSection(
                w2s=[W2(document_id="w2", box1_wages=D("50000"), box15_state="OK",
                        box3_social_security_wages=D("50000"), box5_medicare_wages=D("50000"))],
                form_1099_ints=[
                    Form1099INT(document_id="i", box3_interest_on_us_savings_bonds=D("800"))
                ],
            )
        )
        ok = engine.compute(r).oklahoma
        assert ok.subtraction_detail["us_treasury_interest"] == D("800")

    def test_retirement_subtraction_is_capped(self, engine, ok_rules):
        cap = ok_rules.decimal("subtractions.us_government_retirement_cap")
        r = ok_return(
            oklahoma=OklahomaSection(us_government_retirement_benefits=cap * 5)
        )
        ok = engine.compute(r).oklahoma
        assert ok.subtraction_detail["us_government_retirement"] == cap


class TestResidency:
    def test_part_year_resident_is_blocked_and_not_computed(self, engine):
        """Form 511-NR is not implemented; the engine must refuse, not guess."""
        r = ok_return(
            oklahoma=OklahomaSection(residency=ResidencyStatus.PART_YEAR_RESIDENT, months_resident=7)
        )
        c = engine.compute(r)
        assert c.oklahoma is None
        assert any(f.code == "state.oklahoma_part_year" for f in c.findings)
        assert not c.can_be_filed

    def test_federal_return_still_computed_for_part_year_resident(self, engine):
        """Blocking the state return must not block the federal one."""
        r = ok_return(
            oklahoma=OklahomaSection(residency=ResidencyStatus.NONRESIDENT)
        )
        c = engine.compute(r)
        assert c.federal is not None
        assert c.federal.adjusted_gross_income > 0


class TestOklahomaCredits:
    def test_oklahoma_eic_is_a_share_of_the_federal_credit(self, engine, ok_rules):
        from olbostax_schema import Dependent, DependentRelationship

        rate = ok_rules.decimal("credits.earned_income_credit.rate_of_federal")
        r = ok_return(
            wages="22000",
            filing_status=FilingStatus.HEAD_OF_HOUSEHOLD,
            dependents=[
                Dependent(
                    first_name="Kid", last_name="Resident", ssn="900-77-0002",
                    date_of_birth=date(2015, 1, 1),
                    relationship=DependentRelationship.SON,
                )
            ],
        )
        c = engine.compute(r)
        federal_eitc = c.federal.credit_detail["earned_income_credit"]
        assert federal_eitc > 0
        assert c.oklahoma.credit_detail["earned_income_credit"] == federal_eitc * rate
