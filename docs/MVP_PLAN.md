# MVP implementation plan

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Foundation: monorepo, auth, database, security baseline | **Done** |
| 2 | Taxpayer workflow: status, dependents, income, deductions, credits | **Done** (engine + API; interview UI partial) |
| 3 | Document AI: upload, OCR, extraction, verification | **Modelled, not built** |
| 4 | Tax engine: federal, Oklahoma, validation, year versioning | **Done** |
| 5 | Return generation: data model, forms, explanations, review | **Done** |
| 6 | Payments: checkout, confirmation, refunds | **Partial** — no processor |
| 7 | E-file abstraction: mock provider, acknowledgments | **Done** |
| 8 | Security and compliance: pen test, audit, IR, monitoring | **Partial** |
| 9 | Official e-file onboarding | **Not started** |
| 10 | Production | **Not started** |

## What works end to end today

Verified by an integration test against a real database:

```
Register → sign in → enrol MFA → enter personal information
   → start a 2025 return → save answers → federal + Oklahoma calculated
   → refund shown with a full explainable breakdown
   → checkout for $14.99 → payment recorded
   → finalize  ✗ REFUSED: tax rules for this year are not yet verified
```

The refusal at the end is the correct behaviour and is asserted by the test.
A taxpayer who has paid, satisfied MFA and passed every other gate still
cannot file a return computed from unverified rules.

## What is left, in dependency order

### Blocking for any real filing

1. **Verify both 2025 rule sets.** A qualified reviewer, following
   `docs/TAX_RULE_GOVERNANCE.md`. Nothing downstream matters until this is
   done. Oklahoma needs more scrutiny than federal.
2. **Obtain an EFIN and complete IRS suitability.** Months of lead time.
3. **Obtain MeF schemas and implement validation levels 4 and 5.**
4. **Pass ATS and Oklahoma acceptance testing.**
5. **Integrate a payment processor,** with server-side verification or a
   signed webhook. Today the confirmation endpoint returns 501 in production
   rather than trusting the client — correct, but it means payment does not
   work in production at all.
6. **Integrate a KMS** and make `LocalKeyring` unreachable in production.
7. **Move rate limiting to shared state.** Per-process limiting does not
   survive horizontal scaling.
8. **Implement an IRC s.7216 consent flow** before any use of return data
   beyond preparing the return.
9. **Penetration test and independent security assessment.**
10. **Write a WISP, an incident response plan and a breach notification
    procedure.**
11. **Have qualified tax and legal professionals review all customer-facing
    language.** The current disclaimers were drafted by engineers.

### Product completeness

12. **Finish the interview UI.** The engine handles far more than the current
    screens collect: dependents, itemized deductions, 1099 income, retirement
    income, Social Security. The API accepts all of it today.
13. **Build the document pipeline.** Tables, provenance types and the
    "unverified extraction blocks filing" rule exist and are tested; nothing
    populates them. Malware scanning must land before upload is enabled.
14. **Notifications.** Registration deliberately does not fake an email send,
    so the gap is visible rather than silent.
15. **Return PDF generation.** A taxpayer needs a copy of what was filed.
16. **Admin and support UI.** The API exists; there is no interface.
17. **Privacy centre.** Export and deletion request handling.

### Engine coverage worth adding

Ordered by how many real taxpayers each unblocks:

18. Premium Tax Credit / Form 1095-A — very common, currently blocks filing.
19. The new tip and overtime deductions — flagged as unsupported today, and
    a taxpayer with tips may be owed more than we show.
20. Multiple Schedule C businesses; depreciation; home office.
21. Schedule E rental income.
22. Form 511-NR for part-year and nonresident Oklahoma returns.
23. Alternative Minimum Tax.
24. Amended returns (Form 1040-X).

## Sequencing advice

Items 1 and 2 have the longest lead times and no dependencies. Start both
immediately; everything else can proceed in parallel.

Do **not** build the authorized-provider adapter before items 2-4 are complete.
An adapter that exists before the authorizations do is a configuration mistake
away from attempting a real transmission, which is why `get_provider` refuses
to construct anything but the mock and says so.

## Success criteria for the MVP

A taxpayer with a W-2, an Oklahoma address and possibly some dependents can:
create an account, enter their information, upload a W-2 and confirm what was
read, see a federal and Oklahoma refund with every figure explained, pay
$14.99, sign, file through an authorized transmitter, and track the return to
acceptance.

Every step of that is built today except the document upload and the final
transmission — and the transmission is blocked by regulation, not by
engineering.
