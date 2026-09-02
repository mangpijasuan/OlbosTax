"""Provenance: where a number on the return came from, and who vouched for it.

Section 9 of the product spec requires that every extracted value carry its
source document, source field, a confidence score and whether a human has
confirmed it.  Provenance is the mechanism, and it exists for three reasons:

1.  The tax engine must be able to refuse to file a return that still contains
    machine-extracted values no human has looked at.  OCR at 0.98 confidence is
    still a guess, and the taxpayer signs the return, not the model.
2.  When a taxpayer asks "where did this number come from?", the answer is a
    lookup, not an investigation.
3.  When an extraction model is later found to misread a particular box, the
    affected returns can be identified by querying provenance rather than
    re-running every return.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ValueSource", "Provenance", "ExtractedValue"]


class ValueSource(str, Enum):
    USER_ENTERED = "USER_ENTERED"
    """A human typed it. Trusted as the taxpayer's own assertion."""

    DOCUMENT_EXTRACTION = "DOCUMENT_EXTRACTION"
    """OCR / document AI produced it. Requires user confirmation before filing."""

    PRIOR_YEAR_CARRYFORWARD = "PRIOR_YEAR_CARRYFORWARD"
    """Copied from a prior-year return held by OlbosTax."""

    CALCULATED = "CALCULATED"
    """Derived by the deterministic tax engine from other values."""

    DEFAULTED = "DEFAULTED"
    """Left blank by the taxpayer and defaulted to zero."""


class Provenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ValueSource = ValueSource.USER_ENTERED
    source_document_id: str | None = None
    source_field: str | None = Field(
        default=None, description='Machine field name, e.g. "W2_BOX_1"'
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Extractor confidence. None for values a human typed.",
    )
    verified_by_user: bool = False
    verified_at: datetime | None = None

    @property
    def requires_user_verification(self) -> bool:
        """True when this value may not be filed without human confirmation.

        Only machine-extracted values need confirming.  A number the taxpayer
        typed is already their assertion, and asking them to confirm their own
        typing is the kind of friction that trains people to click through
        confirmations without reading them.
        """
        return self.source is ValueSource.DOCUMENT_EXTRACTION and not self.verified_by_user


class ExtractedValue(BaseModel):
    """A single value produced by document extraction, before it is accepted.

    This is the wire format between the document-processing service and the
    interview UI.  It is intentionally *not* the format the tax engine
    consumes: values become part of the return only after the taxpayer
    confirms them, at which point they are written into the typed document
    models with the provenance attached.
    """

    model_config = ConfigDict(extra="forbid")

    value: Decimal | str | bool | None
    source_document_id: str
    source_field: str
    confidence: float = Field(ge=0.0, le=1.0)
    verified_by_user: bool = False
    page: int | None = None
    bounding_box: tuple[float, float, float, float] | None = Field(
        default=None, description="(x0, y0, x1, y1) in page-relative units, for highlighting"
    )

    def to_provenance(self) -> Provenance:
        return Provenance(
            source=ValueSource.DOCUMENT_EXTRACTION,
            source_document_id=self.source_document_id,
            source_field=self.source_field,
            confidence=self.confidence,
            verified_by_user=self.verified_by_user,
        )
