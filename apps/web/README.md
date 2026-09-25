# StewardAI web

The StewardAI frontend: Next.js 16 (App Router, TypeScript, Turbopack), Tailwind CSS v4,
shadcn/ui (Radix), Tremor Raw (copy-paste, from Phase 1) and Recharts.

- **Data**: reads Postgres through `supabase-js` under row-level security (from Phase 1).
- **Actions**: calls the Python API (FastAPI, `apps/backend`) with a typed client generated
  from the backend's `openapi.json`. Each call carries the user's Supabase JWT as
  `Authorization: Bearer`.

## Setup

```bash
cp .env.example .env         # in the repository root, once; then fill in the values
cd apps/web
npm ci
npm run dev                  # http://localhost:3000
```

Node 22+ and npm 10+.

### Environment variables

The web app reads the **repository-root `.env`** (shared with the backend) through
`loadEnvConfig` in `next.config.ts`. `/.env.example` lists every variable and where to get
it. Variables already in the process environment (CI, Vercel) win, and an
`apps/web/.env.local` still works for web-only overrides.

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | yes | Supabase project URL (local CLI stack: `http://127.0.0.1:54321`). |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | yes (or the next one) | Anon key. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | alternative | The newer publishable key (`sb_publishable_...`). Used when the anon key is unset. |
| `NEXT_PUBLIC_API_BASE_URL` | no | Python API base URL. Default: `http://localhost:8000`. |
| `NEXT_PUBLIC_ALLOWED_EMAIL` | no | Extra single-user guard: any other signed-in email is signed out at once. |

`NEXT_PUBLIC_*` values are inlined **at build time**. Rebuild (or restart `npm run dev`)
after changing them. The app builds without them. At runtime, with Supabase unset, every
route redirects to `/login`, which says which variables are missing.

The API must allow this app's origin through CORS. The backend's `CORS_ORIGINS` defaults
to `http://localhost:3000`.

## Pages

| Route | What it shows |
| --- | --- |
| `/` | Overview: value, allocation, drift, freshness |
| `/holdings` | Every open position; each row opens its holding page. A tab switches to the transactions view. |
| `/transactions` | Orders, dividends and cash movements, filterable by type, ticker and period (filters live in the URL), with **Load more** paging that never repeats a row. The dividend total per currency and the totals per type, subtype and currency are computed by the API over every matching row, not the loaded page. Part of the Holdings section. |
| `/holdings/[ticker]` | Position, thesis, KPI verdicts (open a row for the rationale, the facts behind it, their snippets and evidence links, and the KPI's history), every extracted figure with its confidence badge, and Accept / Reject for figures the validator held. Runs an earnings check or re-runs verdicts. |
| `/evidence/[factId]` | One fact: the quoted snippet, the archived text around it, the original URL and a signed link to the archived copy |
| `/news` | News feed grouped by story, filterable by holding, severity, confidence and source tier (filters live in the URL); each story shows its copies, why it is (un)confirmed and its corroborating links |
| `/reports`, `/reports/[id]` | Daily and weekly digests; each sentence carries numbered citations linking to its news items (with a source-card sidebar showing tier) or to `/evidence/[factId]` |
| `/config/news-sources`, `/config/event-rules`, `/config/notifications` | Source registry and outlet tiers; severity per event type and overrides; notification rules, quiet hours, cap and a **Send test alert** panel |
| `/config` | Every config domain with its editor and history, and **Export** (every domain's active version as one JSON file) / **Import** (validated domain by domain; any error rejects the whole file, and every error is listed by domain and field) |
| `/config/strategy` | Base currency, FX source, weight basis, goals, horizon, notes, categories and a ticker × dimension classification table, cash / position / category targets with bands and hard limits, and rules (position weight, category weight, cash weight, position count). Weights are typed as percentages and stored as exact fractions. Position + cash targets, and each dimension's category targets, above 100 % are flagged on the fields and block Save. Drift on the Overview uses the saved version at once. |
| `/config/jobs` | Every schedule the backend's `JobsConfig` has (enabled, 5-field cron with a plain-English reading, time zone) and the models and caps of the earnings check, earnings calendar, news pipeline and digests. The worker picks up a saved schedule within 5 minutes. |
| `/config/history`, `/config/history/[domain]` | Version history for every config domain (strategy, theses, jobs, holding-profiles, verification, news-sources, event-rules, notifications, budget): diff any two versions (default: the selected one against its parent), read a version's JSON, and roll back after a confirm step (a new active version with the old body; a body the current schema rejects is refused with its errors). Every editor links here from its header. |
| `/config/theses`, `/config/holding-profiles` | Editors for those config domains. Form state converts to the generated `ThesesConfig` / `HoldingProfilesConfig` types; the backend's Pydantic model validates, and its 422 errors are shown next to each field. Saving theses queues a verdict run, so a changed threshold shows up at once. |

The **command palette** (Cmd/Ctrl+K, or Search in the header) reaches every section, config
editor, config history, the transactions view and every holding, and runs checks (sync, news check, digests, verdicts, an earnings check per
holding) from the keyboard.

Confidence badges (`components/evidence/confidence-badge.tsx`) use the four labels of the
spec, Verified / Corroborated / Unconfirmed / Disputed, in the muted grey / blue / amber / red
palette. Pass and breach use gain and loss; unknown is dashed and neutral.

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Dev server (Turbopack). |
| `npm run build` / `npm start` | Production build / serve. |
| `npm run lint` | ESLint (flat config, `eslint-config-next`), then `lint:colors`. |
| `npm run lint:colors` | Fails on any colour literal or Tailwind palette class in `src/` outside `src/app/tokens.css`. |
| `npm run typecheck` | `next typegen` (route types for `PageProps`/`LayoutProps`), then `tsc --noEmit`. |
| `npm test` | Vitest. Covers the auth/MFA decision logic, the typed API client, the colour rule, the pure form <-> body conversions of the config editors (the strategy and jobs forms round-trip a fully populated body without loss), cron readings, history and import helpers, and the transaction filters. |
| `npm run gen:api` | Regenerates `src/lib/api/schema.d.ts` from `../backend/openapi.json`. |

## Auth and MFA enforcement

There is exactly one user, the owner. There is no sign-up UI anywhere in the app.

1. **`/login`**: email and password only (`signInWithPassword`). That is the first factor
   (`aal1`).
2. **MFA is always required.** After the password step:
   - no verified TOTP factor → **`/mfa/enroll`**: `mfa.enroll({ factorType: 'totp' })`
     shows a QR code and the secret, then `mfa.challenge` + `mfa.verify` checks a 6-digit
     code. Abandoned unverified factors are removed first, so setup can be restarted.
   - a verified factor, but the session is still `aal1` → **`/mfa/verify`**: challenge and
     verify.
3. **Server-side enforcement on every request.** `src/proxy.ts` is the Next 16 proxy
   (formerly `middleware.ts`). It runs on every route except Next internals and static
   files. It refreshes the Supabase session cookies (`@supabase/ssr`) and reads:
   - the user from `auth.getUser()`, which validates the token with Supabase Auth;
   - `currentLevel` from `auth.mfa.getAuthenticatorAssuranceLevel()`;
   - `nextLevel` from the user's verified factors.

   It then applies `decideAccess()` from `src/lib/auth/access.ts`, a pure function with
   unit tests in `access.test.ts`:

   | Session | `/login` | `/mfa/enroll` | `/mfa/verify` | any other route |
   | --- | --- | --- | --- | --- |
   | none | allow | → `/login` | → `/login` | → `/login` |
   | aal1, no factor | → enroll | allow | → enroll | → `/mfa/enroll` |
   | aal1, has factor | → verify | → verify | allow | → `/mfa/verify` |
   | aal2 | → `/` | → `/` | → `/` | allow |
   | email ≠ `NEXT_PUBLIC_ALLOWED_EMAIL` | sign out, → `/login?error=not_allowed` | | | |

   The authenticated layout, `src/app/(app)/layout.tsx`, runs the same check again on the
   server, so a mistake in the proxy matcher cannot expose an app page.
4. **The API checks the same identity.** The backend verifies the JWT against Supabase's
   JWKS and requires the owner's user id and `aal2`.

### Supabase settings (dashboard or `supabase/config.toml`)

- **Disable sign-ups.** Dashboard: Authentication → Sign In / Providers → turn off *Allow
  new users to sign up*. The repo's `supabase/config.toml` sets `enable_signup = false` for
  the local stack. With sign-ups off, a second email cannot create an account, whatever the
  client does.
- **Enable TOTP MFA.** Authentication → Multi-Factor → TOTP: enroll and verify enabled
  (`[auth.mfa.totp] enroll_enabled = true, verify_enabled = true` locally).
- **Create the owner by hand**: Authentication → Users → *Add user* (or *Invite*). The
  owner enrols TOTP at the first sign-in.
- Put the owner's user id in the backend's `OWNER_USER_ID`. Optionally put their email in
  `NEXT_PUBLIC_ALLOWED_EMAIL`.

## Theme and colour tokens

- `src/app/tokens.css` is the **only** file that contains colour values. It defines the
  same variables for light (`:root`, the default) and dark (`.dark`):
  - `surface`, `surface-muted`, `surface-raised`, `border`, `text`, `text-muted`;
  - one `accent` (with `accent-foreground`) and `focus-ring`;
  - `gain` and `loss` for financial state only;
  - `neutral-signal`, `warning-signal`, `info-signal` and `disputed` for severity and
    confidence. This muted palette never overlaps gain or loss.
  - Each state colour has a `-subtle` background variant.
- `@theme inline` exposes the tokens as Tailwind utilities: `bg-surface`, `text-text-muted`,
  `border-border`, `text-gain`, `bg-warning-signal-subtle`, `outline-focus`, and so on.
  Tailwind's default palette is switched off (`--color-*: initial`), so a class like
  `bg-slate-100` generates no CSS. `npm run lint:colors` also rejects it.
- `next-themes` uses `attribute="class"`, `defaultTheme="light"`, `enableSystem` and
  `disableTransitionOnChange`. Its blocking script applies the saved or system theme
  before first paint, so there is no flash. The toggle in the top bar offers Light, Dark and
  System.
- **Numbers**: every price, percentage and currency figure uses tabular numerals. Add the
  `num` utility (defined in `globals.css`) or `tabular-nums` to the element. In tables,
  right-align numeric columns.

## Typed API client

- `npm run gen:api` runs `openapi-typescript ../backend/openapi.json -o src/lib/api/schema.d.ts`.
  Run it after the backend changes its API. The backend regenerates its spec with
  `uv run python -m api.export_openapi`. Commit both files.
- `src/lib/api/client.ts` exports:
  - `createApiClient()`: an `openapi-fetch` client with base URL
    `NEXT_PUBLIC_API_BASE_URL` and a middleware that adds the Supabase access token as a
    Bearer header;
  - `getBrowserApiClient()`: the same client, reading the token from the browser session.

  Paths, parameters and response bodies are type-checked, for example
  `api.GET("/health")` returns `data: { status: "ok"; version: string }`.
- `/status` (the *API status* link in the top bar) calls `GET /health` through this client.
  It shows the status, HTTP code and version, or a clear "Backend unreachable" /
  HTTP-error message.

## Components

- `src/components/ui/`: shadcn/ui components (button, input, label, card, dropdown-menu),
  written by hand from shadcn's source and restyled with tokens. `components.json` is
  valid, so `npx shadcn add <component>` works where the shadcn registry is reachable.
  Swap any palette classes in added components for tokens.
- `src/components/tremor/`: Tremor Raw components, from Phase 1. See the README there.
