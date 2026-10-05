# Backup, restore and retention

What to back up, how to restore it, how long raw provider data is kept, and what to do when a
key expires or a source is removed. The restore step is exercised by
`apps/backend/tests/test_backup_restore.py` (needs `STEWARD_TEST_DATABASE_URL` and the
PostgreSQL client tools); retention by `apps/backend/tests/test_lifecycle_phase8.py`.

## What is state

| Where | What | Lost if the host is lost? |
| --- | --- | --- |
| Postgres (Supabase) | Everything the app knows: holdings, theses, observations, evidence, config versions, audit trails | Yes, unless backed up |
| Host secrets manager or `.env` | Provider and alert keys | Yes; re-issue keys, nothing is derived from them |
| Code and migrations (git) | Schema, `supabase/migrations`, rollbacks in `supabase/rollback` | No |

Derived figures (metrics, returns, coverage) are recomputed from stored observations and
filings. They do not need their own backup, but the observations they come from do.

## Back up

On Supabase, use the project's daily backups (and a point-in-time-recovery add-on if you
have one). In addition take a logical dump you control, at least weekly and before every
migration:

```bash
# Password comes from PGPASSWORD or ~/.pgpass, never the command line.
export PGHOST=<db host> PGPORT=5432 PGUSER=postgres
pg_dump --format=custom --no-owner --no-privileges -f steward-$(date +%F).dump postgres
```

Keep at least the last four weekly dumps off the host (another machine or a private bucket).
Dumps contain your portfolio: encrypt them at rest.

## Restore

Always restore into an empty database first and check it before pointing anything at it.

```bash
createdb steward_restore
pg_restore --no-owner --no-privileges -d steward_restore steward-YYYY-MM-DD.dump
# Exit code 1 with "errors ignored" is normal when the target server lacks an extension
# the source had; read the messages before trusting the result.
```

Then, against `steward_restore`:

1. Compare row counts of the tables you care about with the source (the automated test does
   this for every public table).
2. Run the migrations that are newer than the dump: `supabase db push` against the restored
   database, or apply the missing files from `supabase/migrations` in order.
3. Re-create the backend login role's password (`alter role app_backend with login
   password '...'`) and point `DATABASE_URL` at the restored database.
4. Start the API, call `GET /health/ready`, then open Data sources and Activity: the worker
   heartbeat should be fresh.

## Retention

Raw provider payloads are evidence for a bounded time; normalized observations are kept.
`prune_raw_payloads` (the `raw_retention` job) does, per `RetentionPolicy`. Every period must
be at least 30 days; the defaults are:

- `source_observation.raw`, 400 days (`source_observation_raw_days`): emptied, except for
  payloads still referenced by an open source conflict.
- `macro_payload_archive.payload`, 180 days (`macro_payload_days`): set to null; the row, its
  hash and its source URL stay, so the observation remains explainable.
- `provider_fetch_audit`, 400 days (`fetch_audit_days`): rows older than that are deleted.

Normalized observations are never deleted or altered by retention. Run the job with the
`dry_run` parameter first to see what would go; the period lengths can be overridden as job
parameters.

## Key expiry and source removal

- **A key expires or is revoked.** The provider's access state changes to unverified or
  denied after the next call and Data sources shows the endpoint as such; the app keeps
  every value it already stored and marks later periods unavailable. Replace the key in the
  secrets manager, restart the worker, and use the endpoint's reset-block action once the new
  key works.
- **A source is removed or turned off.** Disable it in Config: Jobs. Stored observations
  stay with their provenance; they are no longer refreshed, and coverage for them ages into
  stale rather than disappearing. Remove the provider's endpoints from the manifest only after
  confirming nothing still cites them.

## Migration rollback

Each free-data-sources migration has a rollback in `supabase/rollback`. A rollback that would
destroy data stops with a precondition error instead of dropping rows
(`tests/test_phase8_rollback_data.py`). Take a dump before rolling back anyway.
