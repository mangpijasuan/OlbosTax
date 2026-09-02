# Tax rule governance

## Why this process exists

Tests prove the engine's arithmetic is self-consistent. They prove nothing
about whether the numbers it operates on match what the IRS published. A rule
set can be internally coherent, fully covered by tests, and wrong on every
return.

This document is the process that closes that gap. It is the only thing
standing between a transcription error and thousands of incorrect returns.

## Certification states

| State | Meaning | Filable |
| --- | --- | --- |
| `DRAFT` | Values transcribed; nobody has checked them against the primary source. | No |
| `UNDER_REVIEW` | A named reviewer is checking each value. | No |
| `PRODUCTION` | Every value verified against its cited primary source by a qualified reviewer, recorded in `verified_by`/`verified_at`, regression suite passing. | Yes |

Current state, as of this writing:

| Rule set | Certification |
| --- | --- |
| `federal/2025` | `DRAFT` |
| `oklahoma/2025` | `DRAFT` |

## The promotion process

```
Transcribe          →  DRAFT
     │                  values entered with source citations
     ▼
Automated tests     →  structural invariants must hold
     │
     ▼
Regression suite    →  every baseline diff explained by the rule change
     │
     ▼
Human review        →  UNDER_REVIEW
     │                  qualified reviewer checks EVERY value against the
     │                  primary document, not a summary of it
     ▼
Approval recorded   →  rule_set_approvals row, bound to a content hash
     │
     ▼
Certification set   →  PRODUCTION (a reviewed code change to the rule file)
```

Approval and certification are separate steps on purpose. The approval is
evidence a person checked the values; the certification is what the software
acts on. Keeping them apart means promotion to `PRODUCTION` is a reviewed diff,
not an API call.

## What a reviewer must actually do

For **every** numeric value in the rule set:

1. Open the cited primary source. Not a summary, not a news article, not a tax
   software blog — the Rev. Proc., the statute, the OTC packet.
2. Confirm the value matches, for the correct tax year.
3. Confirm no later authority amended it. This is the step most likely to be
   skipped and the one most likely to be wrong.
4. Confirm the rule file's `_source` reference actually points at the document
   consulted.

Then run the regression suite and account for **every** changed baseline. A
moved refund that nobody can explain by a specific rule change is an
unexplained defect, not noise.

## Tax year 2025 is unusually risky

Several inflation-adjusted amounts published in Rev. Proc. 2024-40 were amended
by later legislation before that revenue procedure took effect. For each such
parameter the reviewer must establish **which figure controls**, not merely that
some published figure matches.

Parameters in the federal rule set carrying this risk, all flagged in the file
itself:

- Standard deduction base amounts
- The additional deduction for taxpayers 65 and older
- The Child Tax Credit per-child amount and refundable limit
- The SALT cap, its phase-down threshold, rate and floor

## Oklahoma needs more scrutiny than federal

The Oklahoma rule set is the least trustworthy artefact in the repository. Its
structure is right; its numbers need a tax professional.

Specifically unverified:

- **The rate schedule.** Both the bracket edges and the top rate.
- **Which tax year the 2025 rate legislation first applies to.** Applying a
  future year's rate cut to tax year 2025 would understate every Oklahoma
  return.
- **The standard deduction amounts**, which are set by state law and do not
  track the federal figures.
- **The personal exemption**, its amount, and who qualifies for the additional
  age-based exemptions.
- **Every subtraction cap**, including whether the military retirement
  exclusion is now complete and from which year.
- **Every credit**, including rates, refundability and income limits — the
  Oklahoma EIC's refundability in particular has changed more than once.

## Adding a new tax year

1. Copy the prior year's file to `rules/data/<jurisdiction>/<year>.json`.
2. Set `certification` to `DRAFT` and write a specific `unverified_notice`.
3. Update `rule_version` and every `sources` entry to the new year's documents.
4. Update every inflation-adjusted value. Values that did *not* change need
   confirming too — an unindexed figure that quietly became indexed is a silent
   error.
5. Add regression baselines for the new year.
6. Run the promotion process above.

The engine will refuse to compute a year it has no rule set for, rather than
falling back to the nearest year. A 2026 return computed with 2025 brackets is
wrong in a way that looks entirely plausible.

## Changing an existing rule set

A `PRODUCTION` rule set is **not edited**. Corrections create a new
`rule_version`, because returns already filed under the old version must stay
reproducible exactly as computed. `RuleSetMeta.supersedes` records the chain.

## What the code enforces on its own

- `TaxEngine` stamps `rule_sets_certified_for_filing` on every computation.
- `EFileProvider.preflight` refuses to transmit an uncertified computation.
- `capability.py` raises a blocking finding naming the uncertified
  jurisdiction, which the taxpayer sees on the review screen.
- `/api/v1/system/filing-readiness` reports it publicly, and the landing page
  shows it to visitors before they enter anything.
- A regression test asserts no synthetic taxpayer is filable while the rules
  are `DRAFT`, so the gate cannot be removed without a test failing.
- The admin rule-set screen shows any rule set claiming `PRODUCTION` with no
  approval row — a discrepancy worth seeing.
