"""Rule-set certification -- the gate between "we wrote it down" and "we file it".

This module exists because of a specific failure mode.  A tax engine is easy to
make *look* correct: pick plausible bracket numbers, write tests that assert
the engine reproduces those same numbers, and every test passes.  The tests
prove the arithmetic is self-consistent.  They prove nothing at all about
whether the brackets match what the IRS actually published.

So the certification status of a rule set is tracked as data, independently of
whether the code works:

``DRAFT``
    Values have been transcribed but nobody has checked them against the
    primary source.  Usable for development and for showing a taxpayer an
    estimate that is clearly labelled as such.  **Never filable.**

``UNDER_REVIEW``
    A named reviewer is checking each value against the cited source.

``PRODUCTION``
    Every value has been verified against the cited primary source by a
    qualified reviewer, recorded in ``verified_by`` / ``verified_at``, and the
    regression suite passes.

:class:`~olbostax_engine.engine.TaxEngine` will happily compute with a DRAFT
rule set -- refusing would make the product undevelopable -- but it stamps the
result ``rule_sets_certified_for_filing = False``, and the e-file layer refuses
to transmit such a return.  The failure mode is a taxpayer who cannot file yet,
which is recoverable.  The alternative failure mode is a taxpayer who files a
wrong return under penalty of perjury, which is not.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["CertificationStatus", "RuleCertificationError"]


class CertificationStatus(str, Enum):
    DRAFT = "DRAFT"
    UNDER_REVIEW = "UNDER_REVIEW"
    PRODUCTION = "PRODUCTION"

    @property
    def is_filable(self) -> bool:
        return self is CertificationStatus.PRODUCTION

    @property
    def banner(self) -> str:
        """Text the UI must display when a non-production rule set is in use."""
        return {
            CertificationStatus.DRAFT: (
                "These amounts are an estimate. The tax rules for this year have not "
                "finished verification against official IRS and Oklahoma Tax Commission "
                "sources, so this return cannot be filed yet."
            ),
            CertificationStatus.UNDER_REVIEW: (
                "These amounts are an estimate. The tax rules for this year are being "
                "verified against official sources and this return cannot be filed yet."
            ),
            CertificationStatus.PRODUCTION: "",
        }[self]


class RuleCertificationError(RuntimeError):
    """Raised when filing is attempted with a rule set that is not certified."""
