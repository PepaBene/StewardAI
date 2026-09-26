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
| `/holdings` | Every open position; each row opens its holding page. Tabs switch to the thesis board, the review queue and the transactions view. |
| `/theses` | Thesis board: every holding with a thesis once, breaches first, with its overall status, pass / breach / unknown counts and one cell per KPI. Clicking a KPI's status opens its verdict below the row (rationale, observed value against the threshold it was judged under, period, confidence badge, a stale marker, and a link to each fact's evidence page). A KPI never judged is shown as unknown ("Never judged — no verified figure yet"), dashed, never left out. Shows overdue thesis reviews, the number of held figures per holding (linking to the review queue for it) and "Verdicts as of" the newest verdict. Part of the Holdings section. |
| `/review` | Review queue: every held (validator) or quarantined (reconciliation) figure, grouped by holding, with its value as printed (scale, currency), confidence badge, the checks and bounds it broke, the quoted snippet, source and evidence links, the stored figure a restatement differs from (side by side) and the XBRL reconciliation rows (filing, concept, XBRL value and difference in the figure's printed scale). **Accept**, **Correct** (a value in the printed scale) or **Reject**, each with a required reason (1–500 characters). A resolved figure leaves the list at once; the page then polls the holding every 2 s (up to 30 s) until the verdict run the review queued has finished, and shows the affected KPIs' new status (and what it was). `?ticker=` filters by holding. Part of the Holdings section. |
| `/transactions` | Orders, dividends and cash movements, filterable by type, ticker and period (filters live in the URL), with **Load more** paging that never repeats a row. The dividend total per currency and the totals per type, subtype and currency are computed by the API over every matching row, not the loaded page. Part of the Holdings section. |
| `/holdings/[ticker]` | Position, thesis, KPI verdicts (open a row for the rationale, the facts behind it, their snippets and evidence links, and the KPI's history), every extracted figure with its confidence badge, and a **Review** control on held or quarantined figures (Accept / Correct / Reject with a reason; verified figures offer Correct / Reject), with a link to the review queue for the holding. Runs an earnings check or re-runs verdicts; the last earnings check and verdict run link to their job runs. The news section shows when the news was last checked (stale styling as on `/news`) and links to the holding's impact notes. **Price and risk**: a Stooq price chart with the configured benchmark, both rebased to 100 at their first common date, a 3M / 1Y / 2Y / 5Y / Max range (`?days=`), the source and "as of" date on the chart; a risk card (returns 1M–2Y, annualised volatility, maximum drawdown, beta against the named benchmark, observations) showing the backend's stored figures, formatted only (each figure's title holds the stored value); correlations with the other holdings; the plausibility check against Trading212 (flagged or within tolerance); and, when there is no history, the reason with links to set the Stooq symbol and to collect prices. **Identity and structure**: share-class FIGI, ISIN, security type, home market, Stooq symbol; OpenFIGI composite listings; ESMA FIRDS venues (home market marked, terminated venues labelled); FITRS liquidity; the GLEIF legal entity (LEI linking to GLEIF); group structure (consolidating parents, or GLEIF's stated reason there is none; owners; subsidiaries, collapsible; confirmed competitors, suppliers and customers); suggested relationships with source, tier and source link, and **Confirm** / **Reject** (a confirmed competitor, supplier or customer is saved as a new holding-profiles version, whose number is shown with links to the profile and its history; a rejected one is never suggested again); news aliases; the Wikidata summary (tier 3). **Disputed relationships**: a tier-3 claim (Wikidata) that a tier-1 source (GLEIF) contradicts, side by side with the conflicting claim (name, relationship, source, tier and source link for both), with **Confirm the claim** / **Reject the claim** (after a confirm step). Shows the identity error prominently when resolution is incomplete, and **Refresh reference data** re-resolves the holding and updates the section when the job finishes. Every figure and verdict (the KPI table, its history and the figures behind it) and each news story takes wrong / irrelevant / useful; a figure marked wrong is quarantined and the page follows the verdict run it queued. |
| `/config/calendar` | Exchange calendar: the holding exchanges and their countries, public holidays suggested as closures (tick and **Confirm selected**, saved as a new Jobs version), the confirmed closures, and **Refresh holidays** (Nager.Date). Explains that public holidays are not exchange calendars, so each day is confirmed by the owner. |
| `/activity` | **Activity** (nav "Activity"). **Coverage per holding** (`GET /activity/coverage`): when a successful news run, the latest price bar and the last earnings check covered each holding; a holding no news run has covered within the monitoring window (e.g. 48 h), or whose latest price bar is more than 4 days old, shows a **Gap** tag (icon, word and the reason in words), listed first, with no manual query. **Alerts** (`GET /alerts?kind=system`: failed jobs, missed runs, coverage gaps; **All alerts** (`?alerts=all`) adds news, thesis-breach, portfolio and test alerts), each with its severity, event, holding, channel, outcome (sent, held until, suppressed with why, failed) and feedback controls. **Job log** (`GET /jobs`): filter by job type, status and holding (in the URL), **Load older runs** paging (`before_id`), each run's trigger, parameters, status, age, duration, cost (sum of its steps), failed step and error, linking to `/jobs/[id]`. **Source trust** (`GET /sources/trust`): host, score, wrong / useful counts and whether it is demoted one tier. **Evaluation candidates** (`GET /eval-candidates`): kind (golden earnings figure or labelled news item), the item, the corrected value or label, and whether `python -m steward.verify.export_evals` has written it into the repository yet. |
| `/status` | API health (`GET /health`), **Job runs** (open a run by its number) and **Reference data gaps**: holdings whose identity is not resolved and symbols with no price history (holding or benchmark), with the reason. Links to Activity (highlighted as part of it). |
| `/jobs/[id]` | One job run (`GET /jobs/{job_id}`): type, trigger, status, queued / started / finished times and duration, the total model cost (summed from the steps) and the models called, the run's error (or the first failed step's), its parameters, and every step with its status, duration, model, cost, error and detail JSON. Follows a run that has not finished (re-reads every 3 s). Linked from every "queued as job #…" message, the job log, impact notes, the last news check and a holding's last earnings check and verdict run; the palette opens a job by typing its number. |
| `/evidence/[factId]` | One fact: the quoted snippet, the archived text around it, the original URL and a signed link to the archived copy |
| `/news` | News feed grouped by story, filterable by holding, severity, confidence and source tier (filters live in the URL); each story shows its copies, why it is (un)confirmed and its corroborating links, and a disputed story links to its side-by-side view. A freshness label ("News as of 14:05 · 2 h ago · Next due 16:00") dates the feed by the last news check, links to that job, and turns **Stale** (icon, word and reason) once the next scheduled check (`next_ingest_due_at`) is more than 30 min overdue ("the next news check was due at 16:00 and no newer check has finished"), after 10 h when no schedule is known, or when the last check failed. Wrong / irrelevant / useful on each story, its lead item and each copy (a wrong item can be added to the labelled news set with the right event type, holding, relevance and rumour flag). |
| `/news/disputed` | Disputed stories (`GET /stories/disputed`): the claim and its sources beside the verified sources that contradict it (and any that support it), each with tier, outlet, headline and time; the corroboration note; whether the holding's verdicts are frozen. **Confirm: it happened** or **Mark false**, with an optional note (up to 500 characters), calls `POST /stories/{id}/resolve`; the story then shows its resolution, note and time. **Show resolved** (`?resolved=1`) lists resolved stories too. |
| `/reports`, `/reports/[id]` | Daily and weekly digests (tabs with the impact notes); each sentence carries numbered citations linking to its news items or to `/evidence/[factId]`. **Hovering or keyboard-focusing any citation** (sentences, verdicts, thesis changes) shows a preview with the exact stored text it links to (a news item's headline and summary, a figure's snippet) and the source's tier (a figure shows its status instead: it has no tier). The claim verifier's result is shown per sentence: supported (a quiet shield), **Unsupported** (mark mode: wavy underline, word, icon and the verifier's reason) or **Unchecked** (dotted underline and word), with the report's claim-check summary (mode; supported / unsupported / unchecked / removed; note). The **source sidebar** lists every source (number, outlet, tier, headline and link, publish time, its trust score after feedback and whether it is demoted, and its stored text) and every **cited figure** (holding, metric, period, value as printed, status, snippet, evidence and source links). Every sentence, story and verdict takes wrong / irrelevant / useful. A digest also lists its portfolio flags (out of band, hard limit, rule; open or cleared), thesis status changes (from → to, with fact links), theses due for review, the latest macro figures (value in its unit, period, release date, source link, "new" when released in the window) and market holidays. Each shows "News as of" its window end and is current until the next digest of its kind is due (`next_due_at`) plus 2 h, so a Friday daily digest is not stale over the weekend when digests run on weekdays; without a schedule, stale after 26 h (daily) or 8 days (weekly). The list shows when the latest digest was written, with the same rule. **Collect macro data now** (`POST /jobs/macro-collect`) sits next to **Write digest now**. |
| `/escalations`, `/escalations/[id]` | Impact notes (`GET /escalations`, `GET /escalations/{id}`) on confirmed severity-2+ stories and large price moves, filterable by holding (`?ticker=`): headline, holding, trigger (story with its event type, or the price move), severity, whether a model assessed it (or why not), the cited sentences with numbered source links, the thesis KPIs and exit conditions touched or the statement that none applies, the rationale, and the sources with kind (story, search, price), tier and stored snippet. Each citation shows, on hover or keyboard focus, the snippet the sentence was checked against and the tier; each sentence shows the claim verifier's result (supported / unsupported / unchecked with the reason) and takes wrong / irrelevant / useful; the claim-check summary sits above the sentences. **The run that wrote it**: the `event_escalation` job (linked to `/jobs/[id]`) with its status badge, cost and the models it called; the list shows the run's status and cost compactly. Each is labelled "Written … · age", stale after 72 h. |
| `/config/verification` | The Verification config (`GET`/`PUT /config/verification`, **Validate** = `POST /config/verification/validate`): the default quarter-on-quarter sanity band (typed as ± %, or off = null), per-metric bands (metric → ± % or "No bound" = null), the stated-ratio tolerance (pp), period-end tolerance and maximum report lag (days), corroboration (tier 1 confirms alone; independent tier-2 outlets needed, 2–5), source trust (wrong penalty, useful reward, demote-below threshold, 0–1) and the claim verifier (on/off, model, strip or mark, numeric check first, which reports it checks). Every field is checked against the backend's bounds as you type and blocks Save until fixed; the API's 422 errors are shown on the same fields. Links to its version history. |
| `/config/news-sources`, `/config/event-rules`, `/config/notifications` | Source registry (RSS, page, EDGAR, EDGAR full-text search, Finnhub, Google News, RNS, EQS; the per-holding types need no URL; a **macro** flag for central-bank and macro sources), outlet tiers, blocked outlets and the aggregator allow list; severity per event type, overrides, the price-move threshold, macro surprise bands per series ("hicp 0.3" per line) and the ordered RNS / EQS announcement-type rules (first match wins; reset to the defaults); notification rules, quiet hours, days quiet all day (weekends, public holidays of a country), the monitoring alert severity, cap and a **Send test alert** panel. On the event-rules and notification editors **Preview and save** first posts the draft to `POST /config/event-rules/preview` or `/config/notifications/preview` and shows the replay of the last 7 days in a dialog: events replayed, alerts under the saved rules → under the draft, and each event whose outcome changes (headline, holding, event, "would now alert" / "would no longer alert" / "alert changes", and before vs after: severity, decision, channels, held until, suppressed reason, changed fields marked in words). Only **Confirm and save** stores the version (`PUT`); **Cancel** keeps editing. An invalid draft shows its field errors instead. |
| `/config` | Every config domain with its editor and history, and **Export** (every domain's active version as one JSON file) / **Import** (validated domain by domain; any error rejects the whole file, and every error is listed by domain and field) |
| `/config/strategy` | Base currency, FX source, weight basis, goals, horizon, notes, categories and a ticker × dimension classification table, cash / position / category targets with bands and hard limits, and rules (position weight, category weight, cash weight, position count). Weights are typed as percentages and stored as exact fractions. Position + cash targets, and each dimension's category targets, above 100 % are flagged on the fields and block Save. Drift on the Overview uses the saved version at once. |
| `/config/jobs` | Every schedule the backend's `JobsConfig` has (enabled, 5-field cron with a plain-English reading, time zone; including macro data and monitoring) and the models and caps of the earnings check, earnings calendar, news pipeline, digests and event escalation; macro (Eurostat areas, and the Eurostat, FRED and ECB series as rows); monitoring (job types whose failures alert, missed-run grace, news coverage window); reference data (OpenFIGI and Wikidata refresh, Wikidata on/off, subsidiary aliases), prices (benchmark symbols one per line, years of history, plausibility tolerance) and the holiday calendar (public-holiday countries, exchange closures as rows). The worker picks up a saved schedule within 5 minutes. |
| `/config/history`, `/config/history/[domain]` | Version history for every config domain (strategy, theses, jobs, holding-profiles, verification, news-sources, event-rules, notifications, budget): diff any two versions (default: the selected one against its parent), read a version's JSON, and roll back after a confirm step (a new active version with the old body; a body the current schema rejects is refused with its errors). Every editor links here from its header. |
| `/config/theses`, `/config/holding-profiles` | Editors for those config domains (a holding profile includes the Stooq price symbol override). Form state converts to the generated `ThesesConfig` / `HoldingProfilesConfig` types; the backend's Pydantic model validates, and its 422 errors are shown next to each field. Saving theses queues a verdict run, so a changed threshold shows up at once. |

The **command palette** (Cmd/Ctrl+K, or Search in the header) reaches every section, config
editor (including Verification), config history, the thesis board, the review queue (also per holding), the transactions view, the exchange calendar, the reference data gaps, the disputed stories, the impact notes, the Activity page's job log, coverage gaps, monitoring alerts, source trust and eval candidates, the API status page and every holding, opens a job run by its number, and runs checks (sync, news check, digests, verdicts, reference data, price history, holidays, macro data, and an earnings check or a reference-data refresh per
holding) from the keyboard. A queued check links to its job run.

Confidence badges (`components/evidence/confidence-badge.tsx`) use the four labels of the
spec, Verified / Corroborated / Unconfirmed / Disputed, in the muted grey / blue / amber / red
palette. Pass and breach use gain and loss; unknown is dashed and neutral.

## Feedback on every item (Phase 4)

`components/feedback/feedback-control.tsx` is one reusable control: **Wrong**, **Irrelevant**
and **Useful** icon buttons (each with an accessible name, `aria-pressed` for the current mark,
and the mark in words next to them, e.g. "Marked wrong"). Choosing one opens a small dialog
(keyboard: Tab cycles inside it, Escape closes it and returns focus) with an optional note (up
to 1000 characters); for **wrong** on a figure or news item, **Add to the eval set** asks for the
right value (in the printed scale, optionally as printed and the passage stating it) or the
right label (event type, holding, relevant, rumour). It posts `POST /feedback` and shows the
`effects` returned: "Quarantined; verdicts re-judged (job #n); ir.test trust 0.8; added to the
eval set (candidate #n)". Report sentences are addressed by `sentence_path`: `overview/<i>` and
`sections/<s>/summary/<i>` in a digest, `sentences/<i>` in an impact note (indices into the stored
body arrays). Earlier marks come from `GET /feedback`, batched per kind for every control on
screen (`lib/feedback/marks-store.ts`). The request builder and validation, sentence paths, the
current-mark rule and the effects summary are pure (`lib/feedback/feedback.ts`, tested).

Checklist of the surfaces with the control (`lib/feedback/surfaces.ts`; `surfaces.test.ts` fails
if a listed file stops rendering a control for each kind):

| Surface | Route | Kinds |
| --- | --- | --- |
| Holding page: extracted figures and the figures behind each verdict | `/holdings/[ticker]` | fact, verdict |
| Holding page: news section (one row per story) | `/holdings/[ticker]` | story |
| Evidence page | `/evidence/[factId]` | fact |
| Thesis board: verdict panel | `/theses` | verdict |
| Review queue: held and quarantined figures | `/review` | fact |
| News feed: stories, lead items and copies | `/news` | story, news item |
| Disputed stories: the story and each source item | `/news/disputed` | story, news item |
| Digest: overview and section-summary sentences, stories and verdicts | `/reports/[id]` | report sentence, story, verdict |
| Impact note: each sentence | `/escalations/[id]` | report sentence |
| Activity: alerts (system, or all) | `/activity` | alert |

## Scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Dev server (Turbopack). |
| `npm run build` / `npm start` | Production build / serve. |
| `npm run lint` | ESLint (flat config, `eslint-config-next`), then `lint:colors`. |
| `npm run lint:colors` | Fails on any colour literal or Tailwind palette class in `src/` outside `src/app/tokens.css`. |
| `npm run typecheck` | `next typegen` (route types for `PageProps`/`LayoutProps`), then `tsc --noEmit`. |
| `npm test` | Vitest. Covers the auth/MFA decision logic, the typed API client, the colour rule, the pure form <-> body conversions of the config editors (the strategy and jobs forms round-trip a fully populated body without loss), cron readings, history and import helpers, the transaction filters, the thesis board helpers, the review rules (allowed actions, reason and corrected-value validation, printed scale, verdict-run polling, affected KPIs) and the reference-data display rules (listing order and state, parent labels and GLEIF exceptions, decision messages, chart rebasing, risk-figure formatting, the plausibility record, closure selection), the freshness labels (age, schedule-aware staleness from the next due run with a grace margin, the fixed fallback thresholds, the news check's state), the job-run display (durations, summed cost, errors), the disputed-story and impact-note rules (source order, resolution body and note limit, citation numbering, thesis link, price move) and the digest's status sections; and for Phase 4 the feedback request builder, sentence paths, current marks, effects summary, batched mark loading and the surfaces checklist, the citation preview lookup (digest and impact note), the claim-check labels, popover placement, the Activity page rules (job-log filters and query, coverage gaps in words, alert labels and outcomes, trust and eval candidates), the replay-preview diff, the verification form (round trip of a fully populated body, client-side bounds) and the disputed-relationship conflict. |
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
  - `chart-bar` (allocation bars), `chart-line` (a holding's price line) and
    `chart-benchmark` (its benchmark, also drawn dashed). Recharts reads them as
    `var(--chart-line)`.
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
  The Activity section (`/activity`) holds the job log, coverage, alerts and source trust.
  It shows the status, HTTP code and version, or a clear "Backend unreachable" /
  HTTP-error message.

## Components

- `src/components/ui/`: shadcn/ui components (button, input, label, card, dropdown-menu),
  written by hand from shadcn's source and restyled with tokens. `components.json` is
  valid, so `npx shadcn add <component>` works where the shadcn registry is reachable.
  Swap any palette classes in added components for tokens.
- `src/components/tremor/`: Tremor Raw components, from Phase 1. See the README there.
