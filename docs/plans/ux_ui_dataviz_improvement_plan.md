# UX / UI / Data-Visualisation Improvement Plan

Date: 2026-10-06. Scope: `apps/web` (Next.js 16, Tailwind 4, custom SVG chart kit in `components/charts`, recharts only in `price-chart` / `contribution-waterfall`).

**Status of evidence.** Findings below come from a code audit (two sampling passes; overview, holding page, job log, strategy/digest editors best covered). They have **not** yet been confirmed in a live browser. Not read in depth: chat, login/mfa, verification pages, status, command palette. Phase 0 closes that gap. File:line references can drift; re-grep before editing.

---

## 1. Headline problems

1. **Pages are too long.** Holding page ≈ 35 stacked panels with no in-page navigation (`holdings/[ticker]/holding-view.tsx:254-383`). Overview = 9 grid rows (`overview-view.tsx:190-263`). Strategy editor 7 sections / 1040 lines. Digest ≈ 12 sections, no TOC. Review/monthly review 5-7 sections. Funds and Relationships 6 panels each. Research report 711 lines, no TOC.
2. **Raw data leaks into the UI.** `<pre>` JSON for job params/step detail; raw enum/snake_case (`trigger`, `failed_step`, `kpiId · basis`); raw run ids, CUSIPs, `R-0001` ids; `JSON.stringify(error.detail)`; `toFixed(4)` FX; walls of `—` in identity/insider/transaction tables; minified one-line JSX with inline `money()` in investors files.
3. **Tables are not mobile-ready.** Holdings `min-w-[72rem]`, transactions `min-w-[62rem]`, watchlist `min-w-[1160px]` (per-row forms, no sort/filter). Top nav is `overflow-x-auto min-w-max` with clipped dropdowns on small screens.
4. **Numbers where charts belong.** Job log, cron schedules, budget, KPI targets, watchlist targets, investor positions, relationships, escalations, verification are tables or text.
5. **Copy-paste UI primitives.** Section h2 style in ~30 files, `blueprint p-5` card, loading state, hand-styled selects, dirty-state check repeated in 11 editors, ad-hoc date formatting.

---

## 1a. Information architecture redesign (proposal — owner approved restructuring)

**Problems with today's nav (`lib/nav.ts`):** 4 clusters, 21 pages. "Research & Verification" is a grab bag of 9 unrelated pages (Theses, Review queue, Relationship review, Research, Screens, Macro, News, 13F investors, Glossary). "Oversight" mixes things *waiting for the owner* (Escalations) with system health (Activity, Data sources, Verification) and documents (Reports, Reviews). Decisions are scattered across 5 places (Escalations, Review queue, Relationship review, News → Disputed, Verification). Holdings and Transactions exist as both nav items and tabs inside Holdings. Funds and Watchlist are separate pages although they are views of "what I hold / might hold". Config is one link to a 12-card hub that duplicates its own sidebar. Review queue is a whole page for an empty state. Holding detail is a 33-screen page.

**Proposed groups (organised by the question being asked):**

| Group | Question | Pages (tabs inside a page marked ›) |
|---|---|---|
| **Portfolio** (home `/`) | What do I hold and how is it doing? | **Overview** › Performance · Allocation & drift · Risk · Cash flow & exposure · Data quality<br>**Holdings** › Positions · Heatmap · Charts · Funds & ETFs · Watchlist · Transactions (absorbs `/funds`, `/watchlist`, `/transactions`; old URLs redirect)<br>**Holding detail** `/holdings/[ticker]` › Overview · Thesis & evidence · Financials · Ownership · Linkages & exposure · Data & identity |
| **Research** | Is my thesis still true? What's out there? | **Theses** (board, with evidence and KPI verdicts)<br>**Research** (reports)<br>**News** (stories, disputed as a filter)<br>**Screens**<br>**Markets** › Macro series · 13F investors (ownership of the market) |
| **Briefings** | What happened while I was away? | **Reports** › Daily · Weekly · Impact notes · Search<br>**Reviews** › Monthly reviews (Reports + Reviews become one page with tabs) |
| **Inbox** (badge with count in nav) | What needs a decision from me? | One page, filter tabs with counts: Escalations · Figure review · Relationship review · Disputed news · Verification flags. Each item is actionable inline; empty tabs are hidden or show one line. Replaces 5 pages and the empty Review-queue page. |
| **System** | Can I trust it, and what does it cost? | **Activity** (job runs, timeline) · **Data sources** (coverage, usage, **budget** moved here from Config as a read view) · **Verification quality** (gates, shadow report) · **Status** |
| **Settings** (gear icon, right side) | How should it behave? | Config hub as a grouped sidebar instead of a 12-card page: *Investment rules* (Strategy, Theses, Holding profiles, Event rules) · *Sources & data* (News sources, Calendar, Research profiles, Verification) · *Automation* (Jobs, Budget, Prompts, Notifications) · *History & export*. `/config` redirects to Strategy; each editor keeps its summary header + sticky SaveBar. |

Other placement decisions
- **Glossary** → "?" help menu / command palette entry, not a nav item. Inline term tooltips link to it.
- **Chat** stays a global slide-over (Ctrl+J) — already right.
- **Ticker switcher** (type-ahead) in the holding-detail header to jump between holdings without going back to the list.
- **Mobile:** bottom tab bar with Portfolio · Research · Inbox · Briefings · More (System, Settings); replaces the horizontally clipped top nav.
- **Top nav** drops from 4 dropdown clusters (21 links) to 5 labels + Settings gear; page-level tabs carry the rest, URL-addressable via sub-routes (`/holdings/[ticker]/financials`) so tabs are linkable and lazily rendered.

Migration / safety
1. Single source of truth stays `lib/nav.ts` (+ `nav.test.ts`): add `Cluster.tabs`, per-page `badge` source (Inbox count), and a `REDIRECTS` map consumed by `next.config.ts` `redirects()` so every old URL (`/funds`, `/watchlist`, `/transactions`, `/review`, `/relationships`, `/reviews`, `/news/disputed`, `/config/budget`, …) keeps working; evidence/job/escalation deep links are unchanged.
2. Update command palette index, breadcrumbs/eyebrows in `PageHeader`, and any hard-coded `href`s (grep for old paths; add a unit test that every nav/redirect target resolves to a real route).
3. Ship in two steps: (a) tabs + redirects with old nav intact, (b) switch nav to the new groups once every page is reachable.
4. Open question for the owner: is "Inbox" a good name vs "Attention"/"To review"? Should Settings sit in the user menu instead?

---

## 1b. Live browser findings (confirmed 2026-10-06, desktop 1440 + mobile 390)

Scanned: overview, holdings, holding detail (AMD, GOOGL), transactions, watchlist, funds, theses, news, reports, activity, data-sources, config hub. Not yet viewed live: strategy and other config editors, escalations, verification, review, screens, investors, relationships, macro, research, glossary, chat, dark mode. Run those in Phase 0.

**Unwanted gaps / dead space**
- Overview: *Drift* card is full-width for one "Cash" row (~250px blank); *Monthly returns heatmap* card ≈ 320px tall for a single 12-cell row; *Weights against targets* card sits beside the tall Contribution chart with ~700px blank below it; *Cash flow* "Loading transactions…" box stretches to the Exposure card's height. Cause: grid rows stretch cards to the tallest neighbour. Fix: `items-start`, content-sized rows, or pair panels of similar height; merge Drift + Weights-vs-targets into one panel.
- Reports (`/reports`): left column ends after the macro table while the *Sources* rail keeps running ≈ 3,000px (≈ 40 near-identical "Insider transaction report (Form 4)" entries) → large empty area beside it. Fix: collapse/group sources by domain with counts, cap rail height (internal scroll) or make it sticky.
- Holding detail: the three-card row (Thesis / History / Next event) is 277px each for one-line empty states; 6 consecutive empty-state cards (Customer concentration, Country & industry, Trade, Input costs, Patents, Estimates) of 118-240px each; Financial history and Cash & debt are just a sentence. Fix: one "Exposure & dependencies" card with a single "not populated yet — run <job>" state and a checklist of what is missing.
- Config hub: 12 cards repeat the left sidebar one-for-one; card heights uneven; sidebar badges inconsistent ("0", "30", "$25", none).

**Raw / overlong content**
- Holding detail page is **29,642px tall** (≈ 33 screens). `Identity decisions, filings and issuers` is one unpaginated **431-row table, 16,288px** with raw candidate issuer/security ids; `Identifiers, listings and group` is 4,484px (52-row MIC table + 15-row FIGI table). Fix: paginate/virtualise + filters, collapse by default, move to a "Reference data" tab/sub-route, resolve ids to names.
- Business network table lists 21 rows with **duplicate counterparties** (Broadcom ×2) and "Start not stated" repeated 19 times.
- Overview Allocation list and "No target set for …" show **raw tickers** (`CSGa_EQ`, `AMD_US_EQ`, 30 tickers in one sentence) while Holdings shows company names → use names + symbol chip; replace the sentence with a count + expandable list.
- Holdings table: 30 identical "NO TARGET" badges and "no target" labels; mute or collapse when no targets exist.
- News: ~150 of 164 stories are near-identical "Ignored" SEC 8-K keyword hits ("<Company> 8-K mentions 'tariffs'") that flood the list with two-line rows. Fix: default filter hides *Ignored*, group identical-pattern hits ("34 8-Ks mention tariffs"), day headers.
- Activity page 14,386px; data-sources 8,589px (57-row table); a wide heatmap table (2,311px) inside a clipped container.
- Peers table: "Position" mini-scale for a single value (min=0 → dot at far right) is meaningless; "Not computable" in every Peer-mean cell.
- Holding page: Financial history says "No annual statement facts are stored" while the Peers panel shows revenue growth/margins for the same holding — data inconsistency to check in backend.
- Not-found holding page shows "Try again" (pointless for 404) and a bare `AMD` breadcrumb.

**Second sweep (investors, macro, screens, relationships, escalations, review, verification, strategy, dark mode)**
- Escalations: the item body prints a **raw Anthropic API error** (HTTP 400, `request_id`, "domains are not accessible to our user agent: apnews.com, ft.com …"). Show "Assessment failed — retry" with details collapsed. This also exposes a likely backend bug: the impact-note call passes a web-search tool with blocked domains; fix in backend plan. The right column's two charts have large empty areas ("No acknowledgements yet" box, 1 bar on a 12-week axis) and the page leaves ~400px blank bottom-left.
- Relationships: 5 of 6 sections are empty states ("Choose a holding") ≈ 2,000px; the holding is a free-text field (`e.g. AAPL_US_EQ`) → searchable select that defaults to the first holding; suggested items include near-duplicates (same TED notice for Ville de Bois-Colombes ×2) and low-signal public-procurement awards.
- Macro: small multiples have **no axes, dates or units on the plot**; each card repeats "Revised periods: Not tracked / Average revision size: Not computable / Vintage unavailable" (noise ×12); a raw id title (`us_unemployment`); three cards titled "DE Economic sentiment indicator" differing only by geography code; "Country code" is free text; the "How much of the portfolio is mapped" bar is a full-width blue "Unknown 100%" (reads as a filled bar for 0% mapped).
- Investors / Screens / Review queue: page-level empty states are fine but each occupies a full page; Screens form uses ~40% of the width.
- Verification: 4 sections, 16-row table 1,137px wide in a narrower container (horizontal scroll); Strategy editor 24 inputs across 7 sections (3,066px).
- Page header inconsistency: 13F investors has no eyebrow/section label; others do.
- Perceived performance: on overview 8 panels sit on spinners ("Loading performance…", "Loading history…", "Loading transactions…") for 10+ s each, and the monthly-heatmap card reserves its full height while loading. Add skeletons with fixed aspect ratios and one batched summary request; show cached last-known values.
- Dark mode (overview) renders correctly; the hero stays solid blue in both themes. Still to check: contrast of muted text in dark, chart palettes in dark on other pages.

**Mobile (390px) confirmed**
- Top nav clipped ("Confi…") with a visible horizontal scrollbar under it; no drawer.
- Holdings table 1,173px wide inside 454px container; page horizontally scrolls the table only (no card fallback). KPI tiles stack to ~480px before any content.

**Data coverage (important for viz)**: for GOOGL and AMD the following panels are *empty states*: Financial history, Cash & debt, Estimates, Patents, Customer/supplier concentration, Country/industry context, Trade (HS codes), Input costs/commodities. Charts for several of these already exist in code (`financial-history-panel`, `cash-debt-panel`, `trade-panel`, `input-costs-panel`, `segments-panel`) but are never seen. Phase 5 must (a) verify which backend jobs populate them and run them, (b) add a "coverage" strip so empty ≠ broken, and (c) design each chart against real data, not mocks.

---

## 2. Phase 0 — Live UI audit (remainder)

*Done: see §1b. Remaining routes and dark mode below.* (Original note: Playwright MCP is not connected; chrome-devtools MCP was used. Dev API runs on port **8765** per `.env`, web on 4000.)

Playwright MCP is not connected. Use claude-in-chrome (user logs in once incl. MFA) or enable Playwright via `/mcp`. Needs `pnpm dev` (port 4000) + backend running.

Step → verify:
1. Walk every route in `app/(app)` at 1440px and 390px, light + dark → one screenshot per route-state, no more.
2. Record: horizontal overflow, clipped dropdowns, truncation, contrast, empty/loading/error states, console errors.
3. Append confirmed bugs to §5 below; drop code-audit findings that don't reproduce.
4. Clean up browser/dev-server processes afterwards.

---

## 3. Shared foundations (do first — everything else reuses them)

| # | Component / helper | Replaces | Notes |
|---|---|---|---|
| F1 | `<SectionNav>` (sticky right-rail TOC on ≥lg, horizontal chips on mobile) + `<Section>` accordion wrapper | stacked panels | scroll-spy, deep-link hash, remembers collapsed state in localStorage (try/catch) |
| F2 | `<ResponsiveTable>` (table ≥md, card list <md, sortable headers, sticky first col) | `min-w-[…]` wrappers | column config declares `primary`, `secondary`, `hideOnMobile` |
| F3 | `<NullCell/>` + `formatters` (money, pct, shares, fx, id-short, cusip) in `lib/format` | `?? "—"`, inline `money()`, `toFixed(4)`, `toLocaleString` | one rule: null → muted "—" with `title="Not reported"`; units always shown |
| F4 | `<SectionHeading>`, `<Panel>`, `<LoadingState>` (skeleton), `<KeyValue>`, `<JsonDetail>` (pretty rows + collapsed "Raw") | 30× h2 class, `blueprint p-5`, spinner text, `<pre>` JSON | |
| F5 | Enum label maps (`lib/labels.ts`): job trigger, step names, severity, status, KPI basis | raw snake_case | unit-test that every backend enum has a label |
| F6 | `useConfigEditor` hook (dirty baseline, save, guard) | JSON.stringify check in 11 editors | SaveBar already good |
| F7 | `<ResponsiveNav>` (drawer below md) | `app-nav.tsx` overflow scroll | focus trap, Esc, aria |
| F8 | Form `<Select>` / `<FilterChips>` from `components/config/fields` | hand-styled selects in job-log | |

Verify: unit tests for F3/F5; Storybook-less visual check via one demo route is optional.

---

## 4. UX plan by page (long pages & raw data)

### Holdings → `[ticker]` (H)
- Split into tabs/sub-routes: **Overview** (KPIs, price, thesis summary) · **Thesis & Evidence** (Thesis, Facts, Research, News) · **Financials** (Segments, FinancialHistory, CashDebt, Estimates, Peers, InputCosts) · **Ownership** (Ownership, Stakes, InsiderTrades) · **Linkages** (Network, Patents, PatentOverlap, Concentration, MacroContext) · **Reference data** (Identity, PriceData, CorpActionCoverage, MorePanels).
- Lazy-mount inactive tab panels; keep `?tab=` in URL.
- `identity-section.tsx:350-355` FIGI/exchange table → `<details>` collapsed by default.
- `holding-view.tsx:583` replace `kpiId · basis` with label map (F5).

### Overview (H)
- Summary-first: hero + 4 tiles + one allocation/drift view + top 3 alerts. Move Risk, Heatmap, Concentration, CashFlow, Exposure, Contribution, DataContext, KnockOn, Valuation into tabs (Performance · Risk · Exposure · Cash flow · Data quality).
- Merge duplicated Allocation + Drift + DriftTable into one panel with a view toggle.
- FX `toFixed(4)` → F3.

### Holdings list / Transactions / Watchlist (M-H)
- F2 on all three; card layout < md.
- Watchlist: replace fixed 1160px form grid with a table + row-expand editor; add sort/filter/search.

### Investors, Funds, Relationships, Research, Glossary
- Investors: expand minified JSX, move `money()` to `lib/format`, CUSIP → monospace + copy button + issuer name if resolvable.
- Funds / Relationships: tabs (F1) instead of 6 stacked panels.
- Research report: TOC (F1), replace raw `R-0001` with title + short id chip.
- Glossary: A-Z jump bar; `role=group` on category buttons.

### Strategy editor (H)
- Tabs or F1 nav across 7 sections; sticky summary header (target sum = 100%, drift status, unsaved count).

### Digest / Review / Monthly review (H-M)
- Right-rail TOC; headline + movers always open, lower-priority sections (managers, holidays, sources) collapsed.

### Jobs editor, Activity, Job detail (M)
- Jobs editor: accordion per schedule/section with one-line summary (cron in words, enabled).
- Job log: label maps (F5), truncate error with expand, run id as short chip.
- Job detail: params/step detail via `<JsonDetail>` (F4).
- Calendar: format `error.detail` as message, not JSON.

### News (M)
- Group by day with sticky day headers; virtualise or "load more" with count; keep filters in URL.

### Global shell (M)
- F7 nav; consistent `PageHeader` with page-level actions; command palette check in Phase 0.

---

## 5. UI bug / polish backlog

Confirmed from code (re-verify in Phase 0):
- Nav clipping on mobile (`app-nav.tsx:31-32`).
- Mixed date formatting: `toISOString().slice(0,10)` in `sessions-section.tsx:33`, `overview-panels.tsx:191`, relationships panels → `formatDate`.
- Macro geography filter is free text → select; chart limit of 12 series with no paging.
- Missing skeletons (spinner text only).
- A11y: category buttons without group role; verify chart `aria-label`/table fallbacks, keyboard focus on drilldowns, colour-only status encoding.
- Duplicate wrappers `overflow-x-auto border` → F2.

To add after Phase 0: contrast/dark-mode issues, overflow screenshots, console errors.

---

## 6. Data visualisation plan

Existing kit (reuse): sparkline, line, bar, hbar, donut, treemap, sankey, radar, heat, waterfall, candle, drawdown, grid-status, audit-trail, ring, vintage-fan, revision-line. Colour/tooltip rules: follow `components/charts/shared.tsx` and `lib/palette`.

### New shared chart components
| Chart | Purpose | Build on |
|---|---|---|
| **BulletChart** | value vs target/band | hbar + marker |
| **RangeBar** (buy ← price → sell) | watchlist targets | hbar |
| **RunTimeline / Gantt** | job runs by type lane; step durations; cron schedules | new SVG, time axis |
| **CalendarHeatmap** | transaction flows per day, market closures | heat |
| **SlopeChart / DeltaBar** | previous → reported position value | bar |
| **ForceGraph** (small, ≤60 nodes) | relationships | existing `network-panel` as base |
| **PercentileBar** | screens metric in distribution | hbar |
| **Histogram / Box** | escalation ack latency | bar |
| **DiffRows** | config version before/after | text + timeline |

### Holding-page visualisation upgrades (user-requested; every panel gets Chart | Table toggle, same data)
| Panel | Today | Target |
|---|---|---|
| Business network (`network-panel`) | 21-row table, duplicates | Force/radial graph: holding at centre, nodes = counterparties coloured by relationship (customer, supplier, competitor, subsidiary, partner), edge style = confirmed vs suggested, size = disclosed value; click → evidence. Dedupe by counterparty id. Table tab retained. |
| Revenue mix (`segments-panel`) | single stacked bar + legend | Donut (≤7 slices, "Other" bucket) + sorted hbar with prior-year delta; region view on a **choropleth/bubble map**; segment-over-time stacked area when ≥3 periods. |
| Financial history | table/empty sentence | Small multiples: revenue, gross/operating margin, net income, FCF as bars+line, YoY labels, annual/quarterly toggle, TTM marker; peer-median overlay. |
| Cash conversion & debt | text/empty | Waterfall net income → operating CF → capex → FCF; debt maturity ladder (stacked bars by year, coloured fixed/floating) with cash line overlay; net-debt/EBITDA bullet. |
| Dependencies (customer/supplier concentration) | sentence | Concentration bars (top customers % of revenue) + Sankey suppliers → company → customers, confirmed vs inferred styling; HHI gauge. |
| Country & industry context | text + "64 datasets" | Map/heatmap of exposure weights × macro indicator (GDP, CPI, rates) per country; sparkline per indicator; sector exposure treemap. |
| Macro context | list | Small multiples with exposure-weighted contribution; "what moved" ranking. |
| Trade (UN Comtrade) | none shown | Product × partner-country flow Sankey or ranked bars, trend lines for import/export value, HS-code selector. |
| Input costs & currency | none shown | Indexed price lines for each input commodity/FX vs holding price (rebased 100) + rolling correlation; cost-share bar if disclosed. |
| Correlation (`price-risk-section.tsx:312`) | sorted table of coefficients | Portfolio **correlation heatmap matrix** (clustered, diverging palette, sortable), plus on holding page a diverging hbar of top ± correlated holdings with 252-day rolling-correlation sparkline; diversifier/duplicate callouts. Add a rolling-correlation line vs benchmark. |
| Ownership details (`ownership-section`, 13F + stakes + funds) | 1 bar chart + tables | Holder-type donut/treemap (index funds, active, hedge funds, insiders); top-holder bars with QoQ change arrows; 13F share-count over quarters (stacked area by holder class); **stakes timeline** (13D/G thresholds crossed over time) and a fund-position treemap (fund → shares → % of fund). Reuse for `/investors/[cik]`. |
| Peer comparison | table with degenerate scales | Dot/strip plot per metric: peers as dots, holding highlighted, peer median line; hide metric when fewer than 2 peers. |
| Insider transactions | 105-row table + 1 svg | Timeline (buys up / sells down, sized by value) over price line; net-buying bar by quarter; table paginated. |
| Estimates | table | Estimate-vs-actual beat/miss bars + revision lines (existing `revision-line`, `vintage-fan`). |
| Price-history completeness / split & dividend coverage | 121-row table | Calendar strip (gaps highlighted) – one row per year. |

Also **sweep all other pages for the same pattern** (tables/text where a chart fits) — Phase 0 covers the not-yet-viewed routes; add each hit to this table.

### Per-page application (priority order)
1. **Job log + job detail (H):** run-timeline strip above table (lanes = job_type, x = created_at→duration, colour = status) + status/cost sparkline; step Gantt on detail page (duration, cost).
2. **Watchlist (H):** RangeBar per row from latest quote.
3. **Investors (H):** treemap/hbar of `positions_detail.value_usd`; delta bars `previous_value_usd → reported_value_usd`.
4. **KPI table on holding (M):** BulletChart target vs observed + sparkline of `history`.
5. **Cron / calendar (H):** 24h/weekly schedule timeline from `describeCron`; month calendar heatmap of closures.
6. **Budget (M):** stacked bars or treemap for spend by job type/model; daily burn-up vs cap with projected month-end; per-job bullet (`job_cap_usd` vs spend).
7. **Relationships (M):** network graph replacing list.
8. **Screens (M):** percentile bar per metric cell.
9. **News (M):** source-reliability small multiples; optional Sankey source → severity → holding.
10. **Escalations (M):** open-age ladder + ack-latency histogram.
11. **Transactions (L):** calendar heatmap, per-instrument flow breakdown.
12. **Config history (M):** timeline + DiffRows.
13. **Verification (L):** pass-rate trend line; rule × source heatmap.
14. Review existing visuals for wrong/misleading type during Phase 0 (e.g. donut vs sorted bars for >6 slices, truncated axes).

Backend check before each: confirm the field is in `lib/api/schema.d.ts`; if the series needs a new endpoint, add it to a backend plan item rather than computing client-side.

---

## 7. Sequencing

| Phase | Work | Verify |
|---|---|---|
| 0 | Live audit — done for 22 of ~30 routes (§1b); remaining: strategy/other config editors in detail, research, reviews, status, glossary, chat, evidence, disputed, dark-mode sweep | confirmed bug list |
| 0.5 | IA redesign (§1a): nav.ts model + redirects + tabs scaffolding + Inbox page; decide naming open questions first | every old URL redirects; nav test passes |
| 1 | Foundations F1-F5, F7 | unit tests, typecheck in `apps/web` only |
| 2 | Long-page restructure: holding page, overview, strategy, digest (biggest user pain) | screenshot desktop + mobile per page |
| 3 | Mobile tables: holdings, transactions, watchlist (+F2) | 390px screenshot, no horizontal scroll |
| 4 | Raw-data cleanup: job views, investors, identity, enums, FX | grep for `<pre>`, `toFixed(4)`, `?? "—"` hits drop |
| 5 | Viz wave 1: RunTimeline, RangeBar, investor treemap/delta, bullet KPI | component tests + visual check |
| 6 | Viz wave 2: cron/calendar, budget, network graph, screens, escalations, news | same |
| 7 | Remaining editors (F6 refactor), a11y pass, Lighthouse a11y score check | Lighthouse ≥ 90 a11y on 5 key pages |

Run `tsc` and lint only in `apps/web` and only touched files, once per batch.

## 8. Open questions
- Browser tool for Phase 0 (claude-in-chrome vs Playwright)?
- Tabs as **sub-routes** (shareable, lazy) vs **in-page tabs** for holding/overview? Recommendation: sub-routes via `?tab=` first (lower refactor cost).
- Mobile is a real usage target, or desktop-first with "not broken" on mobile?
