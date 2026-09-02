"""A development e-file provider that transmits nothing.

Its purpose is to let the whole flow -- validate, submit, poll, acknowledge,
handle a rejection, file a corrected version -- be built and tested without any
connection to a taxing authority.

It is deliberately *unhelpful* in one specific way: it can never return an
ACCEPTED acknowledgment, because :class:`Acknowledgment` refuses to construct
one on the MOCK channel. A developer wanting to exercise the accepted-return UI
uses :meth:`simulate_acceptance`, whose name makes it obvious in a diff that
the acceptance is fabricated, and which still produces an object carrying
``channel=MOCK`` so every downstream display shows the simulation disclaimer.

The rejection scenarios are modelled on the kinds of rejection that actually
dominate real e-file traffic -- name/SSN mismatches against SSA records, a
dependent already claimed on another return, a wrong prior-year AGI used to
sign -- so that the error-resolution UI is built against realistic cases
rather than invented ones.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from olbostax_schema import Jurisdiction, TaxComputation, TaxReturnInput

from .base import (
    Acknowledgment,
    EFileProvider,
    EFileValidationIssue,
    EFileValidationResult,
    SubmissionReceipt,
    SubmissionStatus,
    TransmissionChannel,
)
from .validation_rules import validate_for_efile

__all__ = ["MockEFileProvider", "MockScenario"]


class MockScenario:
    """Named outcomes a developer can force."""

    ACCEPT = "accept"
    REJECT_DEPENDENT_CLAIMED = "reject_dependent_claimed"
    REJECT_NAME_MISMATCH = "reject_name_mismatch"
    REJECT_PRIOR_YEAR_AGI = "reject_prior_year_agi"
    PENDING = "pending"


_REJECTIONS: dict[str, tuple[str, str, str]] = {
    MockScenario.REJECT_DEPENDENT_CLAIMED: (
        "IND-517-01",
        "Someone else has already claimed one of your dependents on their tax return.",
        "Check that the Social Security number and date of birth for each dependent are "
        "exactly right. If they are correct and you are entitled to claim this "
        "dependent, you will need to file on paper so the IRS can review both returns.",
    ),
    MockScenario.REJECT_NAME_MISMATCH: (
        "R0000-500-01",
        "The name and Social Security number you entered do not match Social Security "
        "Administration records.",
        "Check the spelling of your name and your Social Security number against your "
        "Social Security card, not your driver's licence. If you changed your name "
        "recently, the change may not have reached the SSA yet.",
    ),
    MockScenario.REJECT_PRIOR_YEAR_AGI: (
        "IND-031-04",
        "The prior year adjusted gross income you used to sign your return does not "
        "match IRS records.",
        "Use the adjusted gross income from your originally filed prior year return, "
        "not an amended one. If you did not file last year, enter 0.",
    ),
}


class MockEFileProvider(EFileProvider):
    """Simulates the e-file lifecycle without transmitting anything."""

    name = "olbostax-mock"

    def __init__(
        self,
        *,
        scenario: str = MockScenario.ACCEPT,
        acknowledgment_delay: timedelta = timedelta(seconds=0),
    ) -> None:
        self.scenario = scenario
        self.acknowledgment_delay = acknowledgment_delay
        self._submissions: dict[str, SubmissionReceipt] = {}
        self._returns: dict[str, tuple[TaxReturnInput, TaxComputation]] = {}

    @property
    def channel(self) -> TransmissionChannel:
        return TransmissionChannel.MOCK

    @property
    def supported_jurisdictions(self) -> frozenset[Jurisdiction]:
        return frozenset({Jurisdiction.FEDERAL, Jurisdiction.OKLAHOMA})

    # -- validation ---------------------------------------------------------

    def validate_return(
        self,
        tax_return: TaxReturnInput,
        computation: TaxComputation,
        jurisdiction: Jurisdiction,
    ) -> EFileValidationResult:
        """Run the structural checks that do not need official schemas.

        The real IRS and Oklahoma schema validation (spec sections 15 levels 4
        and 5) cannot be implemented without the current published schemas and
        business rules, which OlbosTax does not have. Rather than invent rules
        that would give false confidence, the mock runs only checks derivable
        from first principles and says so.
        """
        return validate_for_efile(
            tax_return,
            computation,
            jurisdiction,
            # Safe here and only here: this provider's channel cannot file, so
            # accepting reserved synthetic identifiers costs nothing. The flag
            # is derived from the channel rather than configured, so it cannot
            # be switched on for a path that transmits.
            allow_test_identifiers=not self.channel.is_real_filing,
        )

    # -- submission ---------------------------------------------------------

    def submit_return(
        self,
        tax_return: TaxReturnInput,
        computation: TaxComputation,
        jurisdiction: Jurisdiction,
        *,
        signature_reference: str,
        payment_reference: str,
    ) -> SubmissionReceipt:
        self.preflight(computation)

        validation = self.validate_return(tax_return, computation, jurisdiction)
        if not validation.is_valid:
            from .base import EFileError

            raise EFileError(
                "return failed validation and was not submitted: "
                + "; ".join(i.code for i in validation.errors)
            )

        submission_id = self._submission_id(tax_return, jurisdiction)
        receipt = SubmissionReceipt(
            submission_id=submission_id,
            jurisdiction=jurisdiction,
            channel=self.channel,
            status=SubmissionStatus.SUBMITTED,
            submitted_at=datetime.now(UTC),
            provider_name=self.name,
            provider_reference=f"mock-{submission_id[:12]}",
        )
        self._submissions[submission_id] = receipt
        self._returns[submission_id] = (tax_return, computation)
        return receipt

    def get_status(self, submission_id: str) -> SubmissionStatus:
        receipt = self._submissions.get(submission_id)
        if receipt is None:
            return SubmissionStatus.NOT_STARTED
        acknowledgment = self.retrieve_acknowledgment(submission_id)
        return acknowledgment.status if acknowledgment else receipt.status

    def retrieve_acknowledgment(self, submission_id: str) -> Acknowledgment | None:
        receipt = self._submissions.get(submission_id)
        if receipt is None:
            return None
        if datetime.now(UTC) - receipt.submitted_at < self.acknowledgment_delay:
            return None
        if self.scenario == MockScenario.PENDING:
            return None

        if self.scenario in _REJECTIONS:
            code, message, resolution = _REJECTIONS[self.scenario]
            return Acknowledgment(
                submission_id=submission_id,
                jurisdiction=receipt.jurisdiction,
                channel=self.channel,
                status=SubmissionStatus.REJECTED,
                received_at=datetime.now(UTC),
                rejection_codes=[code],
                rejection_issues=[
                    EFileValidationIssue(
                        code=code,
                        severity="ERROR",
                        jurisdiction=receipt.jurisdiction,
                        taxpayer_message=message,
                        resolution=resolution,
                    )
                ],
            )

        # The ACCEPT scenario cannot produce an accepted acknowledgment: the
        # Acknowledgment model forbids it on a non-live channel. What comes
        # back is a SUBMITTED acknowledgment, which is the honest answer --
        # the return was handed over and nothing has accepted it, because
        # nothing real is on the other end.
        return Acknowledgment(
            submission_id=submission_id,
            jurisdiction=receipt.jurisdiction,
            channel=self.channel,
            status=SubmissionStatus.SUBMITTED,
            received_at=datetime.now(UTC),
        )

    # -- development helper -------------------------------------------------

    def simulate_acceptance(self, submission_id: str) -> Acknowledgment:
        """Produce an acceptance-shaped acknowledgment for UI development.

        Named to be conspicuous. The returned object still carries
        ``channel=MOCK``, so anything that renders it shows the simulation
        disclaimer, and the status stays SUBMITTED rather than ACCEPTED --
        because the model will not allow otherwise, and that restriction is
        the point rather than an obstacle to route around.
        """
        receipt = self._submissions[submission_id]
        return Acknowledgment(
            submission_id=submission_id,
            jurisdiction=receipt.jurisdiction,
            channel=TransmissionChannel.MOCK,
            status=SubmissionStatus.SUBMITTED,
            received_at=datetime.now(UTC),
            raw_payload_reference="simulated-for-development",
        )

    @staticmethod
    def _submission_id(tax_return: TaxReturnInput, jurisdiction: Jurisdiction) -> str:
        """A deterministic, non-identifying submission id.

        Derived from a hash rather than from the taxpayer's details so that the
        id itself never carries an SSN into a URL, a log line or a support
        ticket.
        """
        seed = f"{tax_return.taxpayer.ssn.reveal()}|{tax_return.tax_year}|{jurisdiction.value}|{datetime.now(UTC).isoformat()}"
        return hashlib.sha256(seed.encode()).hexdigest()[:32]
