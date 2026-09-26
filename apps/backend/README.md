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
                     profiles, verification, news sources, event rules, notifications,
                     budget) + versioned store (history, diff, rollback, export/import)
    documents/       fetching source documents, HTML/PDF to text, snippet matching
    earnings/        earnings check: locate, extract (Claude), validate, verdicts, calendar,
                     XBRL reconciliation, owner review queue, thesis board, golden-set
                     evaluation (`python -m steward.earnings.golden`)
    news/            news pipeline: collectors, normalise, cluster, link, classify (Claude
                     Haiku), corroborate, escalate (Sonnet impact notes), digest; RNS, EQS
                     and EDGAR full-text collectors; classification eval (`python -m
                     steward.news.eval`)
    notify/          alert routing (rules, quiet hours, cap, throttle), Telegram and email,
                     the dispatcher
    fx/              ČNB and ECB reference-rate collectors, currency conversion
    jobs/            job runner (advisory lock, job/job_step log), pgmq queue, registry
    portfolio/       sync job, day snapshots, drift, rule breaches, transactions, read models
    reference/       identity (OpenFIGI, FIRDS/FITRS, GLEIF), corporate structure, prices
                     and risk metrics, exchange calendar, lifecycle events
    macro/           Eurostat, ECB and FRED series into macro_series, surprise events
    sources/         SEC EDGAR, Finnhub, OpenFIGI, ESMA, GLEIF, Wikidata, Stooq adapters
    trading212/      read-only Trading212 client (+ `python -m steward.trading212`)
    ai.py            Anthropic client, per-step token counts
    costs.py         cost from the Budget config's price table
    monitor.py       alerts for failed jobs, missed runs and news coverage gaps
    heartbeat.py     worker heartbeat row + external ping (`python -m steward.heartbeat_check`)
    signoff.py       Phase 1 live-data sign-off report (`python -m steward.signoff`)
    activity.py      per-holding coverage for the Activity page
    verify/          Phase 4: source trust, owner feedback, claim verifier (+ its eval,
                     `python -m steward.verify.eval`), replay preview, eval export from
                     feedback (`python -m steward.verify.export_evals`), CI evals gate
                     (`python -m steward.verify.evals_gate`)
    storage.py       evidence archive (Supabase Storage REST)
    db.py            asyncpg pool as app_backend
    logging.py       logging with secret scrubbing
    settings.py      environment settings
  api/               FastAPI app, routes, JWT verification, OpenAPI export
  worker/            scheduler + queue consumer entrypoint
tests/               pytest (DB tests need STEWARD_TEST_DATABASE_URL)
tests/golden/        golden set of real earnings releases with labelled figures (plus
                     `fb-*` cases added from feedback)
tests/verify/        20 claim-verifier cases, each with one injected unsupported claim
evals/baseline.json  live eval results per model-facing component (written by the gate)
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

## Windows

- Stop the worker with Ctrl+C (Windows has no SIGTERM in practice).
- `WinError 10013` when the API starts means Windows won't let it bind the port, usually
  because Hyper-V/WSL/Docker reserved a range that includes 8000
  (`netsh interface ipv4 show excludedportrange protocol=tcp` lists them) or antivirus
  blocked it. Use another port, e.g. `uv run uvicorn api.main:app --reload --port 8765`,
  and set `NEXT_PUBLIC_API_BASE_URL=http://localhost:8765` in `.env`.

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
| `GET /health`, `GET /health/ready` | Liveness (public); readiness: database reachable and worker heartbeat fresh (public) |
| `GET /portfolio/overview` | Totals, allocation, drift against the active strategy, freshness |
| `GET /portfolio/holdings` | Every open position of the latest snapshot, largest first |
| `GET /portfolio/transactions` | Orders, dividends and cash movements; filters `type`, `ticker`, `start`, `end`, with period totals |
| `GET /holdings/{ticker}` | Position, thesis, profile, KPI verdicts with history, every fact |
| `GET /holdings/{ticker}/identity` | Identifiers, listings, home market, legal entity, group structure, suggested relationships |
| `GET /holdings/{ticker}/prices` | Daily bars with benchmark, risk metrics, plausibility check against Trading212 |
| `POST /relationships/{id}/confirm`, `/reject` | Confirm a suggested relationship (writes a holding-profile version) or reject it for good |
| `GET /reference/gaps` | Instruments that could not be resolved, symbols without prices |
| `GET /calendar/exchanges`, `POST /calendar/exchanges/closures` | Public holidays and exchange closures; add closures to the Jobs config |
| `GET /theses/board` | Every holding's KPI statuses on one screen |
| `GET /facts/{id}/evidence` | Signed URL of the archived source, and the text around the snippet |
| `POST /facts/{id}/review` | Accept, correct (with value) or reject a held or quarantined fact, with a reason; re-queues verdicts |
| `GET /facts/review-queue` | Facts waiting for the owner, with snippet, source and the check they failed |
| `GET /news` | News items, newest first, with their story's severity and confidence; filters `ticker`, `severity`, `confidence`, `tier`, `story` (repeat a parameter for "any of") |
| `GET /news/event-types` | The event taxonomy |
| `GET /stories/disputed`, `POST /stories/{id}/resolve` | Disputed stories with each conflicting source; resolve one (unfreezes verdicts) |
| `GET /alerts` | Recent alert decisions: sent, held, suppressed (with why), failed; filter `kind` |
| `POST /alerts/test` | Route a test alert through the saved notification rules; the worker sends it |
| `GET /reports`, `GET /reports/{id}` | Digests, each sentence with its cited news items and facts |
| `GET /escalations`, `GET /escalations/{id}` | Event escalation reports (impact notes), with the run's status, cost and models |
| `GET /jobs` | The job log, newest first: filters `job_type`, `status`, `ticker`; cost and failed step per run |
| `GET /jobs/{id}` | One job with its steps, details, tokens and cost |
| `GET /activity/coverage` | Per open holding: last news run, earnings check and price bar covering it, and gaps |
| `POST /feedback`, `GET /feedback` | Wrong / irrelevant / useful on facts, verdicts, alerts, news items, stories, report sentences; `effects` says what it did |
| `GET /sources/trust` | Trust per source host that feedback has moved, and whether it is demoted a tier |
| `GET /eval-candidates` | Items marked wrong and added to an eval set, and whether they are exported |
| `POST /config/{event-rules,notifications}/preview` | Replay the last 7 days of stored stories and alerts under a draft; the changed outcomes |
| `POST /jobs/{portfolio-sync,earnings-check,thesis-verdicts,fact-reconcile,earnings-calendar,news-ingest,digest,macro-collect,reference-data,price-history,holiday-refresh}` | Queue a run now (202); the worker runs it |
| `GET`/`PUT /config/{strategy,theses,jobs,holding-profiles,verification,budget,news-sources,event-rules,notifications}` | Active config version / save a new one |
| `POST /config/{domain}/validate` | Validate a body without saving |
| `GET /config/{domain}/versions`, `/versions/{id}`, `/diff?from=&to=` | Version history and a field-level diff |
| `POST /config/{domain}/versions/{id}/rollback` | Save the chosen version's body as a new active version |
| `GET /config/export`, `POST /config/import` | Every active domain as one JSON file; import validates all domains first and saves nothing on any error |

All but the two health routes need the owner's MFA-verified Supabase JWT.

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
- **Reconciliation and review** (`fact_reconcile`, daily). A US press-release figure is
  compared with the later 10-Q/10-K XBRL value for the same concept and period (Q4 derived
  from the annual figure); a mismatch beyond tolerance quarantines the fact and re-queues
  its verdicts. A new value for a stored period is kept beside the old one, never
  overwriting it. Held and quarantined facts wait in the review queue; the owner's accept,
  correction or rejection always wins and is logged with the reason.
- **Costs.** Every AI step stores its raw token counts (input, output, cache read and write,
  batch flag) and web searches, plus the Budget config's prices at the time and the
  resulting `cost_usd`, so every cost can be recomputed.
- **Reference data** (`reference_data`, weekly; `price_history`, weekdays;
  `holiday_refresh`, January and July). OpenFIGI maps each ISIN to a share-class FIGI
  and listings; ESMA FIRDS gives the home market and termination dates, FITRS liquidity; GLEIF gives the
  issuer LEI, status, successor and parents; Wikidata adds industry, products and people,
  and relationships it alone knows are stored as *suggested*. A lapsed LEI, a successor LEI
  or a FIRDS termination raises one lifecycle event. Stooq bars feed returns, volatility,
  drawdown, beta and correlation, computed in code. Frankfurter backfills FX history and is
  the ECB fallback. Nager.Date holidays plus per-exchange closures in the Jobs config make
  the calendar that silences expected gaps. Anything unresolved is listed, never skipped.
- **Regulatory feeds.** RNS (via Investegate) and EQS pages are read for UK and German
  holdings, chosen by the FIRDS home market; the announcement type maps to the event
  taxonomy in the Event rules config, and results announcements start an earnings check.
  Both are disabled by default: check each site's terms before enabling. EDGAR full-text
  search runs saved searches for each holding's names and watch topics; a 10-K or 20-F that
  names a holding as customer or supplier becomes a suggested relationship linked to it.
- **Macro** (`macro_collect`, daily). Eurostat (HICP, unemployment, GDP, industrial
  production, confidence for CZ, DE, EA), ECB Data Portal and FRED series (FRED needs
  `FRED_API_KEY`) go into `macro_series`. A surprise beyond the Event rules band is
  evaluated only when a value is first seen, and cites its release date and source URL.
  Central-bank press feeds are news sources flagged `macro`. Digest macro numbers come
  only from `macro_series`.
- **Escalation** (`event_escalation`). A confirmed story at the configured severity, or a
  close-to-close price move beyond the Event rules threshold, queues one Sonnet impact
  note (at most `max_steps` calls and searches) that must cite the story's sources and
  name the thesis KPI or exit condition it touches, or say none applies; the draft is
  checked in code before it is stored.
- **Time checks and lifecycle.** Items outside the freshness window are dropped; a story
  whose event date is long before its publish date is marked recycled; a new story that
  matches a stored one is linked as a duplicate and never alerts again. A disputed story
  freezes verdict changes for its holding until the owner resolves it.
- **Breaches** (sync step `breaches`). Drift out of band, hard limits and strategy rules are
  evaluated after each sync; a breach alerts once at its `on_breach` severity, is listed
  in the next digest, and clears when back in band.
- **Monitoring** (`monitor`, every 15 min). A failed watched job, a scheduled run that did
  not start (market holidays excepted), or a holding with no news coverage for the window
  raises one `system` alert each (dedupe keys). The worker's heartbeat row and optional
  `HEARTBEAT_PING_URL` cover the case where the worker itself is down.
- **Verification (Phase 4).** The Verification config holds every defence threshold: sanity
  bands and tolerances, the corroboration rules per tier (tier 1 confirms alone, how many
  independent tier-2 outlets confirm), source trust steps, and the claim verifier.
  *Trust* is per outlet host, 1 until judged: the owner's *wrong* lowers it, *useful*
  raises it; below `demote_below` the host counts one tier lower in corroboration.
  *Feedback* on a fact marked wrong quarantines it and re-queues its holding's verdicts;
  on news, stories and alerts it moves the outlets' trust; verdicts and report sentences
  are recorded for the error report. A wrong fact or item can be added to an eval set with
  its correction; `export_evals` writes it into `tests/golden/earnings/fb-*` or the
  labelled news set after checking it the way CI will.
- **Claim verifier.** Every model-written sentence of a digest or impact note is checked
  before the report is stored: first in code (each number must appear in a cited snippet,
  at any scale: "$94.0 billion" matches "94,036" millions), then by a Haiku call with only
  the sentences and their snippets. Unsupported sentences are removed (`strip`) or kept and
  marked (`mark`); sentences the model could not check, or whose only source is a
  web-search result with no stored text, are marked `unchecked`, never passed as supported.
  Alerts are composed in code from the source's own headline and have nothing to check.
  `python -m steward.verify.eval` scores the 20 injected-error cases: the code check alone
  catches the 12 numeric ones; `--live` measures the model (target: all 20, no false alarm).
- **Replay preview.** A draft Event rules or Notifications config is run twice against the
  last 7 days of stored stories and non-news alerts (active vs draft, in time order, each
  run counting its own daily cap and throttle); the response lists the events whose outcome
  changes. Only stored results are read.
- **Reference trust tiers.** GLEIF (tier 1) confirms structure; Wikidata (tier 3) only
  suggests. A Wikidata parent that GLEIF contradicts is `disputed`, with GLEIF's version in
  `conflict`, until the owner decides.
- **Evals gate** (`evals` CI job). Extraction, classification and the claim verifier each
  have a fingerprint (prompt code, schema, default model, eval set). Unchanged since
  `evals/baseline.json`: no API call. Changed: with the `ANTHROPIC_API_KEY` repository
  secret the live eval runs and fails below its target (98 %, 85 %, 100 %) or one point
  below the recorded score; without the secret the change fails as unmeasured, until a
  live run is recorded with `--write-baseline`. Before a baseline exists, a run without the
  secret only warns. To make it block merges, add the secret and mark the job required.
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
