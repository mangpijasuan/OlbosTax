"""The e-file provider interface.

OlbosTax does not transmit returns to the IRS or the Oklahoma Tax Commission.
Doing so requires registrations, agreements, testing and approvals that
Olbos Technologies, LLC has not represented as complete -- see
``COMPLIANCE_STATUS.md``. Everything downstream of this interface is therefore
an *adapter* to whoever is authorised to transmit, and the interface exists so
that the rest of the product never needs to know which.

The critical rule this module enforces in code, not just in documentation:

    **A mock submission is never represented as a real one.**

Every submission and acknowledgment carries a ``transmission_channel`` that
says whether it went anywhere real. ``ReturnStatus.ACCEPTED`` may only be set
from an acknowledgment whose channel is ``LIVE``. A developer running the mock
provider cannot produce an object that claims a return was accepted by the
IRS, because the constructor rejects it.

That check lives here rather than in a review checklist because the failure it
prevents -- telling a taxpayer their return was accepted when nothing was
filed, so they discover in November that they never filed at all -- is exactly
the kind that a reasonable-looking refactor introduces.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from olbostax_schema import Jurisdiction, TaxComputation, TaxReturnInput

__all__ = [
    "TransmissionChannel",
    "SubmissionStatus",
    "EFileValidationIssue",
    "EFileValidationResult",
    "SubmissionReceipt",
    "Acknowledgment",
    "EFileProvider",
    "EFileError",
    "NotAuthorizedToTransmit",
]


class EFileError(RuntimeError):
    """Base class for e-file failures."""


class NotAuthorizedToTransmit(EFileError):
    """Raised when transmission is attempted without the required authority.

    This is not an error condition to be handled and retried. It means the
    software attempted something Olbos Technologies is not permitted to do.
    """


class TransmissionChannel(str, Enum):
    MOCK = "MOCK"
    """Nothing left this system. For development and automated tests."""

    TEST = "TEST"
    """Sent to an authorised provider's test environment (ATS or equivalent).
    Real protocol, real validation, no real filing."""

    LIVE = "LIVE"
    """Transmitted for actual filing through an authorised provider."""

    @property
    def is_real_filing(self) -> bool:
        return self is TransmissionChannel.LIVE

    @property
    def disclaimer(self) -> str:
        """Text the UI must show alongside any status from this channel."""
        return {
            TransmissionChannel.MOCK: (
                "SIMULATED. This return was not sent to the IRS or the Oklahoma Tax "
                "Commission. Nothing has been filed."
            ),
            TransmissionChannel.TEST: (
                "TEST SUBMISSION. This return was sent to a test system for validation "
                "only. Nothing has been filed."
            ),
            TransmissionChannel.LIVE: "",
        }[self]


class SubmissionStatus(str, Enum):
    """Spec section 31 statuses, per jurisdiction."""

    NOT_STARTED = "NOT_STARTED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

    @property
    def is_terminal(self) -> bool:
        return self in (SubmissionStatus.ACCEPTED, SubmissionStatus.REJECTED)


class EFileValidationIssue(BaseModel):
    """One problem found before transmission.

    ``taxpayer_message`` is required and separate from ``technical_detail``
    because spec section 16 forbids showing raw schema errors to taxpayers. A
    rule that says ``/Return/ReturnData/IRS1040/DependentDetail[2]/DependentSSN
    failed pattern`` is not something a person can act on.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str = Field(pattern="^(ERROR|WARNING)$")
    jurisdiction: Jurisdiction
    taxpayer_message: str
    resolution: str = ""
    field_path: str | None = None
    technical_detail: str | None = None

    @property
    def blocks_submission(self) -> bool:
        return self.severity == "ERROR"


class EFileValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[EFileValidationIssue] = Field(default_factory=list)
    validated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    validator_version: str = "unknown"

    @property
    def errors(self) -> list[EFileValidationIssue]:
        return [i for i in self.issues if i.blocks_submission]

    @property
    def warnings(self) -> list[EFileValidationIssue]:
        return [i for i in self.issues if not i.blocks_submission]

    @property
    def is_valid(self) -> bool:
        return not self.errors


class SubmissionReceipt(BaseModel):
    """Proof that a return was handed to a transmitter. Not proof of acceptance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: str
    jurisdiction: Jurisdiction
    channel: TransmissionChannel
    status: SubmissionStatus
    submitted_at: datetime
    provider_name: str
    provider_reference: str | None = None

    @model_validator(mode="after")
    def _receipt_cannot_claim_acceptance(self) -> SubmissionReceipt:
        """A receipt records handoff, never outcome.

        Acceptance comes from an acknowledgment returned by the taxing
        authority, which arrives minutes to days later. A receipt that claimed
        ACCEPTED would be asserting an outcome nobody has reported yet.
        """
        if self.status in (SubmissionStatus.ACCEPTED, SubmissionStatus.REJECTED):
            raise ValueError(
                "a submission receipt cannot carry a terminal status; acceptance and "
                "rejection come from an Acknowledgment"
            )
        return self

    @property
    def is_real_filing(self) -> bool:
        return self.channel.is_real_filing


class Acknowledgment(BaseModel):
    """The taxing authority's response to a submitted return."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: str
    jurisdiction: Jurisdiction
    channel: TransmissionChannel
    status: SubmissionStatus
    received_at: datetime

    authority_reference: str | None = Field(
        default=None,
        description=(
            "The submission identifier assigned by the taxing authority. Present "
            "only for a LIVE acknowledgment: it is the evidence that a filing "
            "actually occurred."
        ),
    )
    rejection_codes: list[str] = Field(default_factory=list)
    rejection_issues: list[EFileValidationIssue] = Field(default_factory=list)
    raw_payload_reference: str | None = Field(
        default=None,
        description="Pointer to the stored provider response, for audit and dispute",
    )

    @model_validator(mode="after")
    def _only_live_channels_may_report_acceptance(self) -> Acknowledgment:
        """The central safety invariant of the e-file layer.

        Spec section 47: never mark returns accepted without a real
        acknowledgment. Enforcing it in the constructor means no code path --
        a seeded fixture, a demo script, a test helper, a refactor that got the
        channel argument wrong -- can produce an object asserting that the IRS
        accepted a return when it did not.
        """
        if self.status is SubmissionStatus.ACCEPTED and not self.channel.is_real_filing:
            raise ValueError(
                f"an acknowledgment on the {self.channel.value} channel cannot report "
                "ACCEPTED; only a live transmission to a taxing authority can accept a "
                "return"
            )
        if self.status is SubmissionStatus.ACCEPTED and not self.authority_reference:
            raise ValueError(
                "an accepted acknowledgment must carry the authority's reference; "
                "without it there is no evidence the return was filed"
            )
        return self

    @property
    def is_real_filing(self) -> bool:
        return self.channel.is_real_filing

    def taxpayer_explanation(self) -> str:
        """Plain-language status, per spec section 31."""
        if not self.channel.is_real_filing:
            return self.channel.disclaimer
        if self.status is SubmissionStatus.ACCEPTED:
            authority = (
                "the IRS" if self.jurisdiction is Jurisdiction.FEDERAL
                else "the Oklahoma Tax Commission"
            )
            return f"Your return was accepted by {authority}."
        if self.status is SubmissionStatus.REJECTED:
            if self.rejection_issues:
                return self.rejection_issues[0].taxpayer_message
            return (
                "Your return was rejected. We are reviewing the reason and will tell "
                "you what needs to change."
            )
        return "Your return has been submitted and we are waiting for a response."


class EFileProvider(abc.ABC):
    """The interface every transmission path implements.

    Implementations must not be interchangeable in a way that hides which is in
    use: :attr:`channel` is part of the interface precisely so callers can
    check, and so it can be recorded on every receipt and acknowledgment.
    """

    name: str = "unnamed-provider"

    @property
    @abc.abstractmethod
    def channel(self) -> TransmissionChannel:
        """Whether this provider actually files returns."""

    @property
    @abc.abstractmethod
    def supported_jurisdictions(self) -> frozenset[Jurisdiction]:
        ...

    @abc.abstractmethod
    def validate_return(
        self,
        tax_return: TaxReturnInput,
        computation: TaxComputation,
        jurisdiction: Jurisdiction,
    ) -> EFileValidationResult:
        """Check the return against the authority's e-file rules before sending."""

    @abc.abstractmethod
    def submit_return(
        self,
        tax_return: TaxReturnInput,
        computation: TaxComputation,
        jurisdiction: Jurisdiction,
        *,
        signature_reference: str,
        payment_reference: str,
    ) -> SubmissionReceipt:
        """Transmit the return. Callers must have verified payment and signature."""

    @abc.abstractmethod
    def get_status(self, submission_id: str) -> SubmissionStatus:
        ...

    @abc.abstractmethod
    def retrieve_acknowledgment(self, submission_id: str) -> Acknowledgment | None:
        """Fetch the authority's response, or ``None`` if it has not arrived."""

    def preflight(self, computation: TaxComputation) -> None:
        """Gate every submission on the engine's own assessment of the return.

        Runs before any provider-specific work. Two conditions stop a return
        here:

        *   The rule sets used are not certified for filing. Transmitting a
            return computed from unverified tax parameters would put a wrong
            number in front of a taxing authority under the taxpayer's
            signature.
        *   The engine raised a blocking capability finding, meaning it knows
            it could not compute some part of this return correctly.
        """
        if not computation.rule_sets_certified_for_filing:
            raise NotAuthorizedToTransmit(
                "the tax rule sets used to compute this return are not certified for "
                f"filing (federal {computation.federal_rule_version}, oklahoma "
                f"{computation.oklahoma_rule_version}); see COMPLIANCE_STATUS.md"
            )
        blocking = computation.blocking_findings
        if blocking:
            raise NotAuthorizedToTransmit(
                "this return contains situations OlbosTax cannot file: "
                + "; ".join(f.title for f in blocking)
            )
