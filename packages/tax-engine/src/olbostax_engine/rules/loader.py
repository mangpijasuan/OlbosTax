"""Loading, validating and freezing versioned tax rule sets.

Rule sets are JSON, not Python.  That is a deliberate constraint: a tax rule
change must be reviewable by someone who can read a Rev. Proc. but not a
Python decorator, and it must be diffable line by line against the source
document.  Putting the 2025 standard deduction in a ``.py`` file would make
every rule update a code deployment reviewed by engineers; putting it in JSON
with source metadata makes it a data change reviewable by a tax professional.

Every numeric value in a rule set is a string in the JSON and becomes a
:class:`~decimal.Decimal` here.  JSON numbers are IEEE 754 doubles, and a
bracket edge of ``48475.00000000001`` is not a theoretical concern.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .certification import CertificationStatus

__all__ = ["RuleSource", "RuleSetMeta", "RuleSet", "RuleLookupError", "load_rule_set",
           "available_rule_sets", "DATA_ROOT"]

DATA_ROOT = Path(__file__).parent / "data"


class RuleLookupError(KeyError):
    """A rule path does not exist in the rule set.

    Raised rather than returning a default.  A missing rule means the engine is
    about to compute something it has no authority for, and a silent zero would
    turn that into a wrong number on a tax return instead of a loud failure.
    """


class RuleSource(BaseModel):
    """Provenance for a group of rule values -- spec section 41."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    authority: str = Field(description='e.g. "Internal Revenue Service"')
    document: str = Field(description='e.g. "Rev. Proc. 2024-40"')
    title: str = ""
    url: str = ""
    tax_year: int
    effective_date: date | None = None
    retrieved_date: date | None = None
    notes: str = ""


class RuleSetMeta(BaseModel):
    """Identity and certification state of a rule set.

    Frozen, and deliberately so. ``load_rule_set`` caches, so every caller in
    the process shares one instance -- and ``certification`` is what the e-file
    layer consults before allowing a return to be transmitted. If this were
    mutable, any code holding a reference could flip a DRAFT rule set to
    PRODUCTION for the lifetime of the process, and every subsequent return
    would be reported as filable. Freezing the container without freezing this
    would leave the gate open through the back door.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    jurisdiction: str
    tax_year: int
    rule_version: str
    certification: CertificationStatus = CertificationStatus.DRAFT
    verified_by: str | None = None
    verified_at: date | None = None
    sources: list[RuleSource] = Field(default_factory=list)
    unverified_notice: str = ""
    supersedes: str | None = None


class RuleSet(BaseModel):
    """An immutable, versioned set of tax parameters for one jurisdiction-year."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: RuleSetMeta
    values: dict[str, Any]

    # -- lookup ------------------------------------------------------------

    def get(self, path: str) -> Any:
        """Fetch a value by dotted path, e.g. ``"standard_deduction.SINGLE"``."""
        node: Any = self.values
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise RuleLookupError(
                    f"{self.identifier}: no rule at {path!r} (failed at {part!r})"
                )
            node = node[part]
        return node

    def decimal(self, path: str) -> Decimal:
        value = self.get(path)
        if isinstance(value, Decimal):
            return value
        raise RuleLookupError(f"{self.identifier}: rule {path!r} is not a decimal ({value!r})")

    def integer(self, path: str) -> int:
        value = self.get(path)
        if isinstance(value, Decimal):
            return int(value)
        if isinstance(value, int):
            return value
        raise RuleLookupError(f"{self.identifier}: rule {path!r} is not an integer ({value!r})")

    def boolean(self, path: str) -> bool:
        value = self.get(path)
        if isinstance(value, bool):
            return value
        raise RuleLookupError(f"{self.identifier}: rule {path!r} is not a boolean ({value!r})")

    def brackets(self, path: str) -> list[tuple[Decimal, Decimal]]:
        """Return a rate schedule as ``[(upper_bound, rate), ...]``.

        The final bracket carries an upper bound of ``Decimal("Infinity")`` so
        that bracket-walking code needs no special case for the top rate.
        """
        raw = self.get(path)
        if not isinstance(raw, list):
            raise RuleLookupError(f"{self.identifier}: rule {path!r} is not a bracket table")
        out: list[tuple[Decimal, Decimal]] = []
        for row in raw:
            upper = row.get("up_to")
            rate = row["rate"]
            out.append((Decimal("Infinity") if upper is None else upper, rate))
        return out

    def has(self, path: str) -> bool:
        try:
            self.get(path)
        except RuleLookupError:
            return False
        return True

    # -- identity ----------------------------------------------------------

    @property
    def identifier(self) -> str:
        return f"{self.meta.jurisdiction}/{self.meta.tax_year}@{self.meta.rule_version}"

    @property
    def is_filable(self) -> bool:
        return self.meta.certification.is_filable

    def source_for(self, section: str) -> RuleSource | None:
        """The cited source for a top-level section of the rule set."""
        node = self.values.get(section)
        if not isinstance(node, dict):
            return None
        source_id = node.get("_source")
        return next((s for s in self.meta.sources if s.id == source_id), None)

    def citation(self, section: str) -> str:
        source = self.source_for(section)
        return f"{source.authority}, {source.document}" if source else ""


def _decimalize(node: Any, path: str = "") -> Any:
    """Recursively convert rule values into Decimals.

    Keys beginning with ``_`` are metadata (``_source``, ``_note``) and are
    left as-is.  Numbers appearing as JSON floats are rejected outright: the
    rule files are written with quoted strings precisely so that no tax
    parameter can be corrupted by binary floating point on the way in.
    """
    if isinstance(node, dict):
        return {
            key: value if key.startswith("_") else _decimalize(value, f"{path}.{key}")
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_decimalize(item, f"{path}[{i}]") for i, item in enumerate(node)]
    if isinstance(node, bool) or node is None:
        return node
    if isinstance(node, float):
        raise ValueError(
            f"rule value at {path or '<root>'} is a JSON float; write it as a quoted "
            "string so it is read as an exact Decimal"
        )
    if isinstance(node, int):
        return Decimal(node)
    if isinstance(node, str):
        try:
            return Decimal(node)
        except Exception:
            return node
    return node


def _rule_path(jurisdiction: str, tax_year: int) -> Path:
    return DATA_ROOT / jurisdiction.lower() / f"{tax_year}.json"


@lru_cache(maxsize=32)
def load_rule_set(jurisdiction: str, tax_year: int) -> RuleSet:
    """Load and cache the rule set for a jurisdiction and year.

    Cached because rule sets are frozen and reading them is pure; a long-lived
    API process should parse ``2025.json`` once, not once per return.
    """
    path = _rule_path(jurisdiction, tax_year)
    if not path.exists():
        raise RuleLookupError(
            f"no rule set for {jurisdiction} tax year {tax_year} "
            f"(expected {path}). OlbosTax cannot compute a return for a year it has "
            "no verified rules for."
        )
    raw = json.loads(path.read_text())
    meta = RuleSetMeta.model_validate(raw["meta"])
    if meta.tax_year != tax_year or meta.jurisdiction.lower() != jurisdiction.lower():
        raise ValueError(
            f"{path} declares {meta.jurisdiction}/{meta.tax_year} but was loaded as "
            f"{jurisdiction}/{tax_year}"
        )
    return RuleSet(meta=meta, values=_decimalize(raw["values"]))


def available_rule_sets() -> list[tuple[str, int, CertificationStatus]]:
    """Every rule set on disk, for the admin tax-rule dashboard."""
    found: list[tuple[str, int, CertificationStatus]] = []
    for jurisdiction_dir in sorted(DATA_ROOT.iterdir()):
        if not jurisdiction_dir.is_dir():
            continue
        for rule_file in sorted(jurisdiction_dir.glob("*.json")):
            try:
                year = int(rule_file.stem)
            except ValueError:
                continue
            rule_set = load_rule_set(jurisdiction_dir.name, year)
            found.append((jurisdiction_dir.name, year, rule_set.meta.certification))
    return found
