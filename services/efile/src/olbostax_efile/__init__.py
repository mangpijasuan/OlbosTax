"""E-file abstraction.

OlbosTax prepares returns. Transmitting them to a taxing authority is a
separate, regulated activity performed through an authorised provider. This
package is the boundary between the two, and it is written so that the
distinction cannot be blurred by accident: see ``base.py`` for the invariants
that make a simulated acceptance impossible to construct.
"""

from .base import (
    Acknowledgment,
    EFileError,
    EFileProvider,
    EFileValidationIssue,
    EFileValidationResult,
    NotAuthorizedToTransmit,
    SubmissionReceipt,
    SubmissionStatus,
    TransmissionChannel,
)
from .mock_provider import MockEFileProvider, MockScenario
from .provider_factory import (
    PROVIDER_REGISTRY,
    assert_transmission_allowed,
    available_providers,
    get_provider,
)
from .validation_rules import (
    VALIDATOR_VERSION,
    unimplemented_validation_levels,
    validate_for_efile,
)

__all__ = [
    "Acknowledgment", "EFileError", "EFileProvider", "EFileValidationIssue",
    "EFileValidationResult", "MockEFileProvider", "MockScenario",
    "NotAuthorizedToTransmit", "PROVIDER_REGISTRY", "SubmissionReceipt",
    "SubmissionStatus", "TransmissionChannel", "VALIDATOR_VERSION",
    "assert_transmission_allowed", "available_providers", "get_provider",
    "unimplemented_validation_levels", "validate_for_efile",
]
