"""Structural checks on the rule files themselves.

These do not verify that the values are *correct* -- only a qualified reviewer
against the primary sources can do that, and the process is in
docs/TAX_RULE_GOVERNANCE.md. What they verify is that the files are internally
consistent and honestly labelled, which catches a category of transcription
error that would otherwise reach a taxpayer.

The distinction matters. A file whose EITC maximum disagrees with its own
phase-in arithmetic is provably wrong without consulting any source. A file
where both agree may still be wrong, and these tests say nothing about that.
"""

from decimal import Decimal

import pytest
from olbostax_engine import CertificationStatus, available_rule_sets, load_rule_set
from olbostax_schema.enums import FilingStatus

D = Decimal
pytestmark = pytest.mark.tax_engine

ALL_STATUSES = [status.value for status in FilingStatus]


@pytest.fixture(scope="module")
def federal():
    return load_rule_set("federal", 2025)


@pytest.fixture(scope="module")
def oklahoma():
    return load_rule_set("oklahoma", 2025)


class TestHonestLabelling:
    """A rule set must not claim more confidence than it has."""

    def test_uncertified_rule_sets_carry_a_notice(self):
        for jurisdiction, year, certification in available_rule_sets():
            rule_set = load_rule_set(jurisdiction, year)
            if certification is not CertificationStatus.PRODUCTION:
                assert rule_set.meta.unverified_notice, (
                    f"{rule_set.identifier} is {certification.value} but carries no "
                    "notice explaining what has not been verified"
                )
                assert "REQUIRES OFFICIAL VERIFICATION" in rule_set.meta.unverified_notice

    def test_production_rule_sets_record_who_verified_them(self):
        """A PRODUCTION claim without a named reviewer is not evidence of anything."""
        for jurisdiction, year, certification in available_rule_sets():
            rule_set = load_rule_set(jurisdiction, year)
            if certification is CertificationStatus.PRODUCTION:
                assert rule_set.meta.verified_by, f"{rule_set.identifier} names no reviewer"
                assert rule_set.meta.verified_at, f"{rule_set.identifier} has no verification date"

    def test_every_section_cites_a_source(self):
        for jurisdiction, year, _ in available_rule_sets():
            rule_set = load_rule_set(jurisdiction, year)
            for section, node in rule_set.values.items():
                if not isinstance(node, dict):
                    continue
                assert "_source" in node, f"{rule_set.identifier}: {section} cites no source"
                assert rule_set.source_for(section) is not None, (
                    f"{rule_set.identifier}: {section} cites an unknown source id "
                    f"{node['_source']!r}"
                )

    def test_sources_carry_enough_to_find_the_document(self):
        for jurisdiction, year, _ in available_rule_sets():
            rule_set = load_rule_set(jurisdiction, year)
            for source in rule_set.meta.sources:
                assert source.authority
                assert source.document
                assert source.tax_year == year


class TestBracketTables:
    """A malformed schedule produces plausible, wrong tax for everyone."""

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_federal_brackets_ascend_and_are_open_ended(self, federal, status):
        brackets = federal.brackets(f"ordinary_income_brackets.{status}")
        bounds = [upper for upper, _ in brackets]
        assert bounds == sorted(bounds), f"{status} bracket bounds are out of order"
        assert bounds[-1] == D("Infinity"), f"{status} has no top bracket"
        assert len(set(bounds)) == len(bounds), f"{status} has a duplicate bracket bound"

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_federal_rates_ascend_and_are_plausible(self, federal, status):
        rates = [rate for _, rate in federal.brackets(f"ordinary_income_brackets.{status}")]
        assert rates == sorted(rates), f"{status} rates are not ascending"
        assert all(0 <= rate <= 1 for rate in rates), f"{status} has a rate outside 0-100%"

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_capital_gains_rates_never_exceed_ordinary_rates(self, federal, status):
        """Preferential rates that are not preferential indicate a swapped table."""
        top_ordinary = federal.brackets(f"ordinary_income_brackets.{status}")[-1][1]
        top_capital = federal.brackets(f"capital_gains_brackets.{status}")[-1][1]
        assert top_capital < top_ordinary

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_oklahoma_brackets_ascend(self, oklahoma, status):
        brackets = oklahoma.brackets(f"tax_brackets.{status}")
        bounds = [upper for upper, _ in brackets]
        rates = [rate for _, rate in brackets]
        assert bounds == sorted(bounds)
        assert bounds[-1] == D("Infinity")
        assert rates == sorted(rates)

    def test_married_filing_separately_brackets_are_half_of_joint(self, federal):
        """IRC s.1 sets the MFS schedule at half the joint bracket widths.

        True for every bracket except the top two, where the statute diverges.
        A file that got this wrong would tax separated spouses incorrectly in a
        way no single-status test would notice.
        """
        joint = federal.brackets("ordinary_income_brackets.MARRIED_FILING_JOINTLY")
        separate = federal.brackets("ordinary_income_brackets.MARRIED_FILING_SEPARATELY")
        for (joint_bound, joint_rate), (sep_bound, sep_rate) in list(
            zip(joint, separate, strict=True)
        )[:5]:
            assert joint_rate == sep_rate
            assert sep_bound * 2 == joint_bound


class TestInternalConsistency:
    def test_eitc_maximum_matches_its_own_phase_in(self, federal):
        """max_credit must equal earned_income_amount x phase_in_rate.

        This is arithmetic within the file, not a fact about the world, so a
        mismatch is provably a transcription error. The engine caps the
        phase-in at max_credit and does not read the earned income amount, so
        without this check a wrong earned income amount would sit in the file
        unnoticed until someone relied on it.
        """
        for tier in federal.get("earned_income_credit.tiers"):
            expected = tier["earned_income_amount"] * tier["phase_in_rate"]
            actual = tier["max_credit"]
            # Published amounts are rounded to whole dollars, so allow $1.
            assert abs(expected - actual) <= 1, (
                f"EITC tier with {tier['children']} children: earned income amount "
                f"{tier['earned_income_amount']} x rate {tier['phase_in_rate']} = "
                f"{expected}, but max_credit is {actual}"
            )

    def test_eitc_tiers_cover_zero_to_three_children(self, federal):
        children = sorted(int(t["children"]) for t in federal.get("earned_income_credit.tiers"))
        assert children == [0, 1, 2, 3]

    def test_eitc_credit_grows_with_children(self, federal):
        tiers = sorted(
            federal.get("earned_income_credit.tiers"), key=lambda t: int(t["children"])
        )
        maximums = [t["max_credit"] for t in tiers]
        assert maximums == sorted(maximums)

    def test_joint_thresholds_are_at_least_unmarried_thresholds(self, federal):
        for tier in federal.get("earned_income_credit.tiers"):
            assert tier["phase_out_threshold_joint"] >= tier["phase_out_threshold_unmarried"]

    def test_every_filing_status_has_a_standard_deduction(self, federal):
        for status in ALL_STATUSES:
            assert federal.decimal(f"standard_deduction.base.{status}") > 0

    def test_joint_standard_deduction_is_twice_single(self, federal):
        single = federal.decimal("standard_deduction.base.SINGLE")
        joint = federal.decimal("standard_deduction.base.MARRIED_FILING_JOINTLY")
        assert joint == single * 2

    def test_oklahoma_standard_deduction_differs_from_federal(self, federal, oklahoma):
        """Oklahoma sets its own amounts and does not track the federal figure.

        A file that copied the federal amounts would be wrong for every
        Oklahoma taxpayer, and would look entirely reasonable.
        """
        for status in ALL_STATUSES:
            assert oklahoma.decimal(f"standard_deduction.{status}") != federal.decimal(
                f"standard_deduction.base.{status}"
            )

    def test_refundable_ctc_does_not_exceed_the_credit(self, federal):
        per_child = federal.decimal("child_tax_credit.amount_per_child")
        refundable = federal.decimal("child_tax_credit.refundable_limit_per_child")
        assert refundable <= per_child

    def test_phaseout_ranges_are_ordered(self, federal):
        for path in ("adjustments.student_loan_interest_phaseout",
                     "education_credits.aotc.phaseout",
                     "education_credits.llc.phaseout"):
            for status, band in federal.get(path).items():
                if status.startswith("_"):
                    continue
                assert band["start"] < band["end"], f"{path}.{status} ends before it starts"


class TestDecimalSafety:
    def test_no_value_is_a_float(self, federal, oklahoma):
        """A float in a rule file cannot represent a bracket edge exactly.

        The loader rejects JSON floats on read, so this asserts the property
        survived into the loaded structure.
        """

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    if not key.startswith("_"):
                        walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{path}[{index}]")
            else:
                assert not isinstance(node, float), f"{path} is a float: {node!r}"

        walk(federal.values)
        walk(oklahoma.values)


class TestLoaderSafety:
    def test_missing_rule_raises_rather_than_defaulting(self, federal):
        """A silent zero would become a wrong number on a tax return."""
        from olbostax_engine import RuleLookupError

        with pytest.raises(RuleLookupError):
            federal.decimal("standard_deduction.base.NOT_A_FILING_STATUS")

    def test_missing_year_raises_rather_than_falling_back(self):
        """A 2026 return computed with 2025 brackets is plausibly wrong."""
        from olbostax_engine import RuleLookupError

        with pytest.raises(RuleLookupError):
            load_rule_set("federal", 2099)

    def test_certification_cannot_be_flipped_at_runtime(self, federal):
        """The gate must not be reachable through the cached rule set.

        ``load_rule_set`` caches, so every caller shares one instance. If the
        metadata were mutable, any code holding a reference could promote a
        DRAFT rule set to PRODUCTION for the lifetime of the process, and every
        subsequent return would be reported as filable.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            federal.meta.certification = CertificationStatus.PRODUCTION

        with pytest.raises(ValidationError):
            federal.meta.sources[0].document = "a document nobody consulted"

        assert not load_rule_set("federal", 2025).is_filable

    def test_rule_set_container_is_frozen(self, federal):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            federal.values = {}
