# OlbosTax

Tax preparation and filing software from Olbos Technologies, LLC.

**Your taxes. One price. $14.99.** Federal and Oklahoma individual income tax
returns, with no upgrades, no surprise fees, and no percentage of your refund.

---

## Status: cannot file returns

OlbosTax prepares and validates returns. It **cannot transmit them**, and it
says so on its own landing page. Two conditions block filing:

1. **No tax rule set is certified.** The 2025 federal and Oklahoma rule sets
   are marked `DRAFT` — their values were transcribed during development and
   have not been verified against IRS or Oklahoma Tax Commission publications
   by a qualified reviewer.
2. **No authorized e-file path exists.** Olbos Technologies holds no EFIN and
   no transmitter relationship. There is deliberately no code in this
   repository that can reach a taxing authority.

Both are enforced in software, not documentation. See
[`COMPLIANCE_STATUS.md`](COMPLIANCE_STATUS.md).

---

## Layout

```
apps/
  api/        FastAPI service + Alembic migrations
  web/        Next.js application
packages/
  tax-schema/ canonical data model, sensitive value types
  tax-engine/ deterministic federal + Oklahoma calculation
  security/   passwords, encryption, redaction, sessions
services/
  efile/      provider interface + mock implementation
docs/         architecture, engine, e-file, threat model, governance
tests/        engine, security, e-file, API
```

## Running it

Requires Python 3.11+, Node 22+, and PostgreSQL 16.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e packages/tax-schema -e packages/tax-engine \
                      -e packages/security -e services/efile -e apps/api

createdb olbostax
export DATABASE_URL="postgresql+psycopg://olbostax:olbostax@localhost:5432/olbostax"
(cd apps/api && ../../.venv/bin/alembic upgrade head)

.venv/bin/uvicorn olbostax_api.app:app --reload          # API on :8000
(cd apps/web && npm install && npm run dev)              # web on :3000
```

Or `docker compose -f infrastructure/docker/docker-compose.yml up`.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

217 tests. The API tests need PostgreSQL and skip cleanly without it.

```bash
.venv/bin/python -m pytest -m tax_engine    # calculation
.venv/bin/python -m pytest -m security      # security controls
.venv/bin/python -m pytest -m regression    # locked baselines
```

## Design commitments

These are the decisions that shaped everything else.

**No language model touches a calculation.** Tax is computed by deterministic
code against versioned rules. An assistant may explain a figure the engine
produced; nothing turns a model output into a number on a return.

**A simulation is never presented as a real filing.** An `Acknowledgment`
cannot be constructed with status `ACCEPTED` on a non-live channel, and a
database check constraint rejects such a row even if application code is
bypassed.

**Unverified tax rules produce estimates, not returns.** Rule sets carry a
certification status. Uncertified rules compute, but the e-file layer refuses
to transmit, and the taxpayer is told why.

**Sensitive values mask themselves.** `SSN`, `BankAccountNumber` and
`RoutingNumber` render masked from every rendering path. Reading the real value
requires `reveal()`, which is greppable and shows up in review.

**Money is exact.** `Decimal` throughout; `float` is rejected at the boundary
rather than converted. Rounding happens at two documented points, half away
from zero, matching the Form 1040 instruction.

**We say what we cannot do.** The capability matrix is published without an
account, unimplemented validation levels are declared through the API, and the
landing page tells visitors filing is not open before they enter anything.

## Documentation

| Document | Contents |
| --- | --- |
| [`COMPLIANCE_STATUS.md`](COMPLIANCE_STATUS.md) | What is required to file, and what is actually done |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System shape, boundaries, technology assessment, known gaps |
| [`docs/TAX_ENGINE_DESIGN.md`](docs/TAX_ENGINE_DESIGN.md) | Calculation ordering, rounding, credits, testing strategy |
| [`docs/EFILE_DESIGN.md`](docs/EFILE_DESIGN.md) | Provider interface and the invariants that protect it |
| [`docs/DATABASE_DESIGN.md`](docs/DATABASE_DESIGN.md) | Schema, immutability, encryption boundaries |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Threats, mitigations, and unmitigated risks |
| [`docs/TAX_RULE_GOVERNANCE.md`](docs/TAX_RULE_GOVERNANCE.md) | How a rule set becomes filable |
| [`docs/MVP_PLAN.md`](docs/MVP_PLAN.md) | What is done and what remains |

## Development rules

Do not: fabricate tax rules, fabricate e-file approval, scrape OkTAP, use real
taxpayer data, commit secrets, use a language model as a tax authority, or mark
a return accepted without a real acknowledgment.

Do: write tests for calculations before the calculations, cite official
sources, version tax rules, preserve auditability, and fail safely. When an
official requirement is unknown, mark it `REQUIRES OFFICIAL VERIFICATION` and
continue with a mock rather than inventing an answer.

## Legal

OlbosTax is tax preparation software. It is not a tax adviser, an accountant,
or a law firm, and it does not provide tax or legal advice. Users are
responsible for reviewing their returns before authorising them to be filed.

OlbosTax is not affiliated with, endorsed by, or approved by the Internal
Revenue Service, the Oklahoma Tax Commission, or any other government agency.

© 2026 Olbos Technologies, LLC.
