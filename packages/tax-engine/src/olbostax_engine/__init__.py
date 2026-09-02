"""OlbosTax deterministic tax engine.

Separated from the UI, the API and the e-file layer on purpose: the engine is
the component whose correctness a taxpayer's liability depends on, and it is
kept small, pure and testable so that it can be reviewed by someone who knows
tax law but not web frameworks.
"""

from .capability import CAPABILITY_MATRIX, assess_capability
from .engine import TaxEngine, compute
from .rules import (
    CertificationStatus,
    RuleCertificationError,
    RuleLookupError,
    RuleSet,
    available_rule_sets,
    load_rule_set,
)
from .version import ENGINE_VERSION

__all__ = [
    "CAPABILITY_MATRIX",
    "CertificationStatus",
    "ENGINE_VERSION",
    "RuleCertificationError",
    "RuleLookupError",
    "RuleSet",
    "TaxEngine",
    "assess_capability",
    "available_rule_sets",
    "compute",
    "load_rule_set",
]
