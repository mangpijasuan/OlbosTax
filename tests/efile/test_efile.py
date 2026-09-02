"""E-file layer tests.

The most important tests in this file assert what the system *refuses* to do.
A tax product that occasionally tells a taxpayer their return was accepted when
nothing was filed causes far more harm than one that occasionally refuses to
file a return it could have filed, so the safety invariants are tested harder
than the happy path.
"""

from datetime import UTC, datetime

import pytest
from olbostax_efile import (
    Acknowledgment,
    EFileError,
    MockEFileProvider,
    MockScenario,
    NotAuthorizedToTransmit,
    SubmissionReceipt,
    SubmissionStatus,
    TransmissionChannel,
    assert_transmission_allowed,
    get_provider,
    unimplemented_validation_levels,
)
from olbostax_engine import compute
from olbostax_schema import Jurisdiction

from tests.fixtures.synthetic_taxpayers import synthetic_taxpayer_001


@pytest.fixture
def computation():
    return compute(synthetic_taxpayer_001())


def certified(computation):
    """A copy of ``computation`` as it would look with verified rule sets.

    The synthetic returns are computed against DRAFT rule sets, so preflight
    correctly refuses to submit them. Tests that need to exercise the
    *submission* path rather than the certification gate use this to stand in
    for the state the system will be in once the rules are verified.
    """
    return computation.model_copy(
        update={"rule_sets_certified_for_filing": True, "findings": []}
    )


@pytest.fixture
def tax_return():
    return synthetic_taxpayer_001()


class TestSafetyInvariants:
    """The rules that must hold no matter what the rest of the system does."""

    def test_mock_channel_cannot_report_acceptance(self):
        """The core guarantee: a simulation cannot claim a real filing."""
        with pytest.raises(ValueError, match="cannot report ACCEPTED"):
            Acknowledgment(
                submission_id="s1",
                jurisdiction=Jurisdiction.FEDERAL,
                channel=TransmissionChannel.MOCK,
                status=SubmissionStatus.ACCEPTED,
                received_at=datetime.now(UTC),
                authority_reference="fake",
            )

    def test_test_channel_cannot_report_acceptance(self):
        """A provider's test environment does not file returns either."""
        with pytest.raises(ValueError, match="cannot report ACCEPTED"):
            Acknowledgment(
                submission_id="s1",
                jurisdiction=Jurisdiction.FEDERAL,
                channel=TransmissionChannel.TEST,
                status=SubmissionStatus.ACCEPTED,
                received_at=datetime.now(UTC),
                authority_reference="ats-123",
            )

    def test_acceptance_requires_an_authority_reference(self):
        """Without the authority's identifier there is no evidence of filing."""
        with pytest.raises(ValueError, match="authority's reference"):
            Acknowledgment(
                submission_id="s1",
                jurisdiction=Jurisdiction.FEDERAL,
                channel=TransmissionChannel.LIVE,
                status=SubmissionStatus.ACCEPTED,
                received_at=datetime.now(UTC),
            )

    def test_live_acceptance_with_reference_is_permitted(self):
        """The one combination that may assert a filed return."""
        ack = Acknowledgment(
            submission_id="s1",
            jurisdiction=Jurisdiction.FEDERAL,
            channel=TransmissionChannel.LIVE,
            status=SubmissionStatus.ACCEPTED,
            received_at=datetime.now(UTC),
            authority_reference="12345620250101abcdef",
        )
        assert ack.is_real_filing
        assert "accepted by the IRS" in ack.taxpayer_explanation()

    def test_receipt_cannot_claim_a_terminal_status(self):
        """Handing a return to a transmitter is not an outcome."""
        with pytest.raises(ValueError, match="terminal status"):
            SubmissionReceipt(
                submission_id="s1",
                jurisdiction=Jurisdiction.FEDERAL,
                channel=TransmissionChannel.LIVE,
                status=SubmissionStatus.ACCEPTED,
                submitted_at=datetime.now(UTC),
                provider_name="p",
            )

    def test_mock_acknowledgment_carries_a_disclaimer(self):
        ack = Acknowledgment(
            submission_id="s1",
            jurisdiction=Jurisdiction.FEDERAL,
            channel=TransmissionChannel.MOCK,
            status=SubmissionStatus.SUBMITTED,
            received_at=datetime.now(UTC),
        )
        explanation = ack.taxpayer_explanation()
        assert "not sent to the IRS" in explanation
        assert "Nothing has been filed" in explanation

    def test_simulate_acceptance_still_cannot_claim_acceptance(self, tax_return, computation):
        """Even the explicit development helper cannot fabricate a filing."""
        provider = MockEFileProvider()
        receipt = provider.submit_return(
            tax_return, certified(computation), Jurisdiction.FEDERAL,
            signature_reference="sig", payment_reference="pay",
        )
        ack = provider.simulate_acceptance(receipt.submission_id)
        assert ack.status is not SubmissionStatus.ACCEPTED
        assert ack.channel is TransmissionChannel.MOCK


class TestPreflightGate:
    def test_uncertified_rules_block_submission(self, tax_return, computation):
        """A return computed from unverified tax rules must not be transmitted."""
        assert not computation.rule_sets_certified_for_filing
        provider = MockEFileProvider()
        with pytest.raises(NotAuthorizedToTransmit, match="not certified for filing"):
            provider.submit_return(
                tax_return, computation, Jurisdiction.FEDERAL,
                signature_reference="sig", payment_reference="pay",
            )

    def test_blocking_capability_finding_blocks_submission(self, tax_return, computation):
        from olbostax_schema import CapabilityFinding, CapabilityLevel

        blocked = computation.model_copy(
            update={
                "rule_sets_certified_for_filing": True,
                "findings": [
                    CapabilityFinding(
                        code="income.rental",
                        level=CapabilityLevel.NOT_SUPPORTED,
                        title="Rental income is not supported",
                        explanation="x",
                    )
                ],
            }
        )
        with pytest.raises(NotAuthorizedToTransmit, match="cannot file"):
            MockEFileProvider().submit_return(
                tax_return, blocked, Jurisdiction.FEDERAL,
                signature_reference="sig", payment_reference="pay",
            )


class TestProviderFactory:
    def test_defaults_to_mock(self):
        assert get_provider().channel is TransmissionChannel.MOCK

    def test_unknown_provider_refuses_rather_than_falling_back(self):
        """Silently falling back to mock would be worse than failing."""
        with pytest.raises(NotAuthorizedToTransmit, match="not available in this build"):
            get_provider("irs-direct")

    def test_no_live_provider_exists_in_this_build(self):
        from olbostax_efile import available_providers

        for name in available_providers():
            assert get_provider(name).channel is not TransmissionChannel.LIVE

    def test_live_provider_rejected_outside_production(self):
        class FakeLive(MockEFileProvider):
            @property
            def channel(self):
                return TransmissionChannel.LIVE

        with pytest.raises(NotAuthorizedToTransmit, match="must not be used"):
            assert_transmission_allowed(FakeLive(), "staging")


class TestSubmissionLifecycle:
    @pytest.fixture
    def ready(self, computation):
        return certified(computation)

    def test_submit_then_poll(self, tax_return, ready):
        provider = MockEFileProvider()
        receipt = provider.submit_return(
            tax_return, ready, Jurisdiction.FEDERAL,
            signature_reference="sig", payment_reference="pay",
        )
        assert receipt.status is SubmissionStatus.SUBMITTED
        assert not receipt.is_real_filing
        assert provider.get_status(receipt.submission_id) is SubmissionStatus.SUBMITTED

    def test_unknown_submission_reports_not_started(self):
        assert MockEFileProvider().get_status("nope") is SubmissionStatus.NOT_STARTED

    def test_rejection_scenario_explains_in_plain_language(self, tax_return, ready):
        provider = MockEFileProvider(scenario=MockScenario.REJECT_DEPENDENT_CLAIMED)
        receipt = provider.submit_return(
            tax_return, ready, Jurisdiction.FEDERAL,
            signature_reference="sig", payment_reference="pay",
        )
        ack = provider.retrieve_acknowledgment(receipt.submission_id)
        assert ack.status is SubmissionStatus.REJECTED
        assert ack.rejection_codes == ["IND-517-01"]
        issue = ack.rejection_issues[0]
        # Spec section 16: taxpayers see plain language, never schema errors.
        assert "already claimed" in issue.taxpayer_message
        assert issue.resolution
        assert "<" not in issue.taxpayer_message

    def test_submission_id_does_not_contain_the_ssn(self, tax_return, ready):
        provider = MockEFileProvider()
        receipt = provider.submit_return(
            tax_return, ready, Jurisdiction.FEDERAL,
            signature_reference="sig", payment_reference="pay",
        )
        assert tax_return.taxpayer.ssn.reveal() not in receipt.submission_id
        assert receipt.submission_id.isalnum()

    def test_validation_failure_prevents_submission(self, tax_return, ready):
        tax_return.income.w2s[0].box2_federal_income_tax_withheld = (
            tax_return.income.w2s[0].box1_wages * 2
        )
        with pytest.raises(EFileError, match="failed validation"):
            MockEFileProvider().submit_return(
                tax_return, ready, Jurisdiction.FEDERAL,
                signature_reference="sig", payment_reference="pay",
            )


class TestValidation:
    def test_clean_return_validates(self, tax_return, computation):
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert result.is_valid, [i.code for i in result.errors]

    def test_transposed_w2_boxes_caught(self, tax_return, computation):
        w2 = tax_return.income.w2s[0]
        w2.box1_wages, w2.box2_federal_income_tax_withheld = (
            w2.box2_federal_income_tax_withheld,
            w2.box1_wages,
        )
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert "OT-W2-WITHHOLDING-EXCEEDS-WAGES" in {i.code for i in result.errors}

    def test_duplicate_ssn_caught(self, tax_return, computation):
        from datetime import date

        from olbostax_schema import Dependent, DependentRelationship

        tax_return.dependents = [
            Dependent(
                first_name="Copy", last_name="Whitfield",
                ssn=tax_return.taxpayer.ssn.reveal(),
                date_of_birth=date(2015, 1, 1),
                relationship=DependentRelationship.SON,
            )
        ]
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert "OT-SSN-DUPLICATE" in {i.code for i in result.errors}

    def test_structurally_impossible_ssn_caught(self, tax_return, computation):
        tax_return.taxpayer.ssn = "666123456"
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert "OT-SSN-STRUCTURE" in {i.code for i in result.errors}

    def test_unverified_extraction_blocks_filing(self, tax_return, computation):
        """Spec section 9: a human must confirm machine-read values."""
        from olbostax_schema import Provenance, ValueSource

        tax_return.income.w2s[0].provenance = Provenance(
            source=ValueSource.DOCUMENT_EXTRACTION,
            source_document_id="doc-1",
            source_field="W2_BOX_1",
            confidence=0.99,
            verified_by_user=False,
        )
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert "OT-UNVERIFIED-EXTRACTION" in {i.code for i in result.errors}

    def test_confirmed_extraction_does_not_block(self, tax_return, computation):
        from olbostax_schema import Provenance, ValueSource

        tax_return.income.w2s[0].provenance = Provenance(
            source=ValueSource.DOCUMENT_EXTRACTION,
            source_document_id="doc-1",
            source_field="W2_BOX_1",
            confidence=0.99,
            verified_by_user=True,
        )
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        assert "OT-UNVERIFIED-EXTRACTION" not in {i.code for i in result.errors}

    def test_every_issue_has_a_taxpayer_message(self, tax_return, computation):
        """No raw schema errors reach the taxpayer (spec section 16)."""
        tax_return.taxpayer.ssn = "000123456"
        result = MockEFileProvider().validate_return(
            tax_return, computation, Jurisdiction.FEDERAL
        )
        for issue in result.issues:
            assert issue.taxpayer_message
            assert not issue.taxpayer_message.startswith("/")

    def test_unimplemented_levels_are_declared(self):
        """The gap must be discoverable without reading the source."""
        levels = unimplemented_validation_levels()
        assert {entry["level"] for entry in levels} == {"4", "5"}
        for entry in levels:
            assert entry["status"] == "NOT_IMPLEMENTED"
            assert entry["reason"]
            assert entry["consequence"]
