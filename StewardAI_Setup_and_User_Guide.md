# StewardAI — setup and user guide

A single owner's self-hosted portfolio copilot: it reads your Trading212 portfolio, stores
your strategy and per-holding theses, and uses Claude to monitor, verify, and research your
holdings. This guide covers getting it running and what every screen actually does. For the
original design spec, see
[AI_Portfolio_Copilot_Technical_Product_Description.md](AI_Portfolio_Copilot_Technical_Product_Description.md);
for a terser developer-facing reference, see the root [README.md](README.md),
[apps/backend/README.md](apps/backend/README.md) and [apps/web/README.md](apps/web/README.md).

---

## Table of contents

1. [What StewardAI is](#1-what-stewardai-is)
2. [Setup](#2-setup)
3. [Authentication and the security model](#3-authentication-and-the-security-model)
4. [Using the app: Portfolio](#4-using-the-app-portfolio)
5. [Using the app: Research & Verification](#5-using-the-app-research--verification)
6. [Using the app: Oversight](#6-using-the-app-oversight)
7. [The chat copilot](#7-the-chat-copilot)
8. [Using the app: Config](#8-using-the-app-config)
9. [Background jobs and automation](#9-background-jobs-and-automation)
10. [The confidence and verification system, in plain terms](#10-the-confidence-and-verification-system-in-plain-terms)
11. [Navigation and shortcuts](#11-navigation-and-shortcuts)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. What StewardAI is

It is **not** a trading app — it never places an order, never edits a Trading212 pie, and
its Trading212 client is read-only by construction (a hard-coded check refuses any non-GET
request before it's sent). What it does:

- Syncs your Trading212 positions, transactions and account value once a day (or on demand).
- Lets you write down your strategy (target weights, bands, hard limits, rules) and your
  thesis for each holding (why you own it, exit conditions, KPI thresholds).
- Reads company filings and news, extracts numeric facts, checks them in code (sanity
  bounds, cross-checks against the actual regulatory filing), and only then judges your
  KPIs pass/breach/unknown.
- Alerts you (Telegram/email) when something confirmed and material happens, or a rule
  breaches, or a KPI fails.
- Writes daily/weekly digests, on-demand deep-research reports and a monthly compliance
  review, every sentence of which cites what it's based on.
- Offers a chat copilot that answers from the same stored, cited data and can only take an
  action (an earnings check, a research run, a sync) after you explicitly confirm it.

It is **single-user**: there is no sign-up flow anywhere, and the backend rejects any token
that isn't the one configured owner's.

---

## 2. Setup

### 2.1 Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/) (backend package/dependency manager).
- Node 22+ and npm 10+ (frontend).
- A [Supabase](https://supabase.com) project (Free tier is enough for development), created
  in an **EU region** if you care about data residency.
- A Trading212 account and an API key with **read-only** scopes (demo or live).

### 2.2 Create and configure the Supabase project

1. Create the project, then apply the schema (plain SQL migrations under
   `supabase/migrations/`, applied with the Supabase CLI — this is **not** Alembic):
   ```bash
   npx supabase login
   npx supabase link --project-ref <project-ref>
   npx supabase db push
   ```
2. Give the backend's database role a password (the migration creates `app_backend` without
   one, so no secret lives in git). In the Supabase SQL editor:
   ```sql
   alter role app_backend with login password '<generate a strong secret>';
   ```
3. In the Supabase dashboard, under **Authentication**:
   - **Sign In / Providers** → turn **off** "Allow new users to sign up." (The local dev
     stack already has this off in `supabase/config.toml`.)
   - **Multi-Factor** → enable **TOTP** (both enroll and verify).
   - **Users** → **Add user** → create your own owner account (email + password).
4. Register yourself as the owner so Row-Level Security lets your account — and only your
   account — read the app's data. If your project has exactly one auth user at the time you
   ran `db push`, a migration does this automatically; otherwise run:
   ```sql
   insert into private.app_owner (user_id) values ('<your Supabase auth user id>');
   ```
5. Sanity-check two things the app depends on:
   - `select * from pgmq.list_queues();` should list a `jobs` queue.
   - `select id, public from storage.buckets;` should list a private `evidence` bucket.

### 2.3 Fill in the environment

There is **one shared `.env` file at the repository root** — both the backend and the
frontend read it. Copy it once and fill in every value; `.env.example` documents where to
get each one.

```bash
cp .env.example .env
```

The variables, grouped by what they're for:

| Variable | Required? | Purpose |
|---|---|---|
| `SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_URL` | yes | Your Supabase project URL (same value, one name per app). |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (or legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY`) | yes | Public browser key. RLS restricts what it can read to the owner only. |
| `DATABASE_URL` | yes | Postgres connection string for the `app_backend` role (never the `postgres` superuser or `service_role`). Supabase's direct host is IPv6-only unless the project has the IPv4 add-on — a host without IPv6 must use the session pooler (`app_backend.<project-ref>`). |
| `OWNER_USER_ID` | yes | Your Supabase Auth user UUID. The API refuses every other user's token outright. |
| `SUPABASE_SECRET_KEY` | yes | Backend-only service-role-level key, used to write the private `evidence` Storage bucket. Never exposed to the browser. |
| `SUPABASE_JWT_SECRET` | only for legacy projects | Only needed if your project still signs tokens HS256; leave empty otherwise (JWKS is fetched automatically). |
| `REQUIRE_MFA` | no (default `true`) | Set `false` only for local debugging — production should always require MFA. |
| `NEXT_PUBLIC_ALLOWED_EMAIL` | no | Extra client-side guard: any other signed-in email is signed out immediately. |
| `NEXT_PUBLIC_API_BASE_URL` | no (default `http://localhost:8000`) | Where the frontend finds the Python API. |
| `CORS_ORIGINS` | yes in prod | Comma-separated origins the API accepts (defaults to `http://localhost:3000` for dev). |
| `T212_ENV`, `T212_API_KEY`, `T212_API_SECRET` | yes | Trading212 demo or live account, **read-only** key. Verify with `cd apps/backend && uv run python -m steward.trading212`. |
| `ANTHROPIC_API_KEY` | yes | Claude API key — powers extraction, news classification, digests, impact notes, research, the monthly review, chat, and the claim verifier. |
| `ANTHROPIC_WORKSPACE_ID` | no | Only for org-level keys scoped to a workspace. |
| `SEC_USER_AGENT` | for EDGAR | Required by SEC's EDGAR API: filings, Form 13F, Forms 3/4/5 insider trades, Schedule 13D/G control stakes, N-PORT fund positions, XBRL fundamentals. Without it those jobs fail with a clear message. |
| `FINNHUB_API_KEY` | no | Adds a US earnings-calendar and company-news source. |
| `FRED_API_KEY` | no | Adds Fed macro series (Eurostat/ECB series need no key). |
| `OPENFIGI_API_KEY` | no | Raises the identifier-mapping rate limit (works unauthenticated too). |
| `ALPHA_VANTAGE_API_KEY` | no | Free key (25 requests/day, one shared budget). Fills a missing price series or valuation input and feeds owner-triggered exposure and statement lookups. |
| `EODHD_API_KEY` | no | Free plan (20 requests/day): last-resort fallback for a price series Stooq, Yahoo and Alpha Vantage could not supply. |
| `FMP_API_KEY` | no | Free FMP key: the optional `fmp_company` job stores company profile (sector, industry) and annual analyst estimates for holdings with a `sec_ticker` or `fmp_symbol` in their profile; also used by the access probes. |
| `TWELVE_DATA_API_KEY` | no | Free Twelve Data Basic key (8 credits/minute, 800/day): access probes, and the optional `twelve_data_corporate_actions` job that proposes dividends and splits as suggestions you confirm; a paid-plan answer is recorded and never retried. |
| `EDINET_API_KEY` | no | Free EDINET (Japan FSA) key: the optional `edinet_filings` job keeps the filing list and links of holdings whose profile has an `edinet_code` (no financial figures are parsed). |
| `COMPANIES_HOUSE_API_KEY` | no | Free UK Companies House key: the optional `companies_house_filings` job stores the company profile and filing history of holdings whose profile has a `companies_house_number`. |
| `UN_COMTRADE_API_KEY` | no | Free registered UN Comtrade key (500 calls/day). Needed for trade data and trade-exposure alerts; also requires the provider to be enabled in `/config/jobs` and an audit pass. |
| `EPO_OPS_CLIENT_ID`, `EPO_OPS_CLIENT_SECRET` | no | EPO Open Patent Services OAuth client: owner-triggered patent family refresh and legal-status alerts. |
| `DIFFBOT_API_KEY`, `PEOPLE_DATA_LABS_API_KEY` | no | Owner-triggered company research enrichment (company data only). Off by default. |
| `STOOQ_API_KEY` | yes (as of spring 2026) | Required for daily price history, risk metrics and benchmark series. |
| `HEARTBEAT_PING_URL` | no | A dead-man's-switch URL (e.g. healthchecks.io) the worker pings every minute, so a stopped worker is caught from *outside* the host. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for Telegram alerts | Alert/digest delivery channel. |
| `RESEND_API_KEY`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO` | for email alerts | Alert/digest delivery via Resend. |
| `APP_URL` | recommended | Public base URL used inside alert/digest links. |
| `LOG_LEVEL` | no | Backend log verbosity. |
| `STEWARD_TEST_DATABASE_URL` | dev/test only | A local Postgres superuser URL for running DB-backed tests. Never point this at your Supabase project. |

Every optional key degrades gracefully — the app runs without it, just with that one data
source or channel disabled. Removing a key never deletes stored data and never shows a zero:
the affected figure keeps its last verified value with its date, or shows as unavailable, and
the gap is listed in the coverage views. The per-provider detail (free quotas, expected data
delays, what exactly degrades) is in `docs/data/provider-access.md`; behaviour not yet
confirmed against a live account is marked unverified there. New free providers are also off
until you enable them under `providers` in `/config/jobs` and the Source audit
(`POST /jobs/source-audit`) has confirmed your plan covers the endpoint.

### 2.4 Run it locally

```bash
cd apps/backend && uv sync && uv run uvicorn api.main:app --reload   # API on :8000
cd apps/backend && uv run python -m worker                           # scheduler + queue worker
cd apps/web && npm install && npm run dev                             # web on :3000
```

Open `http://localhost:3000` — with Supabase configured you'll land on `/login`.

### 2.5 Fill an empty database once

A fresh project has no portfolio history yet. Backfill it in order (portfolio, reference
data, two years of prices and FX, holidays, macro data, the earnings calendar, 13F
ownership, news):

```bash
cd apps/backend && uv run python -m worker.backfill
```

You can also name individual jobs to run instead of the full sequence — see
`apps/backend/README.md`. After this, the scheduled jobs in `/config/jobs` keep everything
current on their own (see [§9](#9-background-jobs-and-automation)).

### 2.6 Windows notes

- Stop the worker with **Ctrl+C** (Windows has no real `SIGTERM`).
- `WinError 10013` on API start usually means Hyper-V/WSL/Docker reserved a port range that
  includes 8000, or antivirus is blocking it. Run on another port
  (`uv run uvicorn api.main:app --reload --port 8765`) and set
  `NEXT_PUBLIC_API_BASE_URL=http://localhost:8765`.

### 2.7 Deploying

`deploy/compose.yaml` (Docker Compose) and `deploy/systemd/{steward-api,steward-worker}.service`
(two separate systemd units — the API and the worker are independent processes) cover
running this on a standalone host. On a live host, use its secrets manager instead of a
`.env` file.

---

## 3. Authentication and the security model

There is **no sign-up flow anywhere in the app** — it is single-user by design.

1. **`/login`** — email + password (Supabase `signInWithPassword`). This is the first
   factor only (`aal1`).
2. **MFA is mandatory**, not optional:
   - No verified TOTP factor yet → **`/mfa/enroll`**: scan the shown QR code (or use the
     manual secret) in your authenticator app, then enter the 6-digit code to verify.
   - A verified factor exists, but this session hasn't used it yet → **`/mfa/verify`**:
     enter a 6-digit code to upgrade the session to `aal2`.
3. **Every route is protected server-side**, not just by hiding a link: a Next.js proxy
   (`src/proxy.ts`) runs on every request, checks your session and MFA level, and redirects
   you to whichever of `/login` / `/mfa/enroll` / `/mfa/verify` you actually need — so a
   bug in a page's own logic can't expose data.
4. **The backend checks independently.** Every API call's JWT is verified against
   Supabase's own signing keys, must belong to the exact configured `OWNER_USER_ID`, and
   (unless you've explicitly disabled it for local debugging) must carry `aal2`. With no
   owner configured, the API refuses every call.
5. **Row-Level Security** at the database layer means even a compromised frontend key can
   only ever read the owner's own rows — `anon` gets nothing.

If you ever see "not the owner" or get bounced straight back to login, check that
`OWNER_USER_ID` in `.env` matches the Supabase Auth user id of the account you're signing in
with.

---

## 4. Using the app: Portfolio

This is the cluster you'll open most — your positions, their value, and how they're doing
against your own targets.

- **Overview (`/`)** — your portfolio's home screen: total value, invested amount, cash,
  and unrealized P/L as stat cards (shown in your base currency alongside Trading212's own
  account-currency figure, so you can cross-check them); a **portfolio-value line chart**
  (flow-adjusted — a deposit or withdrawal never shows up as a fake gain or loss) with a
  benchmark overlay and a 3M/1Y/2Y/5Y/Max range picker; **allocation** by holding and by any
  custom category you've defined (sector, geography, currency, …), as both a stacked bar and
  a bar list; and a **drift** panel showing every position's weight against its target and
  band from the Strategy config, with a status badge (in-band / out-of-band / no target) and
  a diverging chart. If no sync has run yet you'll see a "Sync now" button instead.
- **Holdings (`/holdings`)** — every open position in one sortable table (quantity, average
  and current price, value, weight vs. target, drift status, unrealized P/L). Click any row
  to open that holding's full page. Tabs at the top switch to the Thesis board, the Review
  queue and Transactions — all four are really one section.
- **A single holding (`/holdings/[ticker]`)** — the richest page in the app, your working
  view for one company: stat cards (value, weight, P/L, next expected report date); buttons
  to run an earnings check now or re-run thesis verdicts (optionally against a specific
  document URL or report date); your thesis (why you own it, exit conditions, review date,
  with a link to edit it); a KPI table (target vs. observed vs. verdict vs. confidence,
  expandable to the full evidence and verdict history); a price-and-risk section (a Stooq
  price chart rebased against your chosen benchmark, plus volatility, max drawdown, beta and
  correlation with your other holdings); the holding's news; every extracted figure with its
  confidence badge and — for anything held or quarantined — an accept/correct/reject form;
  past deep-research reports and a button to run a new one; 13F institutional-ownership
  history; and identity/corporate-structure data (legal entity, group structure, suggested
  competitor/supplier/customer relationships you can confirm or reject).
- **Thesis board (`/theses`)** — every holding that has a thesis, worst-first: an overall
  status badge, pass/breach/unknown counts, a confidence bar, and one cell per KPI you can
  expand in place for its rationale, the observed value against its threshold, and links to
  every fact behind it. A KPI never yet judged shows as *unknown* rather than being hidden.
  Also flags theses that are overdue for review.
- **Review queue (`/review`)** — every figure the numeric validator **held** (failed a
  sanity check) or reconciliation **quarantined** (disagreed with the company's own XBRL
  filing), grouped by holding, with the exact check it failed, the quoted source snippet,
  and any restatement/XBRL comparison. **Accept**, **Correct** (with the right value) or
  **Reject**, always with a required reason — this re-runs the holding's thesis verdicts
  live and the page shows you which KPIs changed.
- **Transactions (`/transactions`)** — every order, dividend and cash movement Trading212
  reports, filterable by type/holding/date range, with dividend totals per currency and
  totals per type — computed over every matching row, not just what's loaded on screen.

---

## 5. Using the app: Research & Verification

Where you go to check *why* the app believes something, or to ask it to dig deeper.

- **Research (`/research`, `/research/[id]`)** — start a new deep-research run on any ticker
  under a chosen research profile (depth, model, source tiers, sections). Every figure in the
  resulting report is computed in code from stored records (never invented by the model);
  the model only writes the narrative, and every sentence carries a citation and a
  claim-verifier check mark.
- **Evidence (`/evidence/[factId]`)** — one extracted fact's complete paper trail: the exact
  quoted snippet, the surrounding archived document text, and links to both the archived
  copy and the original source URL.
- **News (`/news`)** — the live news feed, grouped by story, filterable by holding, severity,
  confidence and source tier. Shows how each story was corroborated (or not), links to every
  copy of it, and a **Check news now** button to run an on-demand ingest.
- **Disputed stories (`/news/disputed`)** — stories where sources actively contradict each
  other: the claim and the contradicting (and any supporting) sources side by side. A
  disputed story never alerts, and it **freezes** the affected holding's thesis verdicts
  until you resolve it — **Confirm: it happened** or **Mark false**.
- **Reports (`/reports`, `/reports/[id]`)** — the digest archive (daily and weekly), tabbed
  alongside impact notes, monthly reviews, and full-text search across every report ever
  written. A digest is assembled per holding: story summaries, thesis-verdict changes,
  portfolio flags, macro figures, 13F changes and market holidays, every sentence cited.
- **Escalations / impact notes (`/escalations`, `/escalations/[id]`)** — a short AI-written
  assessment triggered by a confirmed severity-2+ story or a large single-day price move:
  what triggered it, which thesis KPI or exit condition it touches (or a statement that none
  applies), and its full source list.
- **Monthly reviews (`/reviews`, `/reviews/[id]`)** — a compliance table listing every
  strategy rule as pass/breach/unknown (computed in code, never by the model), your KPI
  statuses, and AI-proposed actions — each one must cite the specific rule or KPI it rests
  on, or it's dropped. Nothing is ever traded or changed by a review; it only reports.

---

## 6. Using the app: Oversight

The "is everything actually working" cluster.

- **Activity (`/activity`)** — five panels in one page: **coverage** (per-holding gaps —
  a holding with no successful news check or a stale price bar shows a flagged reason, with
  no query needed); **alerts** (what the system's own monitor has fired — failed jobs,
  missed runs, coverage gaps — plus "all alerts" for the full history); **job log** (every
  run, filterable by type/status/holding, with duration, cost, and the failed step if any);
  **source trust** (per-outlet trust scores as moved by your own feedback, and whether a
  source has been demoted a tier); and **eval candidates** (figures/news items you marked
  wrong that are queued to strengthen the automated test suite).
- **Status (`/status`)** — a quick API health check, a "look up job by number" box, and a
  reference-data-gaps panel (instruments whose identity or price history couldn't be
  resolved).
- **One job run (`/jobs/[id]`)** — every step of a single run: status, timing, total model
  cost, models used, and each step's own status/cost/error. Auto-refreshes while the run is
  still in progress. Reached from job-log rows or any "queued as job #…" message.

---

## 7. The chat copilot

Ask it anything about your portfolio, a specific holding, the news, or any report. It lives
in two equivalent places: the full `/chat` page (with a conversation list) and a slide-over
panel reachable from the **Chat** button in the header, or **Open chat** in the command
palette, on every screen.

Every answer is sentence-by-sentence, and every sentence that makes a claim carries a
numbered citation into a source list — a fact, a news item, a report. A sentence with
nothing to cite is explicitly marked "no source" rather than left looking authoritative, and
one the claim verifier can't support is marked "Unsupported" with the reason. The same
wrong/irrelevant/useful feedback control used everywhere else in the app is available on
chat sentences too.

**The copilot is not read-only.** It can propose an action — an earnings check, a research
run, a portfolio sync — shown as a card with its reasoning and explicit **Confirm** /
**Cancel** buttons. Nothing runs until you click Confirm; once you do, it's a real job you
can follow to completion the same way as any other run.

---

## 8. Using the app: Config

Everything here is **versioned**: every save creates a new version (validated by the backend
before it's stored), takes effect on the *next* run with no deploy needed, and is fully
recoverable — every domain has its own diff/rollback history.

| Page | What you configure there |
|---|---|
| `/config/strategy` | Base currency, FX source, weight basis, goals/horizon/notes; custom allocation categories (sector, geography, …) and a per-holding classification table; target weights and bands for positions/categories/cash, hard limits, and portfolio rules (position weight, category weight, position count, …). This is what the Overview's drift panel and the monthly review's compliance table are computed against. |
| `/config/jobs` | Every schedule (cron + timezone + enabled) for sync, news ingest, digests, the reference/price/macro/holiday/13F collectors and the monthly review; per-job-family settings (model, effort, caps); exchange closures; tracked 13F managers; macro series definitions. |
| `/config/theses` | Your "why owned" text, exit conditions, review date and KPI thresholds for each holding. Saving immediately re-judges that holding's verdicts. |
| `/config/holding-profiles` | Per-company reference data: fiscal year-end, reporting currency, SEC CIK, Stooq price-symbol override, IR/regulatory URLs, aliases, and competitors/suppliers/customers/watch topics that steer what news the pipeline treats as relevant. |
| `/config/news-sources` | Which feeds/pages are read, their trust tier, and global outlet-trust settings (domain→tier mapping, blocked/allowed lists, freshness window). |
| `/config/event-rules` | Severity per event type (with per-holding overrides), what triggers an escalation or an earnings check, price-move and macro-surprise thresholds, and regulatory-announcement classification rules. Saving shows a **7-day replay preview** of exactly which past alerts would change, before you confirm. |
| `/config/notifications` | Ordered notification rules (first match wins), quiet hours, daily cap, repeat-throttle window, muted tickers, and a **Send a test alert** panel that routes through your saved rules exactly like a real event. Also gated by the 7-day replay preview. |
| `/config/verification` | Every threshold of the defence layers: sanity bands per metric, corroboration rules (how many independent sources confirm a story), source-trust penalty/reward, and the claim verifier's settings. |
| `/config/research-profiles` | Named research "shapes" — depth, model, effort, caps, which sections to include and in what order, source tiers, valuation methods. |
| `/config/prompts` | The system prompt behind each AI step, shown against its built-in default, with a warning that an overridden prompt isn't covered by the automated evals. |
| `/config/budget` | Monthly spend cap, warn threshold, what happens at the cap (downgrade model or stop non-critical jobs), per-model pricing, and a cost breakdown by job type and model. |
| `/config/calendar` | Confirms which suggested public holidays are actual exchange closures for your holdings, so a quiet market day doesn't look like a missed job. |
| `/config/history`, `/config/history/[domain]` | Every past version of every config domain: diff any two, view raw JSON, or roll back. |
| `/config` (index) | All domains as cards, plus **Export** (every active version as one JSON file) and **Import** (validated domain-by-domain; any single error rejects the whole file, so you can never end up half-imported). |

---

## 9. Background jobs and automation

A separate **worker** process (`uv run python -m worker`, its own systemd unit in
deployment) runs everything on a schedule via APScheduler, re-reading `/config/jobs` every
few minutes — a schedule change never needs a restart. Jobs also queue through Postgres
(`pgmq`), so an on-demand "run now" button and a scheduled run go through the same path.

Typical schedule (all configurable):

| Job | Default cadence | What it does |
|---|---|---|
| Portfolio sync | daily | Trading212 positions, FX rates, a day's snapshot, new transactions, drift. |
| News ingest | every 2h, 06:00–22:00 | Reads every enabled source, links items to holdings, clusters duplicates, classifies and corroborates. |
| Earnings check | on report date +1/+2/+4 days, or on demand | Locates the release, extracts figures with Claude, validates them in code, judges KPI verdicts. |
| Thesis verdicts | on demand (or after a thesis/fact change) | Re-judges KPI pass/breach/unknown from currently verified facts. |
| Daily / weekly digest | 07:00 weekdays / Sun 18:00 | Assembles and sends the cited summary report. |
| Monthly review | first weekday of the month | Compliance table, KPI statuses, cited proposed actions. |
| Reference data / prices / FX / macro / holidays | weekly / weekdays / daily / monthly | Keeps identity, price history, risk metrics and the calendar current. |
| Reconciliation | daily | Compares press-release figures against the later official filing. |
| Macro data / public data | daily 11:30 / off by default | Eurostat, ECB, FRED, ČNB daily; World Bank, OECD, BIS and UN Comtrade only after you enable the provider and the audit passes. |
| Fundamentals and valuation | Tue 05:40 / daily 07:45 | Shares, revenue by region and segment, P/E, P/B and yield from SEC and ESEF filings. |
| Insider trades | daily 06:30 | SEC Forms 3, 4 and 5 for held issuers, classified economically. |
| Control stakes (`control_stakes`) | daily 06:45 | SEC Schedule 13D/G holders above 5% for held issuers. Reported separately from 13F and fund holdings; never added to them. |
| N-PORT holdings (`nport_holdings`) | Saturdays 05:15 | What US-registered funds you hold report owning, about 60 days after quarter end. UCITS funds do not file N-PORT. |
| Fund probes (`fund_probes`) | off by default | Checks whether your free Finnhub/FMP plan covers fund, index and ownership endpoints; a paid-only answer is recorded and not retried. |
| Fund characteristics audit (`fund_characteristics_audit`) | daily 07:30 | Watches the iShares/Vanguard fund P/E and P/B pages for layout or methodology changes so a parser problem is not mistaken for a market move. |
| Free-source alerts (`free_source_alerts`) | daily, after macro data | Credit stress, trade-exposure, patent-status and confirmed company-graph alerts, with a readable decision trail (below). |
| Monitor | every 15 min | Raises one alert each for a failed job, a missed scheduled run, or a holding with no news coverage. |

A "catch up on start" option in `/config/jobs` makes the worker run anything that never
succeeded or missed its last scheduled run, in order, when it starts up — so a fresh
install or one that's been off for a while fills itself in without waiting on each cron. A
`HEARTBEAT_PING_URL` dead-man's-switch, pinged every minute, catches the case where the
worker itself has stopped.

### 9.1 Free-source alerts and the decision trail

Four rules watch the free data and can raise an alert (they appear as news items of type
`credit_stress`, `trade_exposure`, `patent_status`, or as deal / management / relationship
events, and go through your normal notification rules, quiet hours and daily cap):

- **Credit stress**: a BIS, ECB or FRED series you configure crosses its threshold on a *new*
  period. The shipped thresholds (ECB bank-lending-survey credit standards above 10, BIS US debt
  service ratio above 15) are starting points, not calibrated values; tune them in the Event
  rules config under `free_source_alerts`. Revised or back-filled old periods never alert.
- **Trade exposure**: a UN Comtrade period for a product code you have mapped to a holding
  (weight at least 5%) moves 20% or more year on year. This is national trade data, not the
  company's revenue; exposure you have not mapped is treated as unknown, never as zero.
- **Patent status**: a patent family evidenced as the company's lapses or is granted. A
  status the source could not read ("unknown") keeps the last known status.
- **Company graph**: only relationships and events you or a primary source have *confirmed*
  alert. Suggested or disputed ones are listed for your review and never raise an alert or a
  thesis breach.

Each rule alerts at most once per series or holding and product per day, and a replayed
observation never alerts twice. To see why something did or did not alert, read the decision
trail (`GET /alerts/free-source-trail`, filters `rule`, `outcome`, `ticker`): outcomes are
`raised`, `throttled`, `revision_ignored`, `baseline` (first load of a series), `not_material`,
`no_baseline`, `persisting`, `review_needed` and `below_severity`, each with its reason and the
observation behind it. There is no web page for the trail yet. Run the evaluator now with
`POST /jobs/free-source-alerts`; turn all four rules off with `free_source_alerts.enabled =
false` in the Event rules config.

---

## 10. The confidence and verification system, in plain terms

Every extracted number and every news story carries a confidence tier, so you know how much
to trust it *before* it's used in a verdict, an alert, or a report:

- **Verified** — passed every automated check (sanity bounds, and for US filings,
  reconciled against the actual XBRL data); treated as fact.
- **Corroborated** (news) — enough independent outlets reported the same thing, or one
  sufficiently authoritative one did, to count as confirmed even though no single source is
  fully authoritative alone.
- **Unconfirmed** — not enough corroboration yet; visible in the feed, but it won't trigger
  an alert or move a thesis verdict.
- **Disputed** — a trusted source directly contradicts the claim. This **freezes** the
  affected holding's verdicts until you resolve it on `/news/disputed`.

For extracted **figures** specifically there's also a **held / quarantined** state (visible
on the Review queue and each holding's Extracted figures table): a figure that fails a
sanity check is held, one that disagrees with the company's own regulatory filing is
quarantined, and either way it feeds *no* verdict until you accept, correct or reject it.

You'll see this system everywhere:

- The **Thesis board** and each **holding page**'s KPI table show a confidence badge per
  verdict.
- The **Evidence page** lets you check any figure against its literal source text.
- **News** and **Disputed stories** show confidence/severity badges and let you resolve a
  contradiction.
- Every report, digest, escalation, research section and chat answer carries citations and a
  claim-verifier check ("Unsupported: …" when a sentence can't be backed up), plus a
  universal **wrong / irrelevant / useful** feedback control. Marking something wrong can
  lower a source's trust score (visible under Activity → Source trust) and, on a figure,
  quarantines it and re-judges whatever depended on it — automatically.

---

## 11. Navigation and shortcuts

- **Command palette** — `Cmd/Ctrl+K`, or the **Search** button in the header. Reaches every
  page, every config editor and its history, the thesis board, review queue, transactions,
  exchange calendar, reference-data gaps, disputed stories, impact notes, the Activity
  page's job log/coverage/alerts/source-trust/eval-candidates, API status, research, monthly
  reviews, report search, research profiles, and every holding by ticker. It also opens a
  job run by typing its number, opens the chat panel ("Open chat"), and can start any
  check (sync, news check, digests, verdicts, reference data, price history, holidays,
  macro data, the monthly review, the 13F load, or an earnings check/research for a named
  holding) directly from the keyboard.
- **Theme toggle** in the header — Light, Dark or System.
- Tabular figures throughout the app use tabular (monospaced-width) numerals, so columns of
  numbers always line up.

---

## 12. Troubleshooting

- **Every route redirects to `/login`** — Supabase env vars are unset or wrong; `/login`
  itself will say which ones are missing.
- **"Not the owner" or an immediate sign-out** — `OWNER_USER_ID` doesn't match the Supabase
  Auth user id you signed in with, or (for the email guard) `NEXT_PUBLIC_ALLOWED_EMAIL`
  doesn't match your account's email.
- **A `NEXT_PUBLIC_*` change doesn't seem to take effect** — these are inlined at build
  time; restart `npm run dev` (or rebuild) after changing them.
- **`WinError 10013` on Windows** — see [§2.6](#26-windows-notes).
- **A scheduled job never seems to run** — check `/config/jobs` for its cron/timezone and
  the enabled flag; the worker re-reads it within a few minutes of a save, no restart
  needed. Then check the job log on `/activity` for whether it ran and failed, versus never
  having been scheduled at all.
- **DB-backed backend tests are skipped** — they need `STEWARD_TEST_DATABASE_URL` pointed at
  a throwaway local Postgres instance; without it they skip rather than fail.
- **A source shows `access_unverified`, `paid_only` or `quota_exhausted`** — see
  `GET /sources/access` and `docs/data/provider-access.md`. A missing key is `access_unverified`
  ("credential not configured"); a plan denial is `paid_only` and is not retried until you change
  the key; free quotas reset on the provider's schedule. Gaps stay visible, they are never filled
  with zeros.
- **`ownership_13f`, `control_stakes`, `nport_holdings` or `insider_trades` fail at once** —
  `SEC_USER_AGENT` is unset or has no contact email.
- **A free-source alert did not fire** — read the decision trail; a revised old period, a first
  load, a throttled repeat or a suggested (unconfirmed) relationship is recorded there with the
  reason.
