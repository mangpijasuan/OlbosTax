# Tax engine design

## The problem this design solves

A tax engine is unusually easy to make *look* correct. Pick plausible bracket
numbers, write tests asserting the engine reproduces those numbers, and every
test passes. The tests prove the arithmetic is self-consistent. They prove
nothing about whether the brackets match what the IRS published.

Most of what follows exists to keep that distinction visible.

## Layers

```
olbostax_engine/
├── engine.py            TaxEngine.compute -- the only public entry point
├── version.py           ENGINE_VERSION, stamped on every computation
├── capability.py        what we cannot compute, reported as findings
├── rules/
│   ├── loader.py        JSON → frozen RuleSet, Decimal-safe
│   ├── certification.py DRAFT / UNDER_REVIEW / PRODUCTION
│   └── data/
│       ├── federal/2025.json
│       └── oklahoma/2025.json
├── calculations/
│   └── primitives.py    bracket walking, phase-outs
├── federal/
│   ├── income.py        1040 lines 1-9, IRC s.86
│   ├── adjustments.py   Schedule 1 Part II
│   ├── deductions.py    standard vs itemized, Schedule A
│   ├── se_tax.py        Schedule SE, Form 8959
│   ├── tax.py           rate schedules, capital gain worksheet, NIIT
│   ├── credits.py       CTC, EITC, care, education, saver's
│   ├── dependents.py    IRC s.152, computed once
│   └── calculator.py    ordering
└── oklahoma/
    └── calculator.py    Form 511, full-year residents
```

## Money

Every monetary value is a `Decimal`. `float` is **rejected**, not converted:

```python
def money(value):
    if isinstance(value, float):
        raise TypeError("float is not accepted as a monetary amount")
```

A float that reaches the engine is a bug upstream. Silently accepting it bakes
an unrepresentable value into a return.

Rounding has exactly two documented policies — `to_cents` for entered amounts
and `to_whole_dollars` for form lines — and both use `ROUND_HALF_UP`, matching
the Form 1040 instruction, not Python's banker's-rounding default.

Intermediate arithmetic runs at full precision. Rounding at every step would
make a total depend on how many steps there happened to be. Results are
quantized to cents at one documented boundary, in `_RoundedResult`; the trace
deliberately keeps full precision, because its job is to show the arithmetic,
not to report the answer.

## Rules as versioned data

```json
"standard_deduction": {
  "_source": "pl-119-21",
  "_note": "Believed amended above the Rev. Proc. 2024-40 figures. REQUIRES OFFICIAL VERIFICATION.",
  "base": { "SINGLE": "15750", "MARRIED_FILING_JOINTLY": "31500" }
}
```

Every number is a **quoted string**, converted to `Decimal` on load. A JSON
float bracket edge of `48475.00000000001` is not theoretical. The loader
rejects JSON floats outright.

Each section cites a source id resolving to full provenance: authority,
document, tax year, URL, retrieval date. `rules.citation("standard_deduction")`
returns it, and it flows into the calculation trace, so a taxpayer asking why
their deduction is what it is gets an answer with an authority attached.

A missing rule raises `RuleLookupError` rather than returning a default. A
silent zero would turn "the engine has no authority for this" into a wrong
number on a tax return.

## The certification gate

```
DRAFT ──────────▶ UNDER_REVIEW ──────────▶ PRODUCTION
transcribed       reviewer checking        verified against primary
not verified      each value               sources, approval recorded
      │                  │                          │
      └──────────────────┴──── not filable ─────────┘
                                                    │
                                              filable
```

The engine computes happily with a `DRAFT` rule set — refusing would make the
product undevelopable — but stamps `rule_sets_certified_for_filing = False`,
and `EFileProvider.preflight` refuses to transmit.

The failure mode is a taxpayer who cannot file yet. That is recoverable. The
alternative failure mode is a taxpayer who files a wrong return under penalty
of perjury, and that is not.

## Calculation ordering

Order is not arbitrary and is the part most easily got wrong:

1. Income other than Social Security.
2. Adjustments not dependent on SE tax — needed because the taxable portion of
   Social Security is computed net of them.
3. Self-employment tax, which depends on business income.
4. Total adjustments, now including the deductible half of SE tax.
5. AGI.
6. Deduction — depends on AGI via the medical floor and SALT phase-down.
7. Taxable income.
8. Tax, using the capital gain worksheet when applicable.
9. Other taxes.
10. Credits: nonrefundable first, then refundable.
11. Payments, then refund or balance due.

Steps 2 and 4 exist because IRC s.86 and s.164(f) are mutually circular. The
Form 1040 instructions resolve it with a modified AGI computed without the
student loan interest deduction, which is what the provisional figure is.

## Two primitives that carry most of the risk

**Graduated brackets.** Each slice of income is taxed at its own bracket's
rate, with no rounding between brackets. Applying the marginal rate to the
whole amount is the classic error; a test asserts a taxpayer $1 over a bracket
edge pays at most the marginal rate more.

**Phase-outs, of two different kinds.** Ratable phase-outs reduce linearly.
Increment phase-outs — IRC s.24(b)(2)'s "$50 for each $1,000 *or fraction
thereof*" — round the excess **up** to a whole increment. A taxpayer $1 over a
$1,000 boundary loses the full $50, not five cents. Computing this as a smooth
ratio is a real bug that overstates refunds, and it is tested directly.

A third table shape is distinct from both: the Saver's Credit is a cliff-edge
table where the whole contribution is multiplied by one rate, with no blending.
Using bracket-walking there would be wrong, so `rate_for_amount` is separate
and named differently.

## Credits: ordering is the whole ballgame

Nonrefundable credits reduce tax to zero and no further. Refundable credits are
paid out regardless. The Additional Child Tax Credit is defined as the part of
the CTC that liability could not absorb — so it can only be computed after
every other nonrefundable credit has been applied.

Getting the order wrong understates refunds for exactly the taxpayers who can
least afford it.

Nonrefundable credits are applied **one at a time** against remaining tax, in
Schedule 3 order, rather than as a single capped sum. A capped sum produces
the right total and the wrong per-credit figures — and those figures are what
the taxpayer sees and what the Oklahoma return reads to compute its own
credits. A taxpayer told they received a $1,000 Saver's Credit that was worth
nothing to them has been misinformed even if their refund is correct. This was
a real bug, caught by a test.

## The capital gain worksheet

Qualified dividends and long-term gains are taxed at 0/15/20%, and the
preferential slice is **stacked on top of** ordinary income when walking the
capital gains schedule. A taxpayer with low ordinary income gets 0% on the
gains that fit below the first threshold.

Applying the capital gains rate to the gain in isolation gives the wrong answer
for anyone near a threshold. Implementing only the ordinary path silently
overtaxes anyone holding an index fund that paid a qualified dividend — which
was, in fact, a bug in the first version of this engine, caught by a test
asserting that dividends are taxed more lightly than the same amount of wages.

## Oklahoma

Oklahoma starts from **federal AGI**, not federal taxable income:

```
Federal AGI + additions − subtractions = Oklahoma AGI
             − deduction (state amounts, not federal)
             − personal exemptions
             = Oklahoma taxable income
```

Three things the code gets right that are easy to get wrong:

- **State withholding is counted only when the document names Oklahoma.**
  Summing every `state_income_tax` box regardless of state code would refund
  money Oklahoma never received.
- **Oklahoma's standard deduction is state law and does not track the federal
  amount.** A test asserts the two differ, so a refactor cannot quietly make
  Oklahoma follow the federal figure.
- **US Treasury interest is subtracted under 31 U.S.C. s.3124**, which is
  federal law binding on every state and therefore not dependent on the
  Oklahoma rule file.

Part-year and nonresident returns (Form 511-NR) are **not implemented**. The
calculator raises rather than approximating, and `capability.py` blocks the
return before it gets there.

## The capability matrix

`assess_capability` inspects a return and reports every situation the engine
cannot handle or handles only approximately. Findings at `NOT_SUPPORTED` or
`REQUIRES_TAX_PROFESSIONAL` block filing.

Every check corresponds to a real gap in the calculation code. When a gap
closes, the check is deleted in the same change — a capability matrix that
drifts out of step with the engine is worse than none, because it teaches
people to ignore it.

## Testing strategy

Three kinds of test, and the difference matters:

**Structural tests** assert relationships that hold regardless of rule values:
a refund equals payments minus tax; taxable income is never negative; the
capital gain worksheet never produces more tax than ordinary rates; no income
level makes a taxpayer worse off in absolute terms. These survive rule updates.

**Primitive tests** exercise bracket walking and phase-outs directly, because a
bug there is wrong on every return in both jurisdictions and is far harder to
diagnose through a full calculation.

**Regression baselines** lock the computed result for five synthetic
taxpayers. They assert *current behaviour, not correctness* — every value came
from DRAFT rules. Their job is to make change visible: a refactor that moves
ten thousand refunds by $40 fails loudly, and the diff shows exactly who was
affected.

All test data is synthetic. Identifiers use area 9xx with a group outside every
ITIN range, which is issued by nobody and therefore cannot collide with a real
person. The e-file validator correctly rejects such numbers, and accepts them
as a warning only on channels that cannot file — derived from the channel
rather than from configuration, so a live path cannot be made to accept test
data by changing a setting.
