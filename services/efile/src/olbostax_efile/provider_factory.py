"""Selecting an e-file provider.

The factory is where the "do not accidentally go live" guarantee is made
operational. Three separate conditions must all hold before anything but the
mock provider can be constructed, and each is checked here rather than being
assumed by configuration.
"""

from __future__ import annotations

import os

from .base import EFileProvider, NotAuthorizedToTransmit, TransmissionChannel
from .mock_provider import MockEFileProvider

__all__ = ["get_provider", "available_providers", "PROVIDER_REGISTRY"]

PROVIDER_REGISTRY: dict[str, type[EFileProvider]] = {
    "mock": MockEFileProvider,
}


def available_providers() -> dict[str, str]:
    """Providers this build can construct, and what each does."""
    return {
        "mock": (
            "Simulates the e-file lifecycle. Transmits nothing. The only provider "
            "implemented."
        ),
    }


def get_provider(name: str | None = None) -> EFileProvider:
    """Construct the configured provider.

    Defaults to the mock provider. Any other name fails, because no other
    provider exists: there is deliberately no code path in this repository that
    can transmit a return to a taxing authority.

    Adding one is not a matter of writing an adapter. It requires the
    registrations, agreements, testing and approvals tracked in
    ``COMPLIANCE_STATUS.md``, and the adapter should be added only once those
    are complete -- so that a half-finished integration cannot be enabled by
    setting an environment variable.
    """
    requested = (name or os.environ.get("OLBOSTAX_EFILE_PROVIDER") or "mock").lower()

    if requested == "mock":
        return MockEFileProvider()

    raise NotAuthorizedToTransmit(
        f"e-file provider {requested!r} is not available in this build. OlbosTax has "
        "no implemented path for transmitting returns to the IRS or the Oklahoma Tax "
        "Commission. See COMPLIANCE_STATUS.md for what must be completed first. "
        f"Available providers: {', '.join(sorted(available_providers()))}."
    )


def assert_transmission_allowed(provider: EFileProvider, environment: str) -> None:
    """Guard against a live provider in a non-production environment.

    Spec section 38: never connect development environments to production tax
    systems. A staging deployment pointed at a live transmitter would file real
    returns from test data.
    """
    if provider.channel is TransmissionChannel.LIVE and environment != "production":
        raise NotAuthorizedToTransmit(
            f"provider {provider.name!r} transmits live filings and must not be used "
            f"in the {environment!r} environment"
        )
