"""Enumerations shared across the tax schema, engine, API and UI."""

from __future__ import annotations

from enum import Enum

__all__ = [
    "FilingStatus",
    "ResidencyStatus",
    "DependentRelationship",
    "IncomeDocumentType",
    "ReturnStatus",
    "Jurisdiction",
    "CapabilityLevel",
    "AccountType",
    "RefundMethod",
]


class FilingStatus(str, Enum):
    SINGLE = "SINGLE"
    MARRIED_FILING_JOINTLY = "MARRIED_FILING_JOINTLY"
    MARRIED_FILING_SEPARATELY = "MARRIED_FILING_SEPARATELY"
    HEAD_OF_HOUSEHOLD = "HEAD_OF_HOUSEHOLD"
    QUALIFYING_SURVIVING_SPOUSE = "QUALIFYING_SURVIVING_SPOUSE"

    @property
    def is_joint(self) -> bool:
        return self is FilingStatus.MARRIED_FILING_JOINTLY

    @property
    def is_married(self) -> bool:
        return self in (
            FilingStatus.MARRIED_FILING_JOINTLY,
            FilingStatus.MARRIED_FILING_SEPARATELY,
        )

    @property
    def label(self) -> str:
        return {
            FilingStatus.SINGLE: "Single",
            FilingStatus.MARRIED_FILING_JOINTLY: "Married filing jointly",
            FilingStatus.MARRIED_FILING_SEPARATELY: "Married filing separately",
            FilingStatus.HEAD_OF_HOUSEHOLD: "Head of household",
            FilingStatus.QUALIFYING_SURVIVING_SPOUSE: "Qualifying surviving spouse",
        }[self]


class ResidencyStatus(str, Enum):
    """State residency for the tax year.

    Only ``FULL_YEAR_RESIDENT`` is supported for Oklahoma in the MVP.  Part-year
    and nonresident returns use Form 511-NR, whose allocation rules are not
    implemented; see ``packages/tax-engine/.../capability.py``.
    """

    FULL_YEAR_RESIDENT = "FULL_YEAR_RESIDENT"
    PART_YEAR_RESIDENT = "PART_YEAR_RESIDENT"
    NONRESIDENT = "NONRESIDENT"


class DependentRelationship(str, Enum):
    SON = "SON"
    DAUGHTER = "DAUGHTER"
    STEPCHILD = "STEPCHILD"
    FOSTER_CHILD = "FOSTER_CHILD"
    GRANDCHILD = "GRANDCHILD"
    BROTHER = "BROTHER"
    SISTER = "SISTER"
    NIECE = "NIECE"
    NEPHEW = "NEPHEW"
    PARENT = "PARENT"
    GRANDPARENT = "GRANDPARENT"
    AUNT = "AUNT"
    UNCLE = "UNCLE"
    OTHER = "OTHER"

    @property
    def is_qualifying_child_relationship(self) -> bool:
        """Relationship test of IRC s.152(c)(2) -- relationship only.

        Age, residency, support and joint-return tests are applied separately
        in the dependent qualification rules.
        """
        return self in {
            DependentRelationship.SON,
            DependentRelationship.DAUGHTER,
            DependentRelationship.STEPCHILD,
            DependentRelationship.FOSTER_CHILD,
            DependentRelationship.GRANDCHILD,
            DependentRelationship.BROTHER,
            DependentRelationship.SISTER,
            DependentRelationship.NIECE,
            DependentRelationship.NEPHEW,
        }


class IncomeDocumentType(str, Enum):
    W2 = "W2"
    FORM_1099_INT = "FORM_1099_INT"
    FORM_1099_DIV = "FORM_1099_DIV"
    FORM_1099_NEC = "FORM_1099_NEC"
    FORM_1099_MISC = "FORM_1099_MISC"
    FORM_1099_R = "FORM_1099_R"
    FORM_1099_G = "FORM_1099_G"
    OTHER = "OTHER"


class Jurisdiction(str, Enum):
    FEDERAL = "FEDERAL"
    OKLAHOMA = "OK"


class ReturnStatus(str, Enum):
    """Lifecycle of a single immutable return version."""

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    READY_FOR_FILING = "READY_FOR_FILING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"

    @property
    def is_finalized(self) -> bool:
        """A finalized version is immutable; edits fork a new version."""
        return self in {
            ReturnStatus.SUBMITTED,
            ReturnStatus.ACCEPTED,
            ReturnStatus.REJECTED,
            ReturnStatus.SUPERSEDED,
        }


class CapabilityLevel(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    REQUIRES_TAX_PROFESSIONAL = "REQUIRES_TAX_PROFESSIONAL"

    @property
    def blocks_filing(self) -> bool:
        return self in {
            CapabilityLevel.NOT_SUPPORTED,
            CapabilityLevel.REQUIRES_TAX_PROFESSIONAL,
        }


class AccountType(str, Enum):
    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"


class RefundMethod(str, Enum):
    DIRECT_DEPOSIT = "DIRECT_DEPOSIT"
    PAPER_CHECK = "PAPER_CHECK"
    APPLY_TO_NEXT_YEAR = "APPLY_TO_NEXT_YEAR"
