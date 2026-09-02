"""OlbosTax canonical tax data schema.

This package defines the vocabulary every other component speaks: the shape of
a taxpayer, of an income document, of a return, of a computed result, and of
the trace that explains the result.  It contains no tax rules and no
calculations -- it is the noun layer, and keeping it free of verbs is what lets
the engine, the API, the validators and the e-file serializers evolve
independently.
"""

from .documents import (
    W2,
    Form1099DIV,
    Form1099G,
    Form1099INT,
    Form1099MISC,
    Form1099NEC,
    Form1099R,
    W2Box12Entry,
)
from .enums import (
    AccountType,
    CapabilityLevel,
    DependentRelationship,
    FilingStatus,
    IncomeDocumentType,
    Jurisdiction,
    RefundMethod,
    ResidencyStatus,
    ReturnStatus,
)
from .money import ZERO, Money, clamp_non_negative, money, to_cents, to_whole_dollars
from .provenance import ExtractedValue, Provenance, ValueSource
from .results import CapabilityFinding, FederalResult, OklahomaResult, TaxComputation
from .return_input import (
    AdjustmentsSection,
    CreditsSection,
    DeductionsSection,
    DirectDeposit,
    IncomeSection,
    OklahomaSection,
    PaymentsSection,
    TaxReturnInput,
)
from .sensitive import SSN, BankAccountNumber, RoutingNumber, SensitiveStr, mask_tail
from .taxpayer import Address, Dependent, Person, Spouse, Taxpayer
from .trace import CalculationTrace, StepKind, TraceStep

__version__ = "0.1.0"

__all__ = [
    "AccountType", "AdjustmentsSection", "Address", "BankAccountNumber",
    "CalculationTrace", "CapabilityFinding", "CapabilityLevel", "CreditsSection",
    "DeductionsSection", "Dependent", "DependentRelationship", "DirectDeposit",
    "ExtractedValue", "FederalResult", "FilingStatus", "Form1099DIV", "Form1099G",
    "Form1099INT", "Form1099MISC", "Form1099NEC", "Form1099R", "IncomeDocumentType",
    "IncomeSection", "Jurisdiction", "Money", "OklahomaResult", "OklahomaSection",
    "PaymentsSection", "Person", "Provenance", "RefundMethod", "ResidencyStatus",
    "ReturnStatus", "RoutingNumber", "SSN", "SensitiveStr", "Spouse", "StepKind",
    "TaxComputation", "TaxReturnInput", "Taxpayer", "TraceStep", "ValueSource",
    "W2", "W2Box12Entry", "ZERO", "clamp_non_negative", "mask_tail", "money",
    "to_cents", "to_whole_dollars", "__version__",
]
