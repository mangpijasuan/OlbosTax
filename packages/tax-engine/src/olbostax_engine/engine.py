"""The public entry point to the OlbosTax tax engine.

One function, :func:`compute`, takes a return and produces a
:class:`~olbostax_schema.results.TaxComputation` containing federal and
Oklahoma results, the full calculation trace, capability findings, and the
version stamps needed to reproduce the run.

The engine is deterministic and side-effect free.  It does not read a database,
call a network service, consult a language model, or look at the clock for
anything except the ``computed_at`` stamp.  Given the same input and the same
rule versions it produces byte-identical output, which is what makes the
regression suite meaningful and what lets a stored return be recomputed years
later to answer a question about it.

**No language model participates in any calculation on this path.**  The AI
assistant explains results the engine produced; it never produces them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from olbostax_schema import TaxComputation, TaxReturnInput
from olbostax_schema.trace import CalculationTrace

from .capability import assess_capability
from .federal import calculate_federal_return, classify_dependents
from .oklahoma import calculate_oklahoma_return
from .rules import CertificationStatus, RuleSet, load_rule_set
from .version import ENGINE_VERSION

__all__ = ["compute", "TaxEngine"]


class TaxEngine:
    """Computes returns against a fixed set of rule versions.

    Instantiating with explicit rule sets is what makes "recompute this 2025
    return exactly as it was computed in April" possible: the caller loads the
    stored rule versions and passes them in, rather than picking up whatever is
    current.
    """

    def __init__(
        self,
        federal_rules: RuleSet,
        oklahoma_rules: RuleSet | None = None,
    ) -> None:
        self.federal_rules = federal_rules
        self.oklahoma_rules = oklahoma_rules

    @classmethod
    def for_tax_year(cls, tax_year: int, *, include_oklahoma: bool = True) -> TaxEngine:
        federal = load_rule_set("federal", tax_year)
        oklahoma = load_rule_set("oklahoma", tax_year) if include_oklahoma else None
        return cls(federal, oklahoma)

    def compute(self, tax_return: TaxReturnInput) -> TaxComputation:
        if tax_return.tax_year != self.federal_rules.meta.tax_year:
            raise ValueError(
                f"return is for tax year {tax_return.tax_year} but the engine was loaded "
                f"with {self.federal_rules.identifier}"
            )

        trace = CalculationTrace()
        findings = assess_capability(tax_return, self.federal_rules, self.oklahoma_rules)

        dependent_statuses = classify_dependents(tax_return, self.federal_rules)

        federal_result = None
        if tax_return.files_federal:
            federal_result = calculate_federal_return(
                tax_return, self.federal_rules, dependent_statuses, trace
            )

        oklahoma_result = None
        blocked_codes = {f.code for f in findings if f.blocks_filing}
        if (
            tax_return.files_oklahoma
            and self.oklahoma_rules is not None
            and federal_result is not None
            and "state.oklahoma_part_year" not in blocked_codes
        ):
            # Oklahoma depends on the federal result, so it can only run once
            # federal has. A blocked residency finding stops it entirely rather
            # than producing a number the taxpayer might act on.
            oklahoma_result = calculate_oklahoma_return(
                tax_return, federal_result, self.oklahoma_rules, dependent_statuses, trace
            )

        certified = self.federal_rules.is_filable and (
            self.oklahoma_rules is None
            or not tax_return.files_oklahoma
            or self.oklahoma_rules.is_filable
        )

        return TaxComputation(
            tax_year=tax_return.tax_year,
            filing_status=tax_return.filing_status,
            engine_version=ENGINE_VERSION,
            federal_rule_version=self.federal_rules.meta.rule_version,
            oklahoma_rule_version=(
                self.oklahoma_rules.meta.rule_version if self.oklahoma_rules else None
            ),
            computed_at=datetime.now(UTC),
            federal=federal_result,
            oklahoma=oklahoma_result,
            trace=trace,
            findings=findings,
            rule_sets_certified_for_filing=certified,
        )

    @property
    def certification(self) -> CertificationStatus:
        """The weaker of the two rule sets' certification levels."""
        statuses = [self.federal_rules.meta.certification]
        if self.oklahoma_rules is not None:
            statuses.append(self.oklahoma_rules.meta.certification)
        order = [
            CertificationStatus.DRAFT,
            CertificationStatus.UNDER_REVIEW,
            CertificationStatus.PRODUCTION,
        ]
        return min(statuses, key=order.index)


def compute(tax_return: TaxReturnInput) -> TaxComputation:
    """Compute a return using the current rule sets for its tax year."""
    engine = TaxEngine.for_tax_year(
        tax_return.tax_year, include_oklahoma=tax_return.files_oklahoma
    )
    return engine.compute(tax_return)
