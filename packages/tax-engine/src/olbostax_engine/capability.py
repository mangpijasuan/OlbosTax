"""What OlbosTax can and cannot compute -- the capability matrix.

Spec section 4: "Do NOT pretend to support every possible tax situation."

This module inspects a return and reports every situation the engine either
cannot handle or handles only approximately.  It is the single most important
safety mechanism in the product, because the alternative to detecting an
unsupported situation is not "the taxpayer gets a slightly worse answer" -- it
is "the taxpayer signs a wrong return under penalty of perjury and finds out
eighteen months later."

Findings at ``NOT_SUPPORTED`` or ``REQUIRES_TAX_PROFESSIONAL`` block filing.
``PARTIALLY_SUPPORTED`` findings do not block, but must be surfaced in the
review step so the taxpayer decides with the limitation in front of them.

Every check here corresponds to a real gap in the calculation code.  When a gap
is closed, the check is deleted in the same change -- a capability matrix that
drifts out of step with the engine is worse than none, because it teaches
people to ignore it.
"""

from __future__ import annotations

from olbostax_schema import CapabilityFinding, CapabilityLevel, TaxReturnInput
from olbostax_schema.enums import FilingStatus, ResidencyStatus
from olbostax_schema.money import ZERO

from .federal.dependents import head_of_household_supported
from .rules import RuleSet

__all__ = ["assess_capability", "CAPABILITY_MATRIX"]


CAPABILITY_MATRIX: dict[str, tuple[CapabilityLevel, str]] = {
    # -- Fully supported ----------------------------------------------------
    "filing_status.single": (CapabilityLevel.SUPPORTED, "Single"),
    "filing_status.mfj": (CapabilityLevel.SUPPORTED, "Married filing jointly"),
    "filing_status.mfs": (CapabilityLevel.SUPPORTED, "Married filing separately"),
    "filing_status.hoh": (CapabilityLevel.SUPPORTED, "Head of household"),
    "filing_status.qss": (CapabilityLevel.SUPPORTED, "Qualifying surviving spouse"),
    "income.w2": (CapabilityLevel.SUPPORTED, "W-2 wages"),
    "income.interest": (CapabilityLevel.SUPPORTED, "Interest income (1099-INT)"),
    "income.dividends": (CapabilityLevel.SUPPORTED, "Dividend income (1099-DIV)"),
    "income.retirement": (CapabilityLevel.SUPPORTED, "Retirement income (1099-R)"),
    "income.unemployment": (CapabilityLevel.SUPPORTED, "Unemployment (1099-G)"),
    "income.social_security": (CapabilityLevel.SUPPORTED, "Social Security benefits"),
    "deduction.standard": (CapabilityLevel.SUPPORTED, "Standard deduction"),
    "deduction.itemized": (CapabilityLevel.SUPPORTED, "Itemized deductions (Schedule A)"),
    "credit.ctc": (CapabilityLevel.SUPPORTED, "Child Tax Credit"),
    "credit.eitc": (CapabilityLevel.SUPPORTED, "Earned Income Credit"),
    "credit.child_care": (CapabilityLevel.SUPPORTED, "Child and Dependent Care Credit"),
    "credit.education": (CapabilityLevel.SUPPORTED, "Education credits"),
    "state.oklahoma_resident": (CapabilityLevel.SUPPORTED, "Oklahoma full-year resident"),

    # -- Partially supported ------------------------------------------------
    "income.self_employment": (
        CapabilityLevel.PARTIALLY_SUPPORTED,
        "Self-employment: one business, cash basis, no depreciation or home office",
    ),
    "income.capital_gains": (
        CapabilityLevel.PARTIALLY_SUPPORTED,
        "Capital gains from a net figure you provide; no per-lot basis tracking",
    ),
    "deduction.qbi": (
        CapabilityLevel.PARTIALLY_SUPPORTED,
        "Qualified business income deduction below the income threshold only",
    ),

    # -- Not supported ------------------------------------------------------
    "state.multi_state": (CapabilityLevel.NOT_SUPPORTED, "More than one state return"),
    "state.oklahoma_part_year": (
        CapabilityLevel.NOT_SUPPORTED, "Oklahoma part-year resident or nonresident (Form 511-NR)"
    ),
    "income.rental": (CapabilityLevel.NOT_SUPPORTED, "Rental real estate (Schedule E)"),
    "income.k1": (CapabilityLevel.NOT_SUPPORTED, "Partnership or S-corporation income (Schedule K-1)"),
    "income.farm": (CapabilityLevel.NOT_SUPPORTED, "Farm income (Schedule F)"),
    "income.foreign": (CapabilityLevel.REQUIRES_TAX_PROFESSIONAL, "Foreign income or foreign accounts"),
    "income.crypto": (CapabilityLevel.NOT_SUPPORTED, "Digital asset transactions"),
    "status.amended": (CapabilityLevel.NOT_SUPPORTED, "Amended returns (Form 1040-X)"),
    "status.nonresident_alien": (
        CapabilityLevel.REQUIRES_TAX_PROFESSIONAL, "Nonresident alien returns (Form 1040-NR)"
    ),
    "credit.premium_tax": (
        CapabilityLevel.NOT_SUPPORTED, "Premium Tax Credit / Marketplace insurance (Form 1095-A)"
    ),
    "tax.amt": (CapabilityLevel.NOT_SUPPORTED, "Alternative Minimum Tax (Form 6251)"),
    "income.tips_overtime_deduction": (
        CapabilityLevel.NOT_SUPPORTED,
        "New deductions for tip and overtime income",
    ),
}


def _finding(
    code: str, level: CapabilityLevel, title: str, explanation: str, guidance: str = ""
) -> CapabilityFinding:
    return CapabilityFinding(
        code=code, level=level, title=title, explanation=explanation, guidance=guidance
    )


def assess_capability(
    tax_return: TaxReturnInput,
    federal_rules: RuleSet,
    oklahoma_rules: RuleSet | None,
) -> list[CapabilityFinding]:
    """Return every capability finding that applies to this return."""
    findings: list[CapabilityFinding] = []
    income = tax_return.income

    # -- Rule set certification --------------------------------------------
    # Not a tax situation, but the same kind of gate: the engine will compute,
    # but the result is an estimate until the rules are verified.
    for rule_set in (federal_rules, oklahoma_rules):
        if rule_set is None:
            continue
        if not rule_set.is_filable:
            findings.append(
                _finding(
                    f"rules.uncertified.{rule_set.meta.jurisdiction}",
                    CapabilityLevel.NOT_SUPPORTED,
                    f"{rule_set.meta.jurisdiction.title()} tax rules are not yet verified",
                    rule_set.meta.certification.banner,
                    "We will email you as soon as this year's rules are finalized so you "
                    "can file. Nothing you have entered will be lost.",
                )
            )

    # -- Residency ----------------------------------------------------------
    if tax_return.files_oklahoma:
        if tax_return.oklahoma.residency is not ResidencyStatus.FULL_YEAR_RESIDENT:
            findings.append(
                _finding(
                    "state.oklahoma_part_year",
                    CapabilityLevel.NOT_SUPPORTED,
                    "Part-year and nonresident Oklahoma returns are not supported yet",
                    "You told us you were not an Oklahoma resident for the whole year. "
                    "That return (Form 511-NR) requires dividing your income between "
                    "Oklahoma and the states you lived in, which OlbosTax cannot do yet.",
                    "You can still file your federal return with us. For Oklahoma you "
                    "will need a preparer who handles part-year returns.",
                )
            )

    # A W-2 with state wages for a state other than Oklahoma means a second
    # state return is likely required, and OlbosTax files only one.
    other_states = {
        w.box15_state.upper()
        for w in income.w2s
        if w.box15_state and w.box15_state.upper() not in ("", "OK")
    }
    if other_states:
        findings.append(
            _finding(
                "state.multi_state",
                CapabilityLevel.NOT_SUPPORTED,
                "You may need to file in another state",
                "One or more of your W-2s reports wages in "
                f"{', '.join(sorted(other_states))}. OlbosTax currently files federal and "
                "Oklahoma returns only.",
                "You can file your federal and Oklahoma returns with us, but you will "
                "need to file the other state separately.",
            )
        )

    # -- Citizenship / residency status -------------------------------------
    if not tax_return.taxpayer.is_us_citizen_or_resident:
        findings.append(
            _finding(
                "status.nonresident_alien",
                CapabilityLevel.REQUIRES_TAX_PROFESSIONAL,
                "Nonresident returns need a specialist",
                "Nonresident aliens file Form 1040-NR, which has different rules for "
                "income, deductions and treaty benefits.",
                "We recommend working with a preparer experienced in nonresident returns.",
            )
        )

    # -- Income situations the engine does not compute -----------------------
    if tax_return.credits.taxpayer_had_foreign_income or tax_return.credits.foreign_tax_paid > 0:
        findings.append(
            _finding(
                "income.foreign",
                CapabilityLevel.REQUIRES_TAX_PROFESSIONAL,
                "Foreign income needs a specialist",
                "Foreign income, foreign tax credits and foreign account reporting have "
                "separate filing requirements with substantial penalties for errors.",
                "We recommend a preparer who handles international tax.",
            )
        )

    if any(f.box1_rents > 0 for f in income.form_1099_miscs):
        findings.append(
            _finding(
                "income.rental",
                CapabilityLevel.NOT_SUPPORTED,
                "Rental income is not supported yet",
                "One of your 1099-MISC forms reports rent. Rental property requires "
                "Schedule E, including depreciation, which OlbosTax does not calculate.",
                "You will need a preparer or software that supports Schedule E.",
            )
        )

    # A 1099-R where the payer could not determine the taxable amount needs
    # basis information the engine does not have.
    undetermined = [f for f in income.form_1099_rs if f.box2b_taxable_amount_not_determined]
    if undetermined:
        findings.append(
            _finding(
                "income.retirement_basis_unknown",
                CapabilityLevel.REQUIRES_TAX_PROFESSIONAL,
                "We cannot determine how much of your retirement distribution is taxable",
                "Your 1099-R has 'taxable amount not determined' checked. Working out the "
                "taxable portion requires knowing how much of the contributions were "
                "already taxed, which is not on the form.",
                "A tax professional can calculate this using your plan records.",
            )
        )

    # -- Self-employment scope ----------------------------------------------
    se_gross = sum((f.box1_nonemployee_compensation for f in income.form_1099_necs), ZERO)
    if se_gross > 0 or income.self_employment_expenses > 0:
        findings.append(
            _finding(
                "income.self_employment",
                CapabilityLevel.PARTIALLY_SUPPORTED,
                "Simple self-employment only",
                "We handle one self-employment business reported on a cash basis. We do "
                "not calculate depreciation, a home office deduction, vehicle expenses "
                "using the actual expense method, or inventory.",
                "If any of those apply to you, your deduction may be understated.",
            )
        )

    if (
        income.net_short_term_capital_gain_loss != 0
        or income.net_long_term_capital_gain_loss != 0
    ):
        findings.append(
            _finding(
                "income.capital_gains",
                CapabilityLevel.PARTIALLY_SUPPORTED,
                "You entered your capital gains as a total",
                "We use the net gain or loss figure you provided rather than tracking "
                "each sale. We also do not carry forward losses from prior years or "
                "apply the wash sale rules.",
                "Check the totals against your broker's 1099-B before filing.",
            )
        )

    # -- Situations flagged by other inputs ---------------------------------
    if not head_of_household_supported(tax_return):
        findings.append(
            _finding(
                "status.hoh_no_qualifying_person",
                CapabilityLevel.NOT_SUPPORTED,
                "Head of household needs a qualifying person",
                "You selected head of household, but none of the dependents you entered "
                "lived with you for more than half the year. The IRS will reject a head "
                "of household return without a qualifying person.",
                "Check your dependents, or change your filing status to single.",
            )
        )

    if tax_return.filing_status is FilingStatus.MARRIED_FILING_SEPARATELY:
        findings.append(
            _finding(
                "status.mfs_limitations",
                CapabilityLevel.PARTIALLY_SUPPORTED,
                "Filing separately limits several tax benefits",
                "Married filing separately disqualifies you from the Earned Income "
                "Credit, education credits and the student loan interest deduction, and "
                "reduces others. If your spouse itemizes, you must itemize too.",
                "Most married couples pay less tax filing jointly. Consider comparing "
                "both before you file.",
            )
        )

    # -- Unsupported new provisions -----------------------------------------
    # Several deductions enacted for tax year 2025 (tip income, overtime
    # premiums, car loan interest) are not implemented. A taxpayer with tips
    # reported in W-2 Box 7 may be entitled to one, so say so rather than
    # letting them assume the return is complete.
    if any(w.box7_social_security_tips > 0 or w.box8_allocated_tips > 0 for w in income.w2s):
        findings.append(
            _finding(
                "income.tips_overtime_deduction",
                CapabilityLevel.PARTIALLY_SUPPORTED,
                "New deductions for tip income are not included",
                "Your W-2 reports tip income. A deduction for qualified tips was enacted "
                "for this tax year and OlbosTax has not implemented it yet, because we "
                "have not finished verifying the rules against IRS guidance.",
                "Your return will still be correct on everything else, but you may be "
                "entitled to a larger refund than we are showing.",
            )
        )

    return findings
