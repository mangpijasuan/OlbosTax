"""Calculation tracing -- the mechanism behind "How was this calculated?".

A tax engine that produces only a refund number is unauditable.  When a
taxpayer, a support agent or a regulator asks why line 15 says $51,200, the
answer must be reconstructible from stored data, not from re-reading the
source code of whatever version happened to be deployed that day.

Every calculation step appends a :class:`TraceStep`.  Steps carry a stable
``code`` (safe to use as a translation key and to assert on in tests), the
human label, the amount, the inputs that fed it, and a reference to the form
line it lands on.  A :class:`CalculationTrace` is stored alongside the return
version and is what the UI renders in a calculation breakdown.

Trace steps must never contain sensitive identifiers.  They describe money and
arithmetic, not people.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .enums import Jurisdiction
from .money import ZERO

__all__ = ["StepKind", "TraceStep", "CalculationTrace"]


class StepKind(str, Enum):
    INPUT = "INPUT"
    """A value taken directly from taxpayer input."""

    SUBTOTAL = "SUBTOTAL"
    """A sum of previously recorded steps."""

    RULE_VALUE = "RULE_VALUE"
    """A constant looked up from the versioned rule set (e.g. a bracket edge)."""

    COMPUTED = "COMPUTED"
    """A derived value produced by a documented formula."""

    LIMITATION = "LIMITATION"
    """A statutory cap, floor or phase-out that changed a value."""

    RESULT = "RESULT"
    """A line the taxpayer sees on the return."""


class TraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(description='Stable identifier, e.g. "FED_AGI"')
    label: str = Field(description="Human-readable label shown in the UI")
    amount: Decimal = ZERO
    kind: StepKind = StepKind.COMPUTED
    jurisdiction: Jurisdiction = Jurisdiction.FEDERAL

    form_line: str | None = Field(
        default=None, description='Where this lands, e.g. "Form 1040, line 11"'
    )
    inputs: dict[str, Decimal] = Field(
        default_factory=dict, description="Named operands that produced ``amount``"
    )
    rule_citation: str | None = Field(
        default=None, description='Authority for the step, e.g. "IRC s.63(c)"'
    )
    detail: str | None = Field(
        default=None, description="Plain-language explanation shown to the taxpayer"
    )

    def explain(self) -> str:
        """One-line rendering used in CLI output and support tooling."""
        line = f"{self.label}: ${self.amount:,.2f}"
        if self.form_line:
            line += f"  [{self.form_line}]"
        return line


class CalculationTrace(BaseModel):
    """An ordered, append-only record of how a return was computed."""

    model_config = ConfigDict(extra="forbid")

    steps: list[TraceStep] = Field(default_factory=list)

    def add(self, step: TraceStep) -> Decimal:
        """Record ``step`` and return its amount.

        Returning the amount lets calculation code read naturally::

            agi = trace.add(TraceStep(code="FED_AGI", ..., amount=total - adjustments))

        so that recording a step and using its value cannot drift apart.
        """
        self.steps.append(step)
        return step.amount

    def get(self, code: str) -> TraceStep | None:
        for step in reversed(self.steps):
            if step.code == code:
                return step
        return None

    def amount(self, code: str, default: Decimal = ZERO) -> Decimal:
        step = self.get(code)
        return step.amount if step is not None else default

    def for_jurisdiction(self, jurisdiction: Jurisdiction) -> list[TraceStep]:
        return [s for s in self.steps if s.jurisdiction is jurisdiction]

    def breakdown(self, jurisdiction: Jurisdiction | None = None) -> list[TraceStep]:
        """Steps a taxpayer should see: results and the limitations that bound them.

        Intermediate ``RULE_VALUE`` and ``INPUT`` steps are kept in the stored
        trace for auditability but filtered out of the taxpayer-facing view,
        which is meant to read like the summary on the spec's example screen,
        not like a spreadsheet dump.
        """
        steps = self.steps if jurisdiction is None else self.for_jurisdiction(jurisdiction)
        shown = (StepKind.RESULT, StepKind.LIMITATION, StepKind.SUBTOTAL)
        return [s for s in steps if s.kind in shown]
