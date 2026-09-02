"""Pre-transmission validation.

Spec section 15 defines five validation levels. Levels 1-3 (input, tax, and
cross-form validation) are implementable from first principles and are here.

**Levels 4 and 5 are not implemented and must not be faked.** Federal e-file
validation means the current IRS Modernized e-File schemas and business rules;
Oklahoma validation means the OTC's published requirements. Neither is in this
repository. Writing plausible-looking rules from memory would produce a system
that passes its own validation and is rejected by the authority -- and worse,
would hide genuine problems behind a green checkmark.

So this module implements what it can justify, and
:func:`unimplemented_validation_levels` reports the gap explicitly so it shows
up in the compliance status rather than being forgotten.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from olbostax_schema import Jurisdiction, TaxComputation, TaxReturnInput
from olbostax_schema.enums import FilingStatus, RefundMethod
from olbostax_schema.money import ZERO

from .base import EFileValidationIssue, EFileValidationResult

__all__ = ["validate_for_efile", "unimplemented_validation_levels", "VALIDATOR_VERSION"]

VALIDATOR_VERSION = "levels-1-3-only/0.1.0"

# Structural rules for taxpayer identification numbers.
#
# An SSN is never issued with area 000 or 666, group 00, or serial 0000.
# Area 900-999 is never an SSN either -- but it *is* the ITIN range, and ITIN
# holders file returns, so a blanket rejection of 9xx would refuse service to
# legitimate taxpayers. An ITIN is 9xx-yy-zzzz where the group yy falls in one
# of the ranges the IRS assigns.
#
# These are published structural facts about number assignment, not guesses,
# and they catch typos and placeholder values before the authority rejects the
# return. They cannot tell whether a number was actually issued to this person;
# only the SSA and IRS can, and that check happens at the authority.
_BAD_AREA = frozenset({"000", "666"})
_ITIN_GROUPS = frozenset(
    {f"{n:02d}" for n in (*range(50, 66), *range(70, 89), *range(90, 93), *range(94, 100))}
)

# Identifiers reserved for synthetic test data. Area 9xx with a group outside
# every ITIN range is issued by nobody -- not as an SSN, not as an ITIN -- so
# it cannot collide with a real person. Accepted only on channels that do not
# file returns; see ``allow_test_identifiers``.
def _is_test_identifier(digits: str) -> bool:
    return digits[0] == "9" and digits[3:5] not in _ITIN_GROUPS


def _is_structurally_invalid(digits: str) -> bool:
    area, group, serial = digits[0:3], digits[3:5], digits[5:9]
    if area in _BAD_AREA or group == "00" or serial == "0000":
        return True
    if area[0] == "9":
        # A 9xx number is valid only as an ITIN.
        return group not in _ITIN_GROUPS
    return False


def _issue(
    code: str,
    jurisdiction: Jurisdiction,
    message: str,
    resolution: str = "",
    *,
    severity: str = "ERROR",
    field_path: str | None = None,
    technical: str | None = None,
) -> EFileValidationIssue:
    return EFileValidationIssue(
        code=code,
        severity=severity,
        jurisdiction=jurisdiction,
        taxpayer_message=message,
        resolution=resolution,
        field_path=field_path,
        technical_detail=technical,
    )


def _level_1_input(
    tax_return: TaxReturnInput, j: Jurisdiction, *, allow_test_identifiers: bool
) -> list[EFileValidationIssue]:
    """Structural validity of what the taxpayer entered."""
    issues: list[EFileValidationIssue] = []

    people = [("taxpayer", tax_return.taxpayer)]
    if tax_return.spouse:
        people.append(("spouse", tax_return.spouse))
    people.extend((f"dependent {d.first_name}", d) for d in tax_return.dependents)

    for label, person in people:
        digits = person.ssn.reveal()
        is_test = _is_test_identifier(digits)
        if _is_structurally_invalid(digits) and not (allow_test_identifiers and is_test):
            issues.append(
                _issue(
                    "OT-SSN-STRUCTURE",
                    j,
                    f"The Social Security number for {label} is not a valid number.",
                    "Check it against the Social Security card. The IRS will reject a "
                    "return with an impossible Social Security number.",
                    field_path=f"{label}.ssn",
                )
            )
        elif is_test and allow_test_identifiers:
            issues.append(
                _issue(
                    "OT-TEST-IDENTIFIER",
                    j,
                    f"The identification number for {label} is a test number, not a "
                    "real Social Security number.",
                    "This return is synthetic and cannot be filed.",
                    severity="WARNING",
                    field_path=f"{label}.ssn",
                )
            )
        if person.date_of_birth > date.today():
            issues.append(
                _issue(
                    "OT-DOB-FUTURE",
                    j,
                    f"The date of birth for {label} is in the future.",
                    "Check the date. It may have been entered with the wrong year.",
                    field_path=f"{label}.date_of_birth",
                )
            )
        if person.date_of_birth.year < 1900:
            issues.append(
                _issue(
                    "OT-DOB-IMPLAUSIBLE",
                    j,
                    f"The date of birth for {label} does not look right.",
                    "Check the year of birth.",
                    field_path=f"{label}.date_of_birth",
                )
            )

    # Duplicate SSNs across the return: a real cause of rejection, and easy to
    # produce by copying a dependent row and forgetting to change the number.
    seen: dict[str, str] = {}
    for label, person in people:
        raw = person.ssn.reveal()
        if raw in seen:
            issues.append(
                _issue(
                    "OT-SSN-DUPLICATE",
                    j,
                    f"The same Social Security number is used for {seen[raw]} and {label}.",
                    "Each person on the return needs their own Social Security number.",
                )
            )
        seen[raw] = label

    if tax_return.refund_method is RefundMethod.DIRECT_DEPOSIT:
        needs_deposit = _expects_refund(tax_return, j)
        if needs_deposit and tax_return.direct_deposit is None:
            issues.append(
                _issue(
                    "OT-DEPOSIT-MISSING",
                    j,
                    "You chose direct deposit but have not given us your bank details.",
                    "Add your routing and account numbers, or choose to receive a "
                    "paper check instead.",
                    field_path="direct_deposit",
                )
            )
    return issues


def _expects_refund(tax_return: TaxReturnInput, j: Jurisdiction) -> bool:
    return True  # refined by the caller using the computation


def _level_2_tax(
    tax_return: TaxReturnInput, computation: TaxComputation, j: Jurisdiction
) -> list[EFileValidationIssue]:
    """Checks that need tax knowledge but not cross-form reconciliation."""
    issues: list[EFileValidationIssue] = []

    if tax_return.filing_status.is_joint and tax_return.spouse is None:
        issues.append(
            _issue(
                "OT-STATUS-NO-SPOUSE",
                j,
                "A joint return needs information about your spouse.",
                "Add your spouse's name, Social Security number and date of birth.",
            )
        )

    if tax_return.filing_status is FilingStatus.QUALIFYING_SURVIVING_SPOUSE:
        if not tax_return.dependents:
            issues.append(
                _issue(
                    "OT-QSS-NO-DEPENDENT",
                    j,
                    "Qualifying surviving spouse status requires a dependent child.",
                    "If you do not have a dependent child living with you, your filing "
                    "status is probably single or head of household.",
                )
            )

    for dependent in tax_return.dependents:
        if dependent.date_of_birth > tax_return.year_end:
            issues.append(
                _issue(
                    "OT-DEPENDENT-BORN-AFTER-YEAR-END",
                    j,
                    f"{dependent.first_name} was born after {tax_return.tax_year} ended.",
                    f"A child born in {tax_return.year_end.year + 1} cannot be claimed "
                    f"on your {tax_return.tax_year} return, but you can claim them next "
                    "year.",
                )
            )
        if dependent.claimed_by_another_taxpayer:
            issues.append(
                _issue(
                    "OT-DEPENDENT-CLAIMED-ELSEWHERE",
                    j,
                    f"You told us someone else is claiming {dependent.first_name}.",
                    "Remove this dependent from your return, or if you are entitled to "
                    "claim them, correct your answer.",
                )
            )

    # A machine-extracted value nobody confirmed must not be filed. This is the
    # provenance rule from spec section 9, enforced at the last gate.
    unverified = [
        document
        for document in tax_return.all_documents()
        if getattr(document, "provenance", None) is not None
        and document.provenance.requires_user_verification
    ]
    if unverified:
        issues.append(
            _issue(
                "OT-UNVERIFIED-EXTRACTION",
                j,
                f"{len(unverified)} of your documents have amounts we read automatically "
                "that you have not confirmed yet.",
                "Review each document and confirm the amounts match what is printed on "
                "your copy. You are signing this return, so the numbers need to be ones "
                "you have checked.",
            )
        )
    return issues


def _level_3_cross_form(
    tax_return: TaxReturnInput, computation: TaxComputation, j: Jurisdiction
) -> list[EFileValidationIssue]:
    """Reconciliation between documents and the computed return."""
    issues: list[EFileValidationIssue] = []
    income = tax_return.income

    for index, w2 in enumerate(income.w2s, start=1):
        label = w2.employer_name or f"W-2 number {index}"

        # Withholding larger than wages is nearly always a data entry error --
        # boxes 1 and 2 transposed. Filing it produces a large wrong refund
        # and an IRS notice.
        if w2.box2_federal_income_tax_withheld > w2.box1_wages and w2.box1_wages > 0:
            issues.append(
                _issue(
                    "OT-W2-WITHHOLDING-EXCEEDS-WAGES",
                    j,
                    f"On your W-2 from {label}, the federal tax withheld is more than "
                    "the wages.",
                    "Check boxes 1 and 2. They may have been entered the wrong way "
                    "around.",
                    field_path=f"income.w2s[{index - 1}]",
                )
            )

        if w2.box1_wages == 0 and w2.box2_federal_income_tax_withheld > 0:
            issues.append(
                _issue(
                    "OT-W2-WITHHOLDING-NO-WAGES",
                    j,
                    f"Your W-2 from {label} shows tax withheld but no wages.",
                    "Check box 1 on your W-2.",
                    severity="WARNING",
                )
            )

        # Social Security tax should be about 6.2% of Social Security wages.
        # A wide tolerance avoids flagging legitimate cases (multiple
        # employers, tips, wage base) while still catching a mistyped digit.
        if w2.box3_social_security_wages > 0 and w2.box4_social_security_tax_withheld > 0:
            expected = w2.box3_social_security_wages * Decimal("0.062")
            if abs(w2.box4_social_security_tax_withheld - expected) > expected * Decimal("0.25"):
                issues.append(
                    _issue(
                        "OT-W2-SOCIAL-SECURITY-MISMATCH",
                        j,
                        f"On your W-2 from {label}, the Social Security tax withheld "
                        "does not look consistent with the Social Security wages.",
                        "Check boxes 3 and 4 against your W-2.",
                        severity="WARNING",
                    )
                )

    if j is Jurisdiction.OKLAHOMA and computation.oklahoma is not None:
        state_wages = sum(
            (w.box16_state_wages for w in income.w2s if w.box15_state.upper() == "OK"), ZERO
        )
        federal_wages = sum((w.box1_wages for w in income.w2s), ZERO)
        if state_wages > 0 and federal_wages > 0:
            difference = abs(state_wages - federal_wages)
            if difference > federal_wages * Decimal("0.20"):
                issues.append(
                    _issue(
                        "OT-STATE-WAGES-DIVERGE",
                        Jurisdiction.OKLAHOMA,
                        "Your Oklahoma wages are very different from your federal wages.",
                        "This can be correct if you worked in more than one state. If "
                        "you worked only in Oklahoma, check box 16 on your W-2.",
                        severity="WARNING",
                    )
                )

    # Refund destination sanity: a return claiming a refund with no way to
    # deliver it.
    result = computation.federal if j is Jurisdiction.FEDERAL else computation.oklahoma
    if (
        result is not None
        and result.refund > 0
        and tax_return.refund_method is RefundMethod.DIRECT_DEPOSIT
        and tax_return.direct_deposit is None
    ):
        issues.append(
            _issue(
                "OT-REFUND-NO-DESTINATION",
                j,
                f"You are due a refund of ${result.refund:,.2f} but have not told us "
                "where to send it.",
                "Add your bank details for direct deposit, or choose a paper check.",
            )
        )
    return issues


def validate_for_efile(
    tax_return: TaxReturnInput,
    computation: TaxComputation,
    jurisdiction: Jurisdiction,
    *,
    allow_test_identifiers: bool = False,
) -> EFileValidationResult:
    """Run validation levels 1-3. Levels 4 and 5 are not implemented.

    ``allow_test_identifiers`` downgrades the structural identifier check to a
    warning for numbers in the reserved synthetic range. It must be enabled
    only on channels that do not file returns -- providers derive it from
    ``channel.is_real_filing`` rather than from configuration, so a live
    transmission path cannot be made to accept test data by changing a setting.
    """
    issues: list[EFileValidationIssue] = []
    issues.extend(
        _level_1_input(tax_return, jurisdiction, allow_test_identifiers=allow_test_identifiers)
    )
    issues.extend(_level_2_tax(tax_return, computation, jurisdiction))
    issues.extend(_level_3_cross_form(tax_return, computation, jurisdiction))

    # Deduplicate while preserving order: the same underlying problem can be
    # reached by more than one check, and showing a taxpayer the same message
    # twice makes the error centre look broken.
    seen: set[tuple[str, str]] = set()
    unique: list[EFileValidationIssue] = []
    for issue in issues:
        key = (issue.code, issue.field_path or "")
        if key not in seen:
            seen.add(key)
            unique.append(issue)

    return EFileValidationResult(issues=unique, validator_version=VALIDATOR_VERSION)


def unimplemented_validation_levels() -> list[dict[str, str]]:
    """The validation this system does NOT perform.

    Surfaced through the admin dashboard and the compliance status document so
    that the gap is visible to whoever decides whether the product is ready to
    file, rather than being discoverable only by reading source code.
    """
    return [
        {
            "level": "4",
            "name": "Federal e-file schema and business rule validation",
            "status": "NOT_IMPLEMENTED",
            "reason": (
                "Requires the current IRS Modernized e-File schemas and business rule "
                "set for the tax year. These are obtained through IRS e-file provider "
                "registration and are not present in this repository."
            ),
            "consequence": (
                "Returns may pass OlbosTax validation and still be rejected by the IRS "
                "for schema or business rule violations."
            ),
        },
        {
            "level": "5",
            "name": "Oklahoma e-file validation",
            "status": "NOT_IMPLEMENTED",
            "reason": (
                "Requires current Oklahoma Tax Commission e-file specifications and "
                "schemas, obtained through OTC software developer registration."
            ),
            "consequence": (
                "Returns may pass OlbosTax validation and still be rejected by the "
                "Oklahoma Tax Commission."
            ),
        },
    ]
