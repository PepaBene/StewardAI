# StewardAI

A single-user, self-hosted portfolio copilot: it reads a Trading212 portfolio, stores the
owner's strategy and per-holding theses, and uses Claude to monitor, explain and research.
The full design is in
[AI_Portfolio_Copilot_Technical_Product_Description.md](AI_Portfolio_Copilot_Technical_Product_Description.md).

**Status: Phase 1 (Portfolio core)**: daily Trading212 sync with ČNB/ECB FX conversion, drift
against the strategy's targets, the Overview and Holdings screens, and a live sync indicator.

## Repository layout

```
apps/backend/          Python 3.12: FastAPI API + worker, one package (uv)
apps/web/              Next.js App Router frontend (TypeScript, Tailwind, shadcn/ui)
supabase/migrations/   the one database schema, plain SQL, applied with the Supabase CLI
supabase/config.toml   local Supabase stack config (auth: sign-up off, TOTP MFA on)
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
5. Register the owner, so RLS lets that user (and only that user) read app data:
   ```sql
   insert into private.app_owner (user_id) values ('<owner auth user id>');
   ```
6. Check pgmq: `select * from pgmq.list_queues();` runs with no error and lists the `jobs`
   queue (created by the Phase 1 migration).

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

See [apps/backend/README.md](apps/backend/README.md) and [apps/web/README.md](apps/web/README.md).

## Database access model

- RLS is enabled on every table. The frontend (`authenticated`) gets `SELECT` only, and only
  when `private.is_owner()` is true for the signed-in user. `anon` gets nothing.
- Frontend writes go through the API, which validates them with the Pydantic config schemas.
- The backend connects directly as `app_backend`: explicit per-table grants plus a permissive
  policy, no superuser, no `BYPASSRLS`, no access to anything it was not granted.
- `config_version` rows are immutable apart from `active`; one active version per domain.
- `job` and `job_step` are in the `supabase_realtime` publication, so the web app's sync
  indicator updates live. The migration adds them only where the publication exists.
