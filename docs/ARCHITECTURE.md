# OlbosTax architecture

## 1. Assessment of what was here

The repository was empty at the start of this work: no commits, no files, no
existing architecture to preserve or work around. Everything described below
was built in this session, which means there is no legacy constraint on the
design and also no prior art to validate it against.

## 2. The shape of the system

```
                 ┌──────────────────────────────────────────┐
   Taxpayer ───▶ │  apps/web        Next.js, mobile-first   │
                 └───────────────────┬──────────────────────┘
                                     │ HTTPS, bearer token
                 ┌───────────────────▼──────────────────────┐
                 │  apps/api        FastAPI                 │
                 │  auth · taxpayers · returns · payments   │
                 │  efile · admin                           │
                 └──┬──────────┬──────────┬─────────────┬───┘
                    │          │          │             │
      ┌─────────────▼──┐  ┌────▼──────┐ ┌─▼──────────┐ ┌▼──────────────┐
      │ packages/      │  │ packages/ │ │ packages/  │ │ services/     │
      │ tax-engine     │  │ security  │ │ tax-schema │ │ efile         │
      │                │  │           │ │            │ │               │
      │ federal/       │  │ passwords │ │ enums      │ │ base.py       │
      │ oklahoma/      │  │ encryption│ │ documents  │ │ mock_provider │
      │ rules/ (JSON)  │  │ redaction │ │ taxpayer   │ │ validation    │
      │ calculations/  │  │ sessions  │ │ results    │ │ factory       │
      │ capability.py  │  │           │ │ trace      │ │               │
      └────────────────┘  └───────────┘ └────────────┘ └───────┬───────┘
                    │                                          │
             ┌──────▼───────┐                    ┌─────────────▼──────────┐
             │ PostgreSQL   │                    │ Authorized transmitter │
             │ 19 tables    │                    │   NOT INTEGRATED       │
             └──────────────┘                    └────────────────────────┘
                                                              │
                                                   ┌──────────▼──────────┐
                                                   │  IRS / Oklahoma TC  │
                                                   └─────────────────────┘
```

Everything to the right of the e-file service is unbuilt, deliberately. See
`COMPLIANCE_STATUS.md`.

## 3. Why the boundaries fall where they do

### The tax engine is separate from everything

`packages/tax-engine` imports `packages/tax-schema` and nothing else. It has
no database access, no HTTP, no clock beyond a timestamp on its output, and no
model inference. Two consequences follow, and both are the point:

**It can be reviewed by someone who knows tax law but not web frameworks.** The
component whose correctness determines a taxpayer's liability is small, pure,
and readable on its own.

**It is deterministic.** The same input and the same rule versions produce
byte-identical output. That is what makes the regression suite meaningful, what
lets a stored return be recomputed years later, and what makes the calculation
trace trustworthy as an audit record rather than a plausible reconstruction.

### Tax rules are data, not code

Rule parameters live in versioned JSON with per-section source citations. A
rule change is then a data diff a tax professional can review line by line
against a Rev. Proc., rather than a code change reviewed by engineers.

It also makes the certification gate possible. A rule set carries a
certification status, and an uncertified one produces estimates that the
e-file layer refuses to transmit. Encoding that in Python constants would give
nowhere to put the status.

### E-file is an interface with one implementation

OlbosTax prepares returns; transmitting them is a separate regulated activity.
The abstraction exists so the rest of the product never learns which
transmitter is in use — and so the distinction between a simulation and a real
filing cannot blur. See `docs/EFILE_DESIGN.md`.

### Sensitive values are types, not conventions

`SSN`, `BankAccountNumber` and `RoutingNumber` render masked from `__str__`,
`__repr__`, `__format__` and JSON serialization. Reading the real value
requires calling `reveal()`, which is greppable and shows up in review. The
accidental path is the safe one.

This is the single highest-leverage decision in the codebase. It converts "do
not log the SSN" from a rule people must remember into a property of the type,
and it caught a real bug during development: the storage encryption was
encrypting the *masked* form, because `str()` on the value masks by design.

## 4. Data flow for one calculation

```
Taxpayer answers a question
   │
   ▼
PUT /api/v1/returns/{id}          object-level authorization by owner
   │
   ▼
returns_service.save_input        refuses if the version is finalized
   │
   ▼
crypto.encrypt_return_input       SSNs and bank details → ciphertext
   │
   ▼
JSONB column on tax_return_versions
   │
   ▼
returns_service.calculate         decrypt → TaxReturnInput
   │
   ▼
TaxEngine.compute
   ├── assess_capability          what we cannot compute → findings
   ├── classify_dependents        IRC s.152, computed once
   ├── calculate_federal_return   income → AGI → deduction → tax → credits
   ├── calculate_oklahoma_return  starts from federal AGI
   └── CalculationTrace           every step, with form line and citation
   │
   ▼
tax_calculations                  snapshot stored, never overwritten
   │
   ▼
API response                      masked identifiers, trace, findings
```

The trace is stored alongside the result rather than regenerated on demand,
because regenerating it means re-running whatever code is deployed today
against a return computed months ago — which answers a different question from
the one being asked.

## 5. Technology assessment

| Choice | Why | What it costs |
| --- | --- | --- |
| Python for the engine | `Decimal` is first-class and exact; the code reads close to the statute; the tax and accounting ecosystem is here. | Slower than a compiled language, which does not matter for arithmetic on one return. |
| `Decimal` everywhere, `float` rejected at the boundary | Binary floating point cannot represent `0.10`. On a signed tax return that is not a rounding curiosity. | Every money value needs explicit construction. Worth it. |
| FastAPI | Pydantic models are the validation layer and the schema at once, so the API cannot drift from the types the engine consumes. | Async framework used mostly synchronously; acceptable for a database-bound workload. |
| PostgreSQL | Check constraints, JSONB, exact `NUMERIC`, and privilege control fine enough to make a table append-only. | Operational weight versus a managed document store. Necessary here. |
| SQLAlchemy + Alembic | Explicit migrations that can be reviewed; naming conventions so constraint names are stable across environments. | Verbosity. |
| JSONB for return input | Written and read as a unit; must be reconstructible byte-for-byte after the relational schema evolves. | Not directly queryable per field. Headline figures are denormalised onto `tax_calculations` for that. |
| Next.js | Server components for public pages, client components for the interview; one language across the UI. | A React app for what are largely forms. Justified by the interactive review and calculation breakdown. |
| No ORM-level encryption | The cipher needs a per-row context string that a column type cannot see. | Encryption is explicit in the service layer, which is also where it is reviewable. |

## 6. What is deliberately absent

- **No LLM anywhere near a calculation.** The assistant described in the
  product spec explains figures the engine produced. There is no code path
  where a model output becomes a number on a return.
- **No live e-file provider.** Not a stub, not a feature flag. Adding one is
  gated on the registrations in `COMPLIANCE_STATUS.md`.
- **No payment processor.** The confirmation endpoint returns 501 in
  production rather than trusting a client-supplied reference.
- **No analytics on tax data.** No third-party script receives return content.

## 7. Known gaps

These are real and are not hidden behind a passing test suite:

1. **Federal and Oklahoma e-file validation (levels 4 and 5) are
   unimplemented.** They need the current published schemas. Writing
   plausible rules from memory would produce software that passes its own
   validation and is rejected by the authority — worse than having none,
   because it manufactures confidence.
2. **Rate limiting is per-process.** A horizontally scaled deployment lets an
   attacker spread attempts across workers. Needs shared state.
3. **Key management is not integrated.** The interface is right; no KMS is
   behind it.
4. **Document upload and OCR are modelled but not built.** The tables,
   provenance types and the "unverified extraction blocks filing" rule all
   exist and are tested; the pipeline that populates them does not.
5. **Notifications are not implemented.** Registration deliberately does not
   fake an email send, so the gap is visible rather than silent.
6. **The Oklahoma rule set is the least trustworthy artefact in the
   repository.** Its structure is right; its numbers need a tax professional.
