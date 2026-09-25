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
    config/          Pydantic schemas per config domain (strategy, theses, jobs) + versioned store
    fx/              ČNB and ECB reference-rate collectors, currency conversion
    jobs/            job runner (advisory lock, job/job_step log), pgmq queue, registry
    portfolio/       sync job, day snapshots, drift, API read models
    trading212/      read-only Trading212 client (+ `python -m steward.trading212`)
    db.py            asyncpg pool as app_backend
    logging.py       logging with secret scrubbing
    settings.py      environment settings
  api/               FastAPI app, routes, JWT verification, OpenAPI export
  worker/            scheduler + queue consumer entrypoint
tests/               pytest (DB tests need STEWARD_TEST_DATABASE_URL)
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

uv run pytest                                # unit tests; DB tests skip without a database
STEWARD_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The DB tests create a throwaway database, apply a small Supabase stand-in
(`tests/sql/supabase_shim.sql`: roles, `auth.uid()`, default grants) and every migration
in `supabase/migrations`, then check the access model as `app_backend`, `authenticated`
(owner and non-owner) and `anon`. The pgmq migration is skipped on servers without the
extension; CI runs against a Postgres image that has it.

## API (Phase 1)

| Route | What it does |
| --- | --- |
| `GET /portfolio/overview` | Totals, allocation, drift against the active strategy, freshness |
| `GET /portfolio/holdings` | Every open position of the latest snapshot, largest first |
| `POST /jobs/portfolio-sync` | Queue a sync now (202); the worker runs it |
| `GET`/`PUT /config/{strategy,theses,jobs}` | Active config version / save a new one |

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
