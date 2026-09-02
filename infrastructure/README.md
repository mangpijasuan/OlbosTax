# Infrastructure

## Environments

| Environment | Purpose | E-file | Payments |
| --- | --- | --- | --- |
| `local` | A developer's machine | mock | mock |
| `development` | Shared dev | mock | mock |
| `staging` | Pre-production | mock or provider test | processor test mode |
| `production` | Real taxpayers | authorized provider (not yet integrated) | real processor |

`assert_transmission_allowed` refuses a live provider outside production, so a
staging deployment cannot file real returns from test data.

## Configuration that must come from a secret manager

Never from a file in the repository, and never from a plain environment
variable in production:

- `OLBOSTAX_SESSION_SECRET`
- `OLBOSTAX_IP_HASH_SALT`
- `DATABASE_URL`
- Payment processor keys
- Field encryption keys — these should come from a KMS, with the application
  receiving only unwrapped data keys. `LocalKeyring` refuses to run outside a
  development environment for exactly this reason.

`Settings` refuses to start a production process when the session secret is
weak, the IP salt is missing, the payment provider is `mock`, SQL echo is on,
or any CORS origin is plain HTTP. Failing at boot is far better than serving
traffic misconfigured: the first is a loud, immediate, fixable problem, and the
second is discovered by a taxpayer.

## Database roles

Two roles, because the application should not be able to rewrite its own audit
log:

```sql
CREATE ROLE olbostax_migrate LOGIN PASSWORD '...';   -- owns the schema
CREATE ROLE olbostax_app     LOGIN PASSWORD '...';   -- the application
GRANT USAGE ON SCHEMA public TO olbostax_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO olbostax_app;
```

The initial migration then revokes `UPDATE`, `DELETE` and `TRUNCATE` from
`olbostax_app` on the append-only tables. It runs only when the role exists, so
a developer's local database and CI are unaffected.

Verify after deploying:

```sql
SET ROLE olbostax_app;
INSERT INTO audit_logs (id, event_type, occurred_at) VALUES ('t','TEST',now());  -- succeeds
UPDATE audit_logs SET event_type = 'X';                                          -- must fail
```

## Backups and recovery

Not provisioned. Before production:

- Automated encrypted backups with point-in-time recovery.
- **Restore testing on a schedule.** A backup that has never been restored is
  a hypothesis, not a backup.
- Documented RPO and RTO. Neither is set.

Tax records carry retention obligations that outlive an account. Backup
retention must be reconciled with the deletion policy: honouring a deletion
request while a backup still holds the data is a policy failure, and deleting a
backup that holds legally required records is a different one.

## Deployment order

1. Apply migrations with `olbostax_migrate`.
2. Verify `alembic check` reports no drift.
3. Deploy the API.
4. Verify `/health` and `/api/v1/system/filing-readiness`.
5. Deploy the web app.

Migrations run separately from application start so a failed migration does not
leave a partially-migrated database serving traffic.
