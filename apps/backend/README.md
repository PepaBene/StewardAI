# StewardAI backend

One Python package, two processes:

| Process | Command | Role |
| --- | --- | --- |
| API | `uv run uvicorn api.main:app` | FastAPI: the frontend's action calls. Verifies the Supabase JWT on every non-public route. |
| Worker | `uv run python -m worker` | APScheduler for timed jobs (schedules from the Jobs config) and the pgmq `jobs` queue consumer for on-demand runs. |

Both share `steward` (settings, logging, DB access, config schemas, source adapters).

```
src/
  steward/           shared library
    config/          Pydantic schemas per config domain (strategy, theses, jobs, holding
                     profiles, verification) + versioned store
    documents/       fetching source documents, HTML/PDF to text, snippet matching
    earnings/        earnings check: locate, extract (Claude), validate, verdicts, calendar,
                     golden-set evaluation (`python -m steward.earnings.golden`)
    news/            news pipeline: collectors, normalise, cluster, link, classify (Claude
                     Haiku), corroborate, digest; classification eval (`python -m
                     steward.news.eval`)
    notify/          alert routing (rules, quiet hours, cap, throttle), Telegram and email,
                     the dispatcher
    fx/              ČNB and ECB reference-rate collectors, currency conversion
    jobs/            job runner (advisory lock, job/job_step log), pgmq queue, registry
    portfolio/       sync job, day snapshots, drift, API read models
    sources/         SEC EDGAR and Finnhub adapters
    trading212/      read-only Trading212 client (+ `python -m steward.trading212`)
    ai.py            Anthropic client, per-step token and cost accounting
    storage.py       evidence archive (Supabase Storage REST)
    db.py            asyncpg pool as app_backend
    logging.py       logging with secret scrubbing
    settings.py      environment settings
  api/               FastAPI app, routes, JWT verification, OpenAPI export
  worker/            scheduler + queue consumer entrypoint
tests/               pytest (DB tests need STEWARD_TEST_DATABASE_URL)
tests/golden/        golden set of real earnings releases with labelled figures
tests/news/          100 labelled news items (classification) and 10 syndication cases
openapi.json         committed API contract; the frontend generates its types from it
```

## Setup

```bash
uv sync                    # Python 3.12 + dependencies from uv.lock
```

Settings come from the environment, falling back to the **repository-root `.env`**, which
the frontend shares (`cp .env.example .env` in the repo root; `/.env.example` says where to
get every value). Empty `KEY=` lines count as unset. On the live host, use its secrets
manager instead of a file.

## Everyday commands

```bash
uv run uvicorn api.main:app --reload         # API on :8000, GET /health
uv run python -m worker                      # worker; logs "scheduler started"
uv run python -m api.export_openapi          # regenerate openapi.json after API/schema changes
uv run python -m steward.trading212          # print positions from the Trading212 demo account
uv run python -m steward.trading212 --env live

uv run python -m steward.earnings.golden      # golden set through validator + verdicts
uv run python -m steward.earnings.golden --live --record runs/$(date +%F)   # measure Claude
uv run python -m steward.news.eval             # labelled news set through the eval harness
uv run python -m steward.news.eval --live      # measure classification (target >= 85 %)

uv run pytest                                # unit tests; DB tests skip without a database
STEWARD_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The DB tests create a throwaway database, apply a small Supabase stand-in
(`tests/sql/supabase_shim.sql`: roles, `auth.uid()`, default grants) and every migration
in `supabase/migrations`, then check the access model as `app_backend`, `authenticated`
(owner and non-owner) and `anon`. The pgmq migration is skipped on servers without the
extension; CI runs against a Postgres image that has it.

## API

| Route | What it does |
| --- | --- |
| `GET /portfolio/overview` | Totals, allocation, drift against the active strategy, freshness |
| `GET /portfolio/holdings` | Every open position of the latest snapshot, largest first |
| `GET /holdings/{ticker}` | Position, thesis, profile, KPI verdicts with history, every fact |
| `GET /facts/{id}/evidence` | Signed URL of the archived source, and the text around the snippet |
| `POST /facts/{id}/review` | Accept or reject a fact the validator held; re-queues verdicts |
| `POST /jobs/portfolio-sync` | Queue a sync now (202); the worker runs it |
| `POST /jobs/earnings-check` | Queue an earnings check for `{ticker, report_date?, url?}` |
| `POST /jobs/thesis-verdicts` | Re-judge KPIs from stored facts (`{ticker?}`), no model calls |
| `POST /jobs/earnings-calendar` | Refresh next report dates now |
| `GET /news` | News items, newest first, with their story's severity and confidence; filters `ticker`, `severity`, `confidence`, `tier`, `story` (repeat a parameter for "any of") |
| `GET /alerts` | Recent alert decisions: sent, held, suppressed (with why), failed |
| `POST /alerts/test` | Route a test alert through the saved notification rules; the worker sends it |
| `GET /reports`, `GET /reports/{id}` | Digests, each sentence with its cited news items and facts |
| `POST /jobs/news-ingest`, `POST /jobs/digest` | Run the news pipeline / write a digest now |
| `GET`/`PUT /config/{strategy,theses,jobs,holding-profiles,verification,news-sources,event-rules,notifications}` | Active config version / save a new one |

All but `/health` need the owner's MFA-verified Supabase JWT.

## Design notes

- **Portfolio sync** (`portfolio_sync`, default daily 22:30 Prague, set in the Jobs config):
  Trading212 account and positions, then the strategy's reference rates (ČNB or ECB; if the
  source is down, stored rates up to 10 days old), then one `position_snapshot` row per
  position per day plus an `account_snapshot` (a re-run replaces that day), then new
  orders, dividends and cash movements (the first run backfills the full history, later
  runs stop at the first known item), then drift. Every stage is a `job_step`. A position
  whose currency has no reference rate fails the job before anything is written.
- **One run per job type.** Each run takes a Postgres advisory lock for its job type; a
  second run waits (`job.status = 'waiting'`) and never writes alongside the first, across
  processes too.
- **FX.** Rates are stored as home-currency units (CZK for ČNB, EUR for ECB) per one unit,
  next to the figure exactly as published. Every base-currency value is quantity x price x
  rate, recomputable by hand; GBX is converted as GBP/100.
- **Drift** is computed on read from the latest snapshot and the *active* strategy, so a
  saved target change shows up immediately.

- **Earnings check** (`earnings_check`, queued by `earnings_due` at report date +1, +2 and
  +4 days, or on demand). Locate: EDGAR 8-K item 2.02 or 6-K exhibit 99.1 near the report
  date; else one Claude web search restricted to the holding profile's domains, whose answer
  is used only if it was among the search results. The code fetches the document (size-capped),
  archives the exact bytes in Storage keyed by SHA-256, and converts it to text. One Claude
  call (Sonnet 5 by default, set in the Jobs config) gets only that document and returns
  figures as JSON with the number as written and a verbatim snippet; it has no tools. The
  validator then checks every figure in code (see `steward/earnings/validate.py`: snippet
  containment, value reading, scale, currency, fiscal-period alignment, consistency,
  recomputation of stated ratios, sanity bounds, restatements). Only `verified` facts reach
  verdicts; `unverified` ones wait for the owner's review; `rejected` ones are kept for the
  record. Every step logs tokens and cost on `job_step`.
- **Verdicts are arithmetic, in code.** A KPI is an absolute threshold, so pass / breach is
  computed from verified facts: margins and growth rates are recomputed from raw figures,
  converted to the KPI's unit (`%`, `x`, `EUR bn`, `USD per share`) and compared. No
  verified figure, a currency mismatch, or a missing prior period gives `unknown` with the
  reason. The spec's separate "verdict call" is deliberately not a model call: it would add
  a way to be wrong and no information, since the judgement needs no language.
- **Golden set.** `tests/golden/earnings`: 23 real releases (Apple, Microsoft, NVIDIA, SAP,
  Novo Nordisk) from EDGAR with 272 labelled figures. CI runs them with the labels as the
  extraction, which proves every correct real-world figure passes validation and every KPI
  gets a verdict. `--live` measures Claude's extraction accuracy (Phase 2 target 95 %).
  The labels were read by Claude and US figures cross-checked against XBRL; hand-check them
  and add your own holdings.
- **News pipeline** (`news_ingest`, every 2 h 06:00-22:00 Prague by default). Every enabled
  source of the news_sources config is read (so a source added there is read on the next
  run): RSS/Atom feeds, IR pages without a feed (new links since the last read; the first
  read only records a baseline), each holding's EDGAR filings (labelled by rule from the
  form and 8-K items, no model call), Finnhub company news, and optionally a Google News
  query per holding. Items get a canonical URL (the unique key: a URL is stored once, ever),
  the tier of their outlet (config, with every profile's IR domains as tier 1), and are
  dropped when stale, blocked, tier 3 (unless enabled) or matching no holding. Linking uses
  names, aliases, `$TICKER`/`(NASDAQ: TICKER)` forms, ISINs, and the profile's competitors,
  suppliers, customers and watch topics. Copies of one wire story (prefixes like "UPDATE 1-",
  outlet suffixes, "bln"/"billion" differences) join one `story_cluster`.
- **Classification** labels event type, affected holding, relevance, thesis link and rumour
  language, 50 items per Haiku call. Labels are checked in code: the affected holding must be
  one the item was linked to, a KPI link must name that thesis's KPI, and a rumour pattern
  match counts even if the model missed it. Severity comes only from the event_rules config.
- **Corroboration is code** (`steward/news/corroborate.py`): a tier-1 item confirms with no
  search; two independent tier-2 stories (different clusters and outlets) confirm; one
  tier-2 story, or rumour language, stays unconfirmed; tier 3 alone is sentiment. For
  unconfirmed stories at severity 2+, a Haiku web search (allowed domains only) looks for
  more sources; a URL it cites counts only if it was among the search results, has tier 1
  or 2 and is not an outlet the story already came from. Stories are re-checked for 48 h.
- **Alerts.** A confirmed, non-rumour story that a notification rule matches (first match
  wins) becomes one `alert` row per channel; mutes, the repeat throttle, the daily cap and
  quiet hours are applied and recorded. The worker's dispatcher sends due alerts every 15 s
  (Telegram Bot API, Resend), retrying failures. KPI breaches found by an earnings check
  alert the same way. Messages are composed in code, never by a model.
- **Digests** (`daily_digest`, `weekly_digest`). Assembled in code per holding: stories
  with their sources, verdicts with their facts. Stories at severity 2+ get a Sonnet summary
  (Batch API by default, falling back to a direct call after a timeout); every sentence must
  cite item or fact ids it was given, or it is removed. A period with nothing material still
  produces and sends a digest that says so.
- **Config is validated here.** `steward.config` holds the Pydantic model per config domain.
  `POST /config/{domain}/validate` exposes them, which also puts `StrategyConfig`,
  `ThesesConfig` and `ThesisConfig` into `openapi.json` for the frontend's generated types.
  Unknown keys are rejected so typos fail at save time.
- **Trading212 is read-only by construction.** The client only implements GET; an httpx
  request hook refuses any other method before it is sent; a test checks the client source
  for write-capable calls. Create the API key with read scopes only as well. Responses are
  validated (the API is beta) and money is parsed as `Decimal` straight from the JSON text.
  Rate limits are taken from the `x-ratelimit-*` response headers, not hard-coded.
- **Auth.** Tokens are verified against the project's JWKS (ES256/RS256/EdDSA). HS256 is only
  accepted when `SUPABASE_JWT_SECRET` is set (legacy projects). The token must also belong to
  `OWNER_USER_ID` and, unless `REQUIRE_MFA=false`, carry `aal2`. With no owner configured,
  every authenticated call is refused.
- **Secrets never reach logs.** Configured secret values, `Basic`/`Bearer` credentials and
  passwords inside URLs are redacted by a logging filter on every handler, including uvicorn's.
