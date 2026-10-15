# Fit inside free limits: Postgres on Railway, Auth and Storage stay on Supabase

Written 2026-10-15. Supabase free tier: database 0.5 GB, egress 5 GB, storage 1 GB. Measured
today: database 1000 MB, egress 17 GB, storage 0.68 GB and growing about 60 MB a day.

Goal: Supabase holds only Auth and the evidence bucket, both inside the free limits with
headroom; the backend and its Postgres run on Railway inside (or close to) the Hobby $5 credit.

## What was measured

| Area | Finding |
|---|---|
| Egress | `novelty.story_vectors` re-downloaded ~1.83M embeddings (~3 KB each, ~5.6 GB). `insider_transaction` read 9.4M rows, `price_bar` 7.4M + 2.5M, `story_cluster` 6.4M, 2.37M `pg_advisory_unlock_all` (connection churn). A 13F read (quoted column names, 3 calls x 443K rows) has no caller in the backend: probably an external script. |
| Database | public schema 978 MB. 13F 156 MB (97% of rows are 2024-2026), `source_record_evidence` 232 MB (443K of 584K rows are one link per 13F holding), insider tables ~145 MB (60 MB was bloat), embeddings/chunks ~97 MB, prices/FX/factors ~93 MB. |
| Storage | Bucket `evidence`: 243 HTML files averaging 2.6 MB (613 MB), 50 JSON (61 MB). All created in the last 10 days. Referenced from `source_document_version.archive_path` (231 files, SEC 595 MB, ESEF 72 MB) and `public.evidence`. Only ~39 MB is not cited by `source_evidence`. |
| Backend RAM (7 days) | floor 0.31 GB, average 0.50 GB, peak 1.14 GB, limit 2 GB; CPU average 0.008 vCPU. One container runs API + worker (`start.sh`). Peaks line up with long jobs: `news_ingest` (avg 15 min, max 37), `price_history` (avg 35 min). |
| Chromium | Installed in the image, off in every job (`browser_fallback` false). Starts lazily, one page at a time, ~300 MB while in use. |
| Web app | Uses Supabase for login/MFA and, in `sync-status-provider.tsx`, reads `job` and `job_step` directly plus a Realtime channel. All other data goes through the backend API. |
| Queue | `steward/jobs/queue.py` calls `pgmq.send`: pgmq is in use, not just installed. |

## Phase 0: no-loss cleanup (do first; shrinks the dump and gives a fallback)

Done in code:
- `news/novelty.py`: cached story centroids, vectors downloaded only for changed stories.
- `disclosure/adjudication.py`: `insider_sample` and `ownership_sample` sample in SQL instead of
  loading whole tables.
- `VACUUM FULL public.insider_filing` (reclaims ~60 MB).

Written, not yet applied: `supabase/migrations/20261124100000_retention_policy.sql` (drops the
72 MB `source_record_evidence_asof` index, adds `retention_policy`, `private.run_retention()`
and a nightly pg_cron job). Apply it with the owner's approval; its rollback is next to it.

To do:
1. Replace the 443K per-holding evidence rows with the filing-level rows that already exist
   (~120 MB). Check every reader of `source_record_evidence` where `domain_table =
   'institutional_holding'` first. Keep a rollback.
2. Store embeddings (`news_embedding.vector`, `document_chunk`) as `halfvec` (~30-40 MB).
3. Find the caller of the quoted-column 13F query (script or tool outside the backend).

## Phase 1: Storage stays on Supabase, compressed

- `steward/storage.py`: gzip HTML/JSON/XML in `put`, detect the gzip magic bytes in `get` and
  decompress, so old and new objects both read. The key stays the SHA-256 of the original
  bytes.
- The two `signed_url` callers (`api/routes.py` fact evidence, `api/disclosure_routes.py`
  archived deal documents) hand the URL to the browser, which would receive gzip bytes. Add a
  backend endpoint that streams the decompressed object and point both at it (or confirm that
  Supabase serves `Content-Encoding: gzip` and use that).
- Backfill: a one-off job that rewrites existing objects compressed. Expected 5-8x on HTML:
  ~680 MB to ~100-140 MB.
- Retention for the bucket: delete archives not cited by `source_evidence` and older than 90
  days (~39 MB today); keep anything a fact or deal cites. Run it from the backend scheduler
  and delete the `archive_path` row's object in the same step.

## Phase 2: backend RAM

Baseline 0.31 GB is two Python processes importing the same code; peaks are long jobs.
1. Set `MALLOC_ARENA_MAX=2` as a Railway variable. Measure for 3 days against the 0.50 GB
   average.
2. Cap `fetch` result sizes in the long jobs (price_history, news_ingest) and stream with
   cursors where they load whole result sets. Review the remaining ~850 `fetch` call sites for
   unbounded queries; prefer `LIMIT`, keyset pagination, or aggregation in SQL.
3. Replace wide `select *` on hot paths with column lists. Most of the ~60 hits are
   single-row lookups or row locks and are fine; the heavy ones are fixed above.
4. Chromium (decision needed): keep the browser build and leave `browser_fallback` and
   `fulltext_browser_fallback` off until a source is actually blocked, or turn them on now (no
   cost while idle, ~300 MB per blocked page). `WITH_BROWSER=0` only saves image size and
   makes the flags unusable.

### RAM audit findings (2026-10-15, read-only sweep of `apps/backend/src`)

`news_ingest` and `price_history` are bounded per item. `price_history` holds one symbol's ~1.3K
bars at a time; its 35 minutes are mostly deliberate pacing sleeps, so it is not a RAM problem.
The risk is overlap: the worker runs up to 4 jobs at once in one process
(`MAX_CONCURRENT_QUEUED_JOBS = 4`, `worker/__main__.py:59`). No `fetchall`, no unbounded
`asyncio.gather`; 13F data is already streamed to disk.

| # | Where | What is held | Est. peak | Fix | Status |
|---|---|---|---|---|---|
| 1 | `news/dedupe.py` `recent_vectors` | up to 5000 x 768 floats as Python tuples, every ingest | ~120 MB | `array('f')` | done |
| 2 | `ownership/nport_collect.py:359`, `sources/nport.py:208` | up to 64 MB XML buffered twice, whole tree parsed | 300-600 MB | stream to a temp file, `iterparse`, cap ~20 MB | open |
| 3 | `disclosure/fetch.py:263-283` | list of chunks plus the joined bytes, up to 128 MB | up to 128 MB | write to a temp file while hashing | open |
| 4 | `retrieval/search.py:147-161` | pgvector-absent fallback loads 4000 x 768 floats per query | ~100 MB per request | lower scan limit | not hit in production (pgvector is installed); Railway's Postgres must keep pgvector |
| 5 | `sources/xbrl.py:291,401` | 20-32 MB filings parsed whole | 200-500 MB | clear elements while iterating | open |
| 6 | `fundamentals/ir.py:94-110,159` | up to 25 MB `BytesIO`, then pypdf / `fromstring` | ~100 MB | spool to disk, cap pages | open |
| 7 | `sources/market.py:177-230` | FX backfill expands ~60K `FxRate` objects | ~50 MB, once | fetch in yearly chunks | open |
| 8 | `valuation/read.py:509-515` | `fx_rate` already filtered by source and date; only the currency is not | small | none: the table needs the base currency too, so a currency filter risks wrong conversions | checked, left |
| 9 | `quant/french_store.py:51-60` | `load_factors` takes `since` and its only caller (`portfolio/factors.py:392`) passes it | small | none | checked, left |
| 10 | `engine/data_audit.py:110-130` | two unbounded `price_bar` fetches (~20K rows) | small | compare in SQL | open |
| 11-12 | `portfolio/exante.py` `_BOOK_CACHE`, `xray/service.py` `_cache` | `_BOOK_CACHE` keeps one entry (cleared on insert); `_cache` drops expired entries on every insert | low | none | checked, left |
| 14 | `news/pipeline.py:1813-1906` | all collected items plus 72 h of recent members | modest | process per source | open |
| - | `worker/__main__.py:59` | 4 jobs at once in one process | multiplies the above | 4 to 2 only with a separate lane for short jobs: `news_ingest` (up to 37 min) and `price_history` (up to 2 h) could otherwise starve the 15-minute `monitor` and `intraday_prices` | open |

## Phase 3: Postgres on Railway

Checked against the live database on 2026-10-15:
- Extensions in use: `vector`, `pgcrypto`, `uuid-ossp`, `pgmq` (one queue, `jobs`), plus
  `pg_cron` (added by the retention migration), `pg_stat_statements` and `supabase_vault`.
  Railway's Postgres image has the first three; pgvector needs the pgvector template.
- pgmq: replaced by the `public.job_queue` table (migration `20261126100000`,
  `jobs/queue.py` rewritten with the same four functions). Done in code; pending messages are
  copied over after the deploy (see the migration comment).
- `auth.users`: 23 `public` tables have `owner_id` foreign keys to it (cascade); the copy keeps
  them valid with a one-row `auth.users` stub holding the owner. One function uses `auth.uid()`
  and 24 of 474 policies mention `auth.`; 207 policies call `private.is_owner()`. Restore with stub `auth.uid()` / `auth.jwt()` functions and
  the roles `anon`, `authenticated`, `service_role`, or drop those policies: the backend checks
  the owner itself (`OWNER_USER_ID`) and connects as `app_backend`.
- 234 tables in `public`, roles granted: `anon`, `app_backend`, `authenticated`, `postgres`,
  `service_role`.
- `supabase_vault`: check for use before the move; the retention job and the stub approach do
  not need it.
- Live sync status: serve `job` and `job_step` from a backend endpoint (poll or SSE) and remove
  the direct Supabase reads and the Realtime channel from `sync-status-provider.tsx`.
- pg_cron: move the retention job to the backend scheduler (worker already uses APScheduler).

Progress (2026-10-15):
- Done in code: live sync status now polls `GET /jobs` / `GET /jobs/{id}` (no Supabase table
  reads or Realtime in `sync-status-provider.tsx`); the retention job also runs from the worker
  scheduler (`Worker.run_retention`, 03:17 daily), so the pg_cron job is not needed.
- Provisioned: service `Postgres` (image `pgvector/pgvector:pg17`, the upstream pgvector image
  rather than an unverified marketplace template), 3 GB volume in europe-west4-drams3a,
  Railway-generated passwords, tuning flags below. A Railway image service ignores its start
  command for one-off jobs (a throwaway `db-restore` service exited at once with no output), so
  the copy runs from a workstation: `deploy/db-move/run-local.sh` (docker, `postgres:17`) over a
  temporary TCP proxy.
- Restore rehearsal done 2026-10-15: public schema copied in about 13 minutes; row counts of
  the big tables identical; 630 MB on Railway against 741 MB on Supabase (no bloat). Smoke test
  (`deploy/db-move/smoke.sql`, as `app_backend`): vector data, queue read/delete, sequences,
  the append-only guard and the owner foreign key all behave. It found that `app_backend` had no
  USAGE on schema `private` (so the worker retention job would have failed on Supabase too);
  fixed by migration `20261129100000`.
- What the copy needs that `pg_dump` cannot give `app_backend`: `private` schema (applied from
  `private_schema.sql`), sequence values (reset from the data), and a temporary
  `grant select on all sequences in schema public to app_backend` on Supabase (revoke after).
- Not started: cutover, backups, removing the TCP proxy and the `db-restore` service.

Steps:
1. Provision Postgres (pgvector) in project `dependable-vitality`, same environment, private
   network only; no public proxy except while restoring.
2. Tune for a ~1 GB data set and a single backend: `max_connections=20` (API pool 5, worker 7,
   headroom), `shared_buffers=128MB`, `effective_cache_size=384MB`, `work_mem=4MB`,
   `maintenance_work_mem=64MB`, `autovacuum_max_workers=2`. Expected resident memory
   150-250 MB. Re-measure with `get-service-metrics` and adjust.
3. Rehearse: `pg_dump` the public schema (after Phase 0), restore to Railway, run the backend's
   tests and a day of jobs against the copy.
4. Cutover: stop the worker, final dump/restore, change `DATABASE_URL`, start, watch
   `/health` and job runs. Keep the Supabase database read-only for 14 days as a fallback,
   then drop it.
5. Backups: nightly `pg_dump` compressed to a Railway bucket, 7 daily and 4 weekly kept;
   test a restore once.
6. Remove `public` tables from Supabase after the fallback window so only Auth remains.

## Phase 4: other Railway cost cuts (done 2026-10-15: measured, decisions recorded)

Measured over 7 days: jobs ran about 34 hours of 168; memory is flat at 0.485 GB average
(0.50 before `MALLOC_ARENA_MAX=2`); CPU about 0.004 vCPU. Railway bills the always-on baseline,
not the job minutes, so:
- Schedules: left as they are. `monitor` is 672 runs of about 1 s (16 minutes a week) and
  `intraday_prices` 320 runs of 18 s (98 minutes); moving either off-hours saves nothing on the
  bill and would delay alerts. `news_ingest` (923 min) and `price_history` (314 min, mostly
  pacing sleeps) are the long jobs; they only matter for peaks, which the bounded-memory audit
  already covers.
- One replica in one region (already so); limits do not change the bill, usage does.
- No second service for the worker unless Chromium is enabled: two always-on services double
  the baseline.
- Egress to Supabase falls to near zero once the database is on the private network (cutover).
- Left to measure after the cutover: the Postgres service's real RAM against the 150-250 MB
  expectation, then tune `shared_buffers`.

### Re-creating the Postgres service (the rehearsal copy was deleted)

The rehearsal service was removed so no idle copy or exposed password stays around. Recreate it
the same way, with fresh generated passwords, just before the cutover:
- Service `Postgres`, image `pgvector/pgvector:pg17`, region `europe-west4-drams3a`, a 3 GB
  volume at `/var/lib/postgresql/data` in the same region.
- Variables: `POSTGRES_USER=postgres`, `POSTGRES_DB=steward`,
  `PGDATA=/var/lib/postgresql/data/pgdata`, and `POSTGRES_PASSWORD` / `APP_BACKEND_PASSWORD`
  as `${{secret(40)}}`.
- Start command: `docker-entrypoint.sh postgres -c max_connections=30 -c shared_buffers=128MB
  -c effective_cache_size=384MB -c work_mem=4MB -c maintenance_work_mem=64MB -c
  autovacuum_max_workers=2 -c max_wal_size=512MB -c listen_addresses=*`.
- Then, with a temporary TCP proxy and the three values in `~/db-move.env`:
  `sh deploy/db-move/run-local.sh` (about 13 minutes), `deploy/db-move/smoke.sql` as
  `app_backend`, remove the proxy and the env file. For the cutover itself, stop the worker
  first and repeat the copy so it is current.

## Expected cost (Hobby, usage-based; verify against Railway's current price list)

| | Estimate per month |
|---|---|
| Backend RAM (0.4-0.5 GB average) | $4-5 |
| Postgres RAM (0.15-0.25 GB) | $1.5-2.5 |
| CPU | $0.2-0.5 |
| 2 GB volume, backups bucket | $0.3-0.6 |
| Total | about $6-8.5, i.e. $1-3.5 above the $5 credit |

Compared with Supabase Pro at $25.

## Order and checks

1. Phase 0 (apply retention migration, evidence-link change, halfvec). Check: database size,
   backend tests, Supabase advisors.
2. Phase 1 (compression and backfill). Check: bucket size, a fact's evidence link opens.
3. Phase 2 measurements for 3 days. Check: Railway memory average and peak.
4. Phase 3 rehearsal, then cutover. Check: job success rate for a week, backup restore.
5. Phase 4 tuning after a week of real numbers.

## Decisions needed

- Chromium: on now, or off until a source is blocked (default: off).
- 13F history window if the database still exceeds the Railway volume plan (default: keep all).
- Supabase Pro stays the fallback if the cutover is abandoned.
