"""Dependent qualification under IRC s.152.

Dependent status drives the Child Tax Credit, the Credit for Other
Dependents, the Earned Income Credit, head-of-household eligibility and the
child care credit.  It is computed once, here, and the answer is reused,
because four separate implementations of "is this a qualifying child" is four
opportunities for them to disagree on the same return.

What this module does *not* do is guess.  Every test below is answered from a
question the taxpayer was asked.  Where a fact was not collected, the
conservative answer is used and a capability finding is raised rather than
inferring the taxpayer's intent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from olbostax_schema import Dependent, TaxReturnInput
from olbostax_schema.enums import FilingStatus

from ..rules import RuleSet

__all__ = ["DependentStatus", "classify_dependent", "classify_dependents"]


@dataclass(frozen=True, slots=True)
class DependentStatus:
    """The tax consequences of one claimed dependent."""

    dependent: Dependent
    age: int
    is_qualifying_child: bool
    is_qualifying_relative: bool
    qualifies_for_child_tax_credit: bool
    qualifies_for_other_dependent_credit: bool
    qualifies_for_eitc: bool
    qualifies_for_child_care_credit: bool
    disqualification_reasons: tuple[str, ...] = ()

    @property
    def is_dependent(self) -> bool:
        return self.is_qualifying_child or self.is_qualifying_relative


def _age_at_year_end(dependent: Dependent, year_end: date) -> int:
    return dependent.age_on(year_end)


def classify_dependent(
    dependent: Dependent,
    tax_return: TaxReturnInput,
    rules: RuleSet,
) -> DependentStatus:
    """Apply the qualifying-child and qualifying-relative tests to one dependent."""
    year_end = tax_return.year_end
    age = _age_at_year_end(dependent, year_end)
    reasons: list[str] = []

    # -- Qualifying child, IRC s.152(c) ------------------------------------
    # Relationship, age, residency, support, and joint-return tests. The
    # tie-breaker rules of s.152(c)(4) are not applied: OlbosTax asks whether
    # another taxpayer is claiming the child and takes the answer at face
    # value, because resolving a contested claim is not something software
    # should decide on one party's account.
    relationship_ok = dependent.relationship.is_qualifying_child_relationship
    if not relationship_ok:
        reasons.append("relationship is not one that qualifies a child")

    under_19 = age < 19
    student_under_24 = age < 24 and dependent.is_student
    age_ok = under_19 or student_under_24 or dependent.is_permanently_disabled
    if not age_ok:
        reasons.append("too old to be a qualifying child and not permanently disabled")

    residency_ok = dependent.months_lived_with_taxpayer > 6
    if not residency_ok:
        reasons.append("did not live with the taxpayer for more than half the year")

    support_ok = not dependent.provided_over_half_own_support
    if not support_ok:
        reasons.append("provided more than half of their own support")

    joint_return_ok = not dependent.files_joint_return_with_spouse
    if not joint_return_ok:
        reasons.append("files a joint return with a spouse")

    not_claimed_elsewhere = not dependent.claimed_by_another_taxpayer
    if not not_claimed_elsewhere:
        reasons.append("is claimed by another taxpayer")

    citizenship_ok = dependent.is_us_citizen_or_resident
    if not citizenship_ok:
        reasons.append("is not a US citizen, national or resident")

    is_qualifying_child = all(
        (
            relationship_ok,
            age_ok,
            residency_ok,
            support_ok,
            joint_return_ok,
            not_claimed_elsewhere,
            citizenship_ok,
        )
    )

    # -- Qualifying relative, IRC s.152(d) ---------------------------------
    # The gross-income test (the dependent's own income must be under the
    # exemption amount) is not evaluated: OlbosTax does not collect the
    # dependent's income. Rather than assume it is under the limit, the
    # taxpayer's assertion that they provided over half the support is used,
    # and the interview warns about the income test. See capability.py.
    is_qualifying_relative = (
        not is_qualifying_child
        and dependent.taxpayer_provided_over_half_support
        and not_claimed_elsewhere
        and joint_return_ok
        and citizenship_ok
    )

    # -- Credit eligibility -------------------------------------------------
    ctc_max_age = rules.integer("child_tax_credit.qualifying_child_max_age")
    needs_work_ssn = rules.boolean("child_tax_credit.requires_ssn_valid_for_employment")

    qualifies_for_ctc = (
        is_qualifying_child
        and age <= ctc_max_age
        and (dependent.has_valid_ssn_for_employment or not needs_work_ssn)
    )
    if (
        is_qualifying_child
        and age <= ctc_max_age
        and needs_work_ssn
        and not dependent.has_valid_ssn_for_employment
    ):
        reasons.append(
            "does not have an SSN valid for employment, which the Child Tax " "Credit requires"
        )

    # A dependent who fails the CTC only because of the SSN requirement still
    # qualifies for the $500 Credit for Other Dependents.
    qualifies_for_odc = (is_qualifying_child or is_qualifying_relative) and not qualifies_for_ctc

    # EITC uses its own, looser qualifying-child definition: no support test.
    qualifies_for_eitc = (
        relationship_ok
        and age_ok
        and residency_ok
        and joint_return_ok
        and not_claimed_elsewhere
        and citizenship_ok
    )

    care_max_age = rules.integer("child_and_dependent_care_credit.qualifying_child_max_age")
    qualifies_for_care = (
        is_qualifying_child
        and dependent.child_care_expenses_paid
        and (age <= care_max_age or dependent.is_disabled_and_needs_care)
    )

    return DependentStatus(
        dependent=dependent,
        age=age,
        is_qualifying_child=is_qualifying_child,
        is_qualifying_relative=is_qualifying_relative,
        qualifies_for_child_tax_credit=qualifies_for_ctc,
        qualifies_for_other_dependent_credit=qualifies_for_odc,
        qualifies_for_eitc=qualifies_for_eitc,
        qualifies_for_child_care_credit=qualifies_for_care,
        disqualification_reasons=tuple(reasons),
    )


def classify_dependents(tax_return: TaxReturnInput, rules: RuleSet) -> list[DependentStatus]:
    return [classify_dependent(d, tax_return, rules) for d in tax_return.dependents]


def head_of_household_supported(tax_return: TaxReturnInput) -> bool:
    """Whether the head-of-household claim has a plausible qualifying person.

    A full s.2(b) determination requires facts OlbosTax does not collect (cost
    of keeping up a home, and whether an unmarried-for-tax-purposes test is
    met).  This checks only the part that can be checked, and the interview
    asks the rest directly.
    """
    if tax_return.filing_status is not FilingStatus.HEAD_OF_HOUSEHOLD:
        return True
    return any(
        d.months_lived_with_taxpayer > 6 and not d.claimed_by_another_taxpayer
        for d in tax_return.dependents
    )
