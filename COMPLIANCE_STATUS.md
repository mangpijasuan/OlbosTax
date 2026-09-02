# OlbosTax compliance status

**Owner:** Olbos Technologies, LLC
**Last updated:** 2026-09-02
**Overall status: NOT AUTHORIZED TO FILE TAX RETURNS**

---

## Read this first

This document exists so that nobody has to guess whether OlbosTax may
transmit a tax return. The answer today is no.

Two independent conditions must both be satisfied before a single real return
can be filed, and neither is close to met:

1. **No tax rule set is certified.** The 2025 federal and Oklahoma rule sets
   are marked `DRAFT`. Their values were transcribed during development and
   have not been verified against IRS or Oklahoma Tax Commission publications
   by a qualified reviewer.
2. **No authorized e-file path exists.** OlbosTax has no EFIN, no transmitter
   relationship, and no implemented adapter to any authorized provider. There
   is deliberately no code in this repository that can send a return to a
   taxing authority.

The software enforces both. `TaxEngine` stamps every computation with
`rule_sets_certified_for_filing`, and `EFileProvider.preflight` refuses to
submit when it is false. The `Acknowledgment` model cannot be constructed with
status `ACCEPTED` on a non-live channel, and a database check constraint
rejects such a row even if application code is bypassed.

**A checklist is not compliance.** The presence of the items below says
nothing about whether any of them has been done. Each carries an explicit
status, and `UNKNOWN` means exactly that — nobody has established the answer.

---

## Status legend

| Status | Meaning |
| --- | --- |
| `UNKNOWN` | Nobody has established what is required or whether it is met. |
| `IN_PROGRESS` | Work is under way; a named owner exists. |
| `COMPLETED` | The work is done, per the person responsible. |
| `VERIFIED` | Independently confirmed, with evidence recorded. |

Only `VERIFIED` should be relied on for a decision about going live.

---

## 1. IRS e-file provider requirements

| Item | Status | Notes |
| --- | --- | --- |
| Determine which e-file provider role(s) OlbosTax needs | `UNKNOWN` | Electronic Return Originator, Transmitter, Software Developer and Intermediate Service Provider are distinct roles with distinct requirements. Which apply depends on whether OlbosTax transmits directly or through a third party. **REQUIRES OFFICIAL VERIFICATION** against current IRS Publication 3112 and the e-file application process. |
| IRS e-Services account | `UNKNOWN` | Prerequisite for the e-file application. |
| EFIN (Electronic Filing Identification Number) | `UNKNOWN` | Not applied for. |
| Suitability check (each principal and responsible official) | `UNKNOWN` | Includes credit, tax compliance, and criminal background checks on named individuals. Lead time is measured in months. |
| PTIN, if any individual prepares returns for compensation | `UNKNOWN` | Whether this applies depends on whether any person at Olbos Technologies prepares or signs returns, as distinct from providing self-preparation software. **REQUIRES OFFICIAL VERIFICATION.** |
| Software Developer registration and assigned software id | `UNKNOWN` | |
| Modernized e-File (MeF) schemas and business rules for the tax year | `UNKNOWN` | Not obtained. Their absence is why validation levels 4 and 5 are unimplemented. |
| Assurance Testing System (ATS) scenarios passed | `UNKNOWN` | Not started. |
| IRS Publication 1345 handling, signature and record retention rules | `UNKNOWN` | Governs signature methods, Form 8879, and what must be retained. Design assumptions in the codebase were **not** derived from the current publication. |
| IRS Publication 4557 safeguards | `IN_PROGRESS` | Security controls are implemented (see the threat model); they have not been assessed against the publication's specific requirements. |
| Written Information Security Plan (WISP) | `UNKNOWN` | Required of tax preparers under the FTC Safeguards Rule. Not written. |

## 2. Oklahoma Tax Commission requirements

| Item | Status | Notes |
| --- | --- | --- |
| OTC software developer / e-file registration | `UNKNOWN` | Not applied for. |
| Oklahoma e-file specifications and schemas for the tax year | `UNKNOWN` | Not obtained. |
| Oklahoma acceptance testing | `UNKNOWN` | Not started. |
| Confirm whether Oklahoma requires federal acceptance first | `UNKNOWN` | Many states accept returns only through the federal/state e-file program, which would make IRS approval a hard prerequisite. **REQUIRES OFFICIAL VERIFICATION.** |
| Form 511 and supporting schedule requirements for the tax year | `UNKNOWN` | The Oklahoma rule set was transcribed without access to the current packet. |

**Explicit prohibition, honoured in this codebase:** OlbosTax does not scrape,
automate, reverse-engineer or impersonate OkTAP or any other Oklahoma Tax
Commission system. There is no code that contacts an OTC endpoint.

## 3. Tax rule verification

This is the item most likely to be underestimated. The engine's arithmetic is
tested; the *parameters it operates on* are not verified.

| Rule set | Certification | Status | Notes |
| --- | --- | --- | --- |
| `federal/2025` | `DRAFT` | `UNKNOWN` | Transcribed from Rev. Proc. 2024-40 and P.L. 119-21 during development. Tax year 2025 is unusually risky: several inflation-adjusted amounts published in the revenue procedure were amended by later legislation before taking effect, so for each parameter a reviewer must establish *which figure controls*. |
| `oklahoma/2025` | `DRAFT` | `UNKNOWN` | Lower confidence than the federal set. The rate schedule, standard deduction, personal exemption and every subtraction cap need independent confirmation. Oklahoma enacted rate legislation in 2025 whose first effective tax year must be confirmed — applying a future year's rate cut to tax year 2025 would understate every Oklahoma return. |

Verification procedure is in [`docs/TAX_RULE_GOVERNANCE.md`](docs/TAX_RULE_GOVERNANCE.md).

## 4. Taxpayer authorization and consent

| Item | Status | Notes |
| --- | --- | --- |
| Authorization to file, captured and retained | `COMPLETED` | `consents` and `signatures` tables; signature is bound to a hash of the exact return content, so a return altered after signing no longer matches. |
| Consent to disclose or use tax return information (IRC s.7216) | `UNKNOWN` | s.7216 imposes criminal penalties for unauthorized disclosure or use of return information, with specific consent-form requirements. **No s.7216 consent flow is implemented.** Required before any use of return data beyond preparing the return itself. |
| Form 8879 or equivalent signature authorization | `UNKNOWN` | Depends on the provider role determined in section 1. |
| Legal review of all customer-facing language | `UNKNOWN` | The disclaimers in the web footer were drafted by engineers and have **not** been reviewed by a qualified tax or legal professional. |

## 5. Privacy and data protection

| Item | Status | Notes |
| --- | --- | --- |
| Privacy policy | `UNKNOWN` | Route exists; no reviewed text. |
| Data retention schedule | `IN_PROGRESS` | Retention is configurable in the schema. The actual required periods for tax records are **not** established. |
| Data subject access / export | `IN_PROGRESS` | Audit events defined; export not implemented. |
| Deletion handling that respects retention obligations | `COMPLETED` | Deletion is a status change plus data minimisation; foreign keys use `RESTRICT` so filed returns cannot be deleted with an account. |
| Applicable state privacy law analysis | `UNKNOWN` | Oklahoma has no comprehensive consumer privacy statute as of writing, but taxpayers may reside elsewhere. **REQUIRES OFFICIAL VERIFICATION.** |
| Gramm-Leach-Bliley / FTC Safeguards Rule applicability | `UNKNOWN` | Tax preparers are generally treated as financial institutions under the Safeguards Rule. If it applies, a WISP and specific controls are mandatory. |

## 6. Information security

| Item | Status | Notes |
| --- | --- | --- |
| Encryption in transit | `IN_PROGRESS` | HSTS and secure headers set; TLS terminates at infrastructure not yet provisioned. |
| Field-level encryption of taxpayer identifiers | `COMPLETED` | AES-256-GCM with per-row associated data and key ids for rotation. |
| Key management | `IN_PROGRESS` | Interface supports a KMS-backed keyring. **No KMS is integrated.** `LocalKeyring` refuses to run outside development. |
| Password storage | `COMPLETED` | Argon2id, 64 MiB memory cost, with transparent rehash on parameter change. |
| MFA | `COMPLETED` | TOTP; mandatory for staff, and for signing, filing and changing bank details. |
| RBAC and least privilege | `COMPLETED` | Six roles with explicit permission sets; sensitive data access requires elevation plus a logged justification. |
| Audit logging | `COMPLETED` | Append-only, enforced by database privileges. |
| Log redaction | `COMPLETED` | Filter and formatter layers; covers tracebacks. |
| Rate limiting and account lockout | `IN_PROGRESS` | Implemented in-process. **Does not survive horizontal scaling** — see the threat model. |
| Malware scanning of uploads | `UNKNOWN` | Column and status exist; no scanner integrated. |
| Penetration test | `UNKNOWN` | Not performed. |
| Independent security assessment | `UNKNOWN` | Not performed. |
| Incident response plan | `UNKNOWN` | Not written. |
| Breach notification procedure | `UNKNOWN` | Not written. Note that tax data breaches carry IRS notification obligations distinct from state breach laws. |

## 7. Payments

| Item | Status | Notes |
| --- | --- | --- |
| PCI DSS scope | `IN_PROGRESS` | No card data touches OlbosTax systems by design; only a processor reference is stored. SAQ level depends on the integration chosen. |
| Payment processor integration | `UNKNOWN` | **Not implemented.** The confirmation endpoint refuses to operate in production rather than trusting a client-supplied reference. |
| Refund handling | `IN_PROGRESS` | States modelled; no processor to execute against. |
| Price disclosure | `COMPLETED` | $14.99, server-authoritative, disclosed before checkout with no conditional fees. |

## 8. Operations

| Item | Status | Notes |
| --- | --- | --- |
| Environment separation | `COMPLETED` | Settings refuse unsafe production configuration; live providers are rejected outside production. |
| Backups and point-in-time recovery | `UNKNOWN` | Not provisioned. |
| Restore testing | `UNKNOWN` | Never performed. A backup that has not been restored is a hypothesis. |
| Documented RPO / RTO | `UNKNOWN` | Not set. |
| Monitoring and alerting | `UNKNOWN` | Not provisioned. |
| Disaster recovery plan | `UNKNOWN` | Not written. |

---

## What must be true before the first real return is filed

In dependency order:

1. A qualified reviewer verifies every value in both 2025 rule sets against
   primary sources and records the approval. Both rule sets reach
   `PRODUCTION`.
2. Olbos Technologies obtains an EFIN and completes IRS suitability.
3. IRS MeF schemas and business rules are obtained, and validation levels 4
   and 5 are implemented against them.
4. ATS testing passes; Oklahoma acceptance testing passes.
5. A payment processor is integrated and confirmation is verified
   server-side against the processor, not the client.
6. A KMS is integrated and `LocalKeyring` is unreachable in production.
7. Rate limiting moves to shared state.
8. An IRC s.7216 consent flow is implemented.
9. A penetration test and an independent security assessment are completed
   and findings remediated.
10. A WISP, an incident response plan and a breach notification procedure
    exist and have been exercised.
11. Qualified tax and legal professionals review all customer-facing language.
12. An authorized provider adapter is implemented **after** steps 2-4, and
    `assert_transmission_allowed` is exercised in staging.

Until every one of these is `VERIFIED`, the honest description of OlbosTax is:
**tax preparation software that cannot yet file returns.** The product says so
on its own landing page.
