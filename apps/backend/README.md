# StewardAI backend

One Python package, two processes:

| Process | Command | Role |
| --- | --- | --- |
| API | `uv run uvicorn api.main:app` | FastAPI: the frontend's action calls. Verifies the Supabase JWT on every non-public route. |
| Worker | `uv run python -m worker` | APScheduler for timed jobs (and, from Phase 1, the pgmq consumer). |

Both share `steward` (settings, logging, DB access, config schemas, source adapters).

```
src/
  steward/           shared library
    config/          Pydantic schemas per config domain (strategy, theses, ...)
    trading212/      read-only Trading212 client (+ `python -m steward.trading212`)
    db.py            asyncpg pool as app_backend
    logging.py       logging with secret scrubbing
    settings.py      environment settings
  api/               FastAPI app, JWT verification, OpenAPI export
  worker/            scheduler entrypoint
tests/               pytest (DB tests need STEWARD_TEST_DATABASE_URL)
openapi.json         committed API contract; the frontend generates its types from it
```

## Setup

```bash
uv sync                    # Python 3.12 + dependencies from uv.lock
cp .env.example .env       # then fill in values
```

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

## Design notes

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
