# StewardAI

A single-user, self-hosted portfolio copilot: it reads a Trading212 portfolio, stores the
owner's strategy and per-holding theses, and uses language models (OpenAI and Google) to monitor, explain and research.
The full design is in
[AI_Portfolio_Copilot_Technical_Product_Description.md](AI_Portfolio_Copilot_Technical_Product_Description.md).

**Status: phases 0-5 built, including the gap-review tasks (phase 1b, phase 2 follow-ups,
phase 2b).** Phase 1 syncs Trading212 daily with ČNB/ECB FX and shows drift, Overview and
Holdings. Phase 1b adds the strategy and jobs editors, config version history with diff,
rollback and JSON export/import, the transactions view, deployment packaging (`deploy/`)
with a worker heartbeat, and a live-data sign-off report. Phase 2 checks each results
release (archived, read by a model into figures with verbatim snippets, validated in code,
judged against the thesis KPIs); its follow-ups add report-date triggers with retries,
XBRL reconciliation and restatements, the owner review queue, the thesis board and
per-call cost accounting. Phase 2b resolves identity and structure (OpenFIGI, ESMA,
GLEIF, Wikidata), stores price history and risk metrics, FX history and the exchange
calendar, and raises lifecycle events. Phase 3 is the news pipeline: feeds, IR pages,
EDGAR filings and full-text search, RNS/EQS (off by default), Finnhub and macro sources are
ingested, clustered, linked, classified by a model and corroborated; confirmed events
alert through Telegram or email under the notification rules, get a Sonnet impact note
when severe, and daily and weekly digests cite every claim. Rule breaches, price moves,
macro surprises, failed jobs, missed runs and coverage gaps alert once each. Phase 4
hardens verification: every fact, verdict, alert, news item, story and report sentence has
wrong / irrelevant / useful feedback (a wrong fact is quarantined and its verdicts
re-judged; sources lose trust and drop a tier), a claim verifier checks each written
sentence against its cited snippets, rule edits show a 7-day replay preview before saving,
the Activity page shows coverage gaps and the job log, and a CI gate re-runs the live evals
whenever a prompt, model, schema or eval set changes. Phase 5 adds deep research under
configurable research profiles (figures written by code from stored records, prose by the
model, every sentence cited; a killed run resumes from its last step), a monthly review
whose compliance table lists every strategy rule and whose proposed actions must cite a
rule or KPI, a chat panel that answers with sources or says it has none and starts jobs
only on confirmation, Form 13F ownership from the SEC data sets, and full-text report search.

Not yet verified: the acceptance criteria that need live data or time (consecutive
scheduled runs on an always-on host, the Realtime 2-second update, the uptime alert,
Overview totals against the Trading212 app, 48-hour ingest runs, source figures matched
by hand), and the live-model evals (golden extraction 98 %, classification 85 %, claim
verifier 20/20), which run on Gemini and need a key: add the `GOOGLE_API_KEY` repository
secret and run `uv run python -m steward.verify.evals_gate --write-baseline` once. Run
`uv run python -m steward.signoff` on the host for the phase 1 checks. Phase 6 is not
started.

## Repository layout

```
apps/backend/          Python 3.12: FastAPI API + worker, one package (uv)
apps/web/              Next.js App Router frontend (TypeScript, Tailwind, shadcn/ui)
supabase/migrations/   the one database schema, plain SQL, applied with the Supabase CLI
supabase/config.toml   local Supabase stack config (auth: sign-up off, TOTP MFA on)
deploy/                Docker Compose and systemd units for the API and worker host
.github/workflows/     CI: backend lint/types/tests (incl. pgmq), frontend types/lint/tests/build
```

The contract between the two apps is `apps/backend/openapi.json`, generated from FastAPI.
The frontend turns it into TypeScript types with `npm run gen:api`, and CI fails when either
side is stale.

## Setting up a Supabase project

1. Create a project (Free tier is fine for development) in an EU region.
2. Apply the migrations:
   ```bash
   npx supabase login
   npx supabase link --project-ref <project-ref>
   npx supabase db push
   ```
3. Give the backend role a password (SQL editor). The migration creates it without one so no
   secret lives in git:
   ```sql
   alter role app_backend with login password '<generated secret>';
   ```
4. Auth settings in the dashboard (`supabase/config.toml` only configures the local stack):
   - Authentication → Sign In / Providers: turn **off** "Allow new users to sign up".
   - Authentication → Multi-Factor: enable **TOTP** (enroll and verify).
   - Authentication → Users → Add user: create the owner account.
5. Register the owner, so RLS lets that user (and only that user) read app data. The
   `register_owner` migration does this automatically when the project has exactly one
   auth user at `db push` time; otherwise run:
   ```sql
   insert into private.app_owner (user_id) values ('<owner auth user id>');
   ```
6. Check pgmq: `select * from pgmq.list_queues();` runs with no error and lists the `jobs`
   queue (created by the Phase 1 migration).
7. Check Storage: the Phase 2 migration creates the private `evidence` bucket
   (`select id, public from storage.buckets;`). The backend writes to it with the project's
   secret key (`SUPABASE_SECRET_KEY`); the browser only ever gets short-lived signed URLs.
8. Alerts: set up a Telegram bot and/or Resend (see `.env.example`), then send a test alert
   from Config → Notifications. The worker sends it within about 15 seconds (or when quiet
   hours end).

Then fill in the environment: `cp .env.example .env` in the repository root. That one file
is read by both apps, and `.env.example` explains where to get every value (Supabase URL
and keys, the `app_backend` connection string, the owner id, the Trading212 key).

Supabase's direct database host is IPv6-only unless the project has the IPv4 add-on. A
backend host without IPv6 must use the session pooler (user `app_backend.<project-ref>`).

## Running locally

```bash
cp .env.example .env                                                 # once; fill in values
cd apps/backend && uv sync && uv run uvicorn api.main:app --reload   # API on :8000
cd apps/backend && uv run python -m worker                           # scheduler
cd apps/web && npm install && npm run dev                            # web on :3000
```

To fill a new or empty database once, run the data jobs in order (portfolio, reference data,
two years of prices and FX, holidays, macro, the earnings calendar, 13F ownership, news):

```bash
cd apps/backend && uv run python -m worker.backfill            # or name the jobs to run
```

See [apps/backend/README.md](apps/backend/README.md) and [apps/web/README.md](apps/web/README.md).

## Database access model

- RLS is enabled on every table. The frontend (`authenticated`) gets `SELECT` only, and only
  when `private.is_owner()` is true for the signed-in user. `anon` gets nothing.
- Frontend writes go through the API, which validates them with the Pydantic config schemas.
- The backend connects directly as `app_backend`: explicit per-table grants plus a permissive
  policy, no superuser, no `BYPASSRLS`, no access to anything it was not granted.
- `config_version` rows are immutable apart from `active`; one active version per domain.
- `fact` rows never change their figure or source (a trigger enforces it); only the review
  state moves. A restated value is a new row pointing at the one it restates. `evidence` and
  `verdict` are append-only, and the backend has no delete grant on any of the three.
- `job` and `job_step` are in the `supabase_realtime` publication, so the web app's sync
  indicator updates live. The migration adds them only where the publication exists.
