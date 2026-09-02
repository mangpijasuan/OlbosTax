"""Federal individual income tax calculation (Form 1040 and schedules)."""

from .calculator import calculate_federal_return
from .dependents import DependentStatus, classify_dependents, head_of_household_supported

__all__ = [
    "DependentStatus",
    "calculate_federal_return",
    "classify_dependents",
    "head_of_household_supported",
]
