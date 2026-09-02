# OlbosTax security threat model

## What an attacker wants

A tax filing system holds an unusual concentration of exactly what identity
theft requires: name, SSN, date of birth, address, employer, income, and bank
account details — for a whole household, verified, in one record.

Ranked by harm:

1. **Redirecting a refund.** Immediate, irreversible, and the taxpayer bears
   the loss and the recovery burden.
2. **Bulk SSN exfiltration.** Enables fraudulent returns in future years
   against people who have no relationship with OlbosTax any more.
3. **Filing a fraudulent return** in a real taxpayer's name.
4. **Blocking a legitimate filing** near a deadline, causing penalties.
5. **Tampering with the audit record** to conceal any of the above.

## Assets and controls

| Asset | Exposure | Control |
| --- | --- | --- |
| SSN / ITIN | Identity theft | Value type masks by default; AES-256-GCM at rest with per-row context; only last four in the clear |
| Bank routing/account | Refund theft | Same encryption; MFA + re-auth to change; high-severity security event on change |
| Return content | Fraud, extortion | Owner-scoped access on every query; encrypted sensitive fields |
| Credentials | Account takeover | Argon2id (64 MiB, t=3, p=4); constant-work verification |
| Session tokens | Impersonation | Short-lived, pinned algorithm, backed by a revocable session row |
| Audit log | Concealment | Append-only, enforced by database privilege |
| Tax rule sets | Systematically wrong returns | Certification gate; approval bound to a content hash |

## Threats and mitigations

### T1 — Insecure direct object reference

*A user changes `/returns/123` to `/returns/124`.*

Mitigated structurally: **no function in the returns service loads a return by
id alone.** `user_id` is a required argument on every loader, so an IDOR cannot
be introduced by forgetting a check — it would require fabricating a user id,
which is a different and far more visible mistake.

"Not found" and "not yours" return an identical 404 with an identical body.
Distinguishing them lets an attacker map which ids exist, which is what an IDOR
probe measures. Tested directly with two real users.

### T2 — Account takeover to redirect a refund

Layered:
- MFA required to change bank details, sign, finalize, or submit.
- Bank changes raise a `HIGH` security event and an audit entry.
- The signature is bound to a hash of the return content, so altering a return
  after signing invalidates the signature and blocks submission.
- Sessions are revocable; "sign out everywhere" includes the calling session,
  because the caller may be the attacker.

**Residual risk:** an attacker who compromises the account *before* MFA
enrolment faces only the password. Mitigation: require MFA before the first
bank detail entry. Not yet implemented.

### T3 — Credential stuffing and enumeration

- Throttling keyed by both account and source address, with exponential
  backoff — the two keys catch different attacks.
- Login failures return one message whether the account exists or not.
- Registration always reports success; the real owner is told by email.
- Password reset gives an identical response either way.
- Verification spends equal work on a nonexistent account, so timing does not
  leak existence.

**Known gap:** throttling is per-process. A horizontally scaled deployment
lets an attacker spread attempts across workers. Needs shared state before
production. Documented rather than hidden, and listed in
`COMPLIANCE_STATUS.md`.

### T4 — Sensitive data in logs

The highest-frequency real-world leak. Defended in three independent layers:

1. **Value types.** `str()`, `repr()`, `format()` and JSON serialization all
   mask. The accidental path is the safe one.
2. **A logging filter** redacting messages, arguments and structured extras
   across every library, not just first-party code.
3. **A logging formatter** redacting the rendered line, including tracebacks.

The third layer exists because a traceback is assembled *after* filters run and
inlines source text and exception reprs — a path the filter never sees. Found
by a test that asserted an SSN could not reach the stream and initially failed.

Model `__repr__` is overridden to print only identity, so a debugger session or
a stack frame does not dump a taxpayer row.

### T5 — Database compromise

A stolen replica, a SQL injection, or a restored backup yields:
- SSNs and bank details as ciphertext (keys are not in the database).
- Everything else in the clear — deliberate, so diagnostics and analytics stay
  possible on data whose disclosure is not the harm.

Associated data binds each ciphertext to its row, so an attacker with write
access cannot move an SSN between taxpayers to impersonate one.

**Residual risk:** an attacker with application-server access can reach the
keys. Field encryption defends against database-only compromise, which is the
common case; it is not a defence against full host compromise.

### T6 — Malicious file upload

Planned pipeline: type allowlist, size cap, malware scan, encrypted object
storage, then extraction. Files are stored by opaque key, never served from an
application origin.

**Status: the scan is not integrated.** The column and status exist; nothing
populates them. Upload must not be enabled for real users until it is.

### T7 — Tampering with the audit record

`REVOKE UPDATE, DELETE, TRUNCATE` from the application role on `audit_logs`,
`security_events`, `sensitive_data_accesses`, `consents`, `signatures` and
`efile_acknowledgments`. The application can insert and read, nothing more.

Without this, "the audit log is immutable" is a claim about the code we happen
to have written. Verified against a live role: INSERT succeeds, UPDATE is
denied, ordinary tables are unaffected.

### T8 — Insider access to taxpayer data

- Six roles with explicit permission sets; support sees masked data by default.
- Unmasking requires `SUPER_ADMIN`, a written justification of at least twenty
  characters, and writes a permanent record naming the administrator, the
  taxpayer, the field and the reason.
- Granting access and disclosing the value are separate steps, keeping the
  disclosure path small enough to audit.
- Staff MFA is mandatory — there is no workflow where an administrator needs
  taxpayer records with a password alone.

### T9 — Filing a return computed from wrong rules

The threat nobody models, and the one most likely to cause harm at scale here:
software that is secure, well-tested, and computes tax wrong.

Mitigated by the certification gate. `DRAFT` rules compute estimates;
`preflight` refuses to transmit. Rule approval is bound to a content hash, so a
rule set edited after approval no longer matches and the approval no longer
applies.

### T10 — Supply chain

Dependencies are pinned to exact versions. `.gitignore` excludes `.env`, keys
and PEM blocks. No secret is committed.

**Gaps:** no lockfile hash verification in CI, no SBOM, no dependency scanning.

### T11 — Cross-site scripting and clickjacking

- API responses: `default-src 'none'`, `frame-ancestors 'none'`, `nosniff`,
  `no-store`.
- Web: strict CSP, `X-Frame-Options: DENY`, `frame-ancestors 'none'`. A tax
  flow inside someone else's iframe is a clickjacking setup for redirecting a
  refund.
- React escapes by default; no `dangerouslySetInnerHTML` anywhere.

**Gap:** the web CSP still allows `'unsafe-inline'` for scripts because Next.js
inlines a bootstrap script. Should move to a nonce-based CSP.

### T12 — Token theft from browser storage

The access token is held **in memory only**, never in `localStorage`. Local
storage is readable by any script that reaches the page, and the consequence
here is not a hijacked cart but a stranger's SSN and the ability to redirect
their refund. The cost is that a refresh signs the user out, which is the
correct trade for a tax session.

## Trust boundaries

```
Untrusted ─────────────────────────────────────────────────
  Browser, uploaded files, payment webhooks, provider responses
─────────────────────────────────────────────────── validate
Semi-trusted
  API process (holds decryption keys during a request)
─────────────────────────────────────── authenticate + authorize
Trusted
  Database, KMS, object storage
```

Everything crossing inward is validated. Nothing crossing outward carries an
unmasked identifier unless a named, logged decision was made.

## Unmitigated risks, stated plainly

1. **Rate limiting does not survive horizontal scaling.**
2. **No KMS is integrated.** The interface is right; nothing is behind it.
3. **No malware scanning.** Upload must stay disabled.
4. **No penetration test or independent assessment has been performed.**
5. **`'unsafe-inline'` remains in the web CSP.**
6. **No incident response or breach notification procedure exists.** Tax data
   breaches carry IRS obligations distinct from state breach laws.
7. **Payment confirmation is not verified against a processor.** The endpoint
   returns 501 in production rather than trusting the client — correct, but it
   means payment does not work in production at all.

Items 1-4 and 6-7 are blocking for production. They are listed in
`COMPLIANCE_STATUS.md` with the same status.
