# UX / UI Redesign v2 — whole-app plan

Date: 2026-10-11. Scope: `apps/web` (Next.js 16, Tailwind 4, Ledger tokens, custom SVG chart kit in `components/charts`, Tremor Raw sparklines, recharts in a few places).
Supersedes the *scope* of `ux_ui_dataviz_improvement_plan.md` (2026-10-06, ~30 routes). It keeps that plan's principles and foundations (F1-F8) and re-applies them to the app as it is now: **~110 routes**, up from ~30. Where this plan does not restate something, the old plan still applies.
Companion docs: `Ledger_Design_System_Redesign_Proposal.md`, `Ledger_II_Redesign_Proposal.md` (tokens, motion, confidence chips — **not re-litigated here**).

**Goal (owner, 2026-10-11).** Clean yet interesting. Very simple to use. **No functionality or information removed.** As visual as possible, but every visual has a plain table/text twin. Each visual is the form that best fits the shape of its data.

---

## 1. Evidence

Walked the **deployed app (stewardai.cz)**, logged in, 1440px. **Phase 0 is complete (2026-10-11).**
- *Metrics* (height, tables, rows, SVGs, empty/loading text, overflow) and a *structured digest* (headings, raw-id counts, accessibility gaps, tiny text, console errors) for **every static route (~100)**.
- *Visual review* (screenshots) of ~25 pages, including: overview, holdings, company workbench, holding page, risk, performance contribution, X-Ray, news (light + dark), activity (light + dark), escalations, relationships, disclosures + alerts + coverage, AI decisions, config hub, strategy, event rules, jobs.
- *Mobile* (390px): overflow metrics on 14 routes, screenshots of overview, holdings, risk. *Dark mode*: via the app's own theme toggle (OS-level emulation does not switch it): risk, news, activity.
- **Not walked** (need a real id; do at the start of Phase 1): detail routes `escalations/[id]`, `evidence/[factId]`, `jobs/[id]`, `reports/[id]`, `reviews/[id]`, `research/[id]`, `investors/[cik]`, `funds/[isin]/*`, `news/stories/[id]`, `config/history/[domain]`, and interaction states (menus, tooltips, drawers, forms after submit).
- Regex hits for "error text" are leads, not verified faults; the news/wire/glossary hits turned out to be headline words (false positives). Real error text is listed in §1.4.
- The first dev-server pass was discarded (cold compile made pages look stuck on loading).

### 1.1 What is already good (keep)
Ledger look: blueprint cards with crosshair corners, condensed display headings, mono eyebrow labels, restrained palette, hero + stat tiles on Overview, confidence chips, severity/status dots, news severity timeline, dark mode. The redesign is **a reorganisation and a visual-density upgrade inside this system, not a new look.**

### 1.2 Headline problems (confirmed live)

1. **Information architecture has not kept up.** Four clusters, ~60 menu links. *Research & Verification* holds **25 pages** (theses, journal, three review queues, screens, discover, macro, news, 13F, 12 disclosure pages, glossary). Things waiting on the owner are scattered across Escalations, Review queue, Relationship review, Disputed news, Disclosure alerts, Accuracy checks, Deal parties, AI decisions, Verification.
2. **Two company pages.** `/holdings/[ticker]` (position, price, thesis) and `/companies/[ticker]` ("workbench", 13 tabs: financials, ratios, peers, segments, valuation …) both exist, are linked differently, and neither knows about the other's content. The workbench header shows the raw id (`CIEN_US_EQ`, "no stored close") where the holding page resolves "Ciena".
3. **Sub-pages are not titled.** All of Risk's 11 tabs have h1 "Risk and decisions"; Performance ×5 "Performance"; X-Ray ×8 "X-Ray"; `/reviews` is titled "Reports"; browser tab shows raw id for companies.
4. **Pages are extremely long.** AI decisions **29,421px**, Disclosure alerts **25,622px**, Relationships **15,299px**, Activity **14,458px**, Accuracy checks **13,204px**, Data sources **10,598px**, News **7,900px** (431 SVGs), News stories 8,336px (100 SVGs), Reports 7,180px, X-Ray overlap 5,258px. All unpaginated lists.
5. **Numbers where charts belong.** Risk (all 11 tabs) is label/value rows and "Unknown" chips; only 2 SVGs on `/risk`. Disclosures ownership/insiders/network/deals/estimates/look-through/coverage are mostly tables (look-through 75 rows, 0 charts; screen 65 rows, 0 charts; coverage 0 charts). X-Ray overlap/proposed/inputs/fundamentals: 0 charts.
6. **Empty and loading states dominate.** ~20 routes sit at exactly 900px (one viewport) because they are a spinner, a form with no result yet (`risk/pretrade`, `rebalance`, `themes`, `whatif`, `replay`, `deposit`, `disclosures/replay`) or a one-line empty state. Several still show "Loading…" after 3 s on production (X-Ray ×4, quality, investors, insights, glossary). Company/holding pages show 3-5 spinner cards at once.
7. **Raw ids in the UI.** `CIEN_US_EQ`, `AMD_US_EQ`, `SXRVd_EQ` in headings, alert cards, AI-decision rows and relationship lists; Holdings already shows company names, so the data exists.
8. **Repeated "Unknown" / "Not computable".** Risk shows 4+ "Unknown" chips with the same reason string (`191 observations, minimum is 250`) repeated per row. The reason belongs once, in a banner/legend.
9. **Config hub still duplicates its own sidebar** (12 cards = 12 sidebar links). Sidebar badges inconsistent ("0", "30", "$25", none).
10. **Config editors report unsaved changes on a clean load.** Reproduced on `event-rules`, `news-sources` and `notifications`: the sticky "Unsaved changes. Validate / Preview and save" bar is already showing, and leaving the page raises the browser "leave page?" prompt (which also blocks automation). Dirty baseline is computed before defaults settle. Fix inside `useConfigEditor` (F6) and add a test: "freshly loaded editor is not dirty" for every editor.
11. **Mobile.** Top nav still clipped with a visible scrollbar ("Oversig…"); no drawer/bottom bar. 390px: horizontal document overflow (375/390 → fine at doc level) but **34 overflowing elements on `/disclosures/ownership`**, 1 each on company tabs, Risk, X-Ray. Company workbench and Risk render 13-14 tabs in one row.
12. **Raw provider/API errors shown to the user** (verified): `/disclosures/coverage` prints "Retrieval failed - public search POST HTTP 400" and "ranged GET HTTP 404 (dataset name is a guess; check the SEC…)"; `/data-sources` prints "HTTP 406" per dataset row; Escalations item E-1 prints `escalation call failed: Error code: 400 - {'type': 'error', …"domains are not accessible to our user agent"}` (this one was already in the old plan and **is still there**). Show a plain status ("Source unavailable · retry") with the technical text under Details.

### 1.3 Old-plan findings re-checked
| Old finding | Now |
|---|---|
| Config hub duplicates sidebar | Still true |
| Mobile nav clipped | Still true (confirmed on overview, holdings, risk; tab rows also clip) |
| Activity page very long | Still true (14.4k) |
| Relationships has 5 empty sections ≈ 2,000px | Now **15,299px** (data arrived, still no pagination/graph); holding is still a free-text field `e.g. AAPL_US_EQ` |
| Overview 9 grid rows, spinner cards | Overview 4.8-6.6k px, still long; ring cards on Activity sit on "Loading…" for seconds |
| Escalations raw API error | **Confirmed still present** (E-1) |
| Escalations right column: empty chart boxes | **Confirmed**: "No acknowledgements yet" dashed box and a 12-week axis with one bar |
| Dark mode fine | Confirmed good on risk/news/activity (see §1.4 for the one weakness) |
| Config hub sidebar badges inconsistent | Still true ("0", "30", "3", "51", "$25") |

### 1.4 New findings from Phase 0
**What works (keep and spread):** Activity's **job-run heatmap** (job × day, colour = OK/retried/failed, with legend and last-run column) is the best visual in the app and the pattern to copy for Inbox, Data coverage and Release checks. Performance › Contribution's labelled bar rows ("Prices moved / Dividends / FX / Fees / Not assigned") read instantly. Zero unlabeled form fields and zero missing `alt` on all ~100 routes — the accessibility base is clean. Dark mode is solid.

**Config editors are enormous.** `holding-profiles` **30,833px / 1,261 inputs** (30 holdings × ~40 fields flat); `jobs` **27,947px / 584 inputs** (51 schedules, each with cron + timezone + model fields); `event-rules` 6,009px / 215 inputs (14 severity selects plus per-holding overrides); `news-sources` 6,078px / 144; `strategy` 4,204px / 39; `notifications` 1,893px / 67. These need master-detail (list left, one item's form right), search, and a visual summary on top (cron timeline for Jobs, severity matrix for Event rules, trust bars for News sources).

**Repeated and raw content**
- Risk: the identical 170-character subtitle ("How the portfolio has behaved, what a bad scenario would cost…") appears under all 11 tabs; every tab also reuses "Unknown" chips.
- Raw ids at scale: AI decisions **410**, Disclosure alerts **226**, Portfolio look-through 83, Evidence screen 70, Data coverage 62, Attribution 53, Lots 57, Relationships 50, Activity 48, News 113 (e.g. `NU_US_EQ`, `BRNTl_EQ`). Performance › Contribution's warning lists **25 raw tickers in one sentence**.
- Overview: "No data" in two panels, 39 raw ids, 13 text nodes under 11px.
- Tiny text (<11px): Holdings 66 elements, News 74, Transactions 32, Overview 13 → raise floor to 12px and promote what matters.

**Perceived speed (still on "Loading…" after 5 s on production):** `/xray` and `/xray/benchmark` (blank, no heading), `/disclosures/coverage`, `/quality`, `/investors`, `/insights`; company workbench and holding page both sit on 3-5 spinner cards. Ring cards on Activity ("Prices / Earnings checks / Identity") load separately from the page.

**Chart-type misfits spotted**
- Activity budget uses a **speedometer gauge** for spend vs cap → replace with a burn-up line (actual vs linear cap, projected month-end) plus a bullet.
- News severity timeline encodes Alert / Highlight / Digest as three **blues of different lightness**; in dark mode Digest and Ignored nearly disappear. Use hue-distinct + pattern/shape (and keep the ordinal blue only for confidence).
- Escalations "per week" bars: one bar on a 12-week axis; ack-latency chart is an empty dashed box → show only when ≥3 data points, otherwise a single sentence.
- Performance › Contribution is a good bar view but not sorted by magnitude and the "Not assigned" bar uses the same colour as real contributions → grey/striped for unattributed.
- Risk rows are pure text; the single drawdown number has no curve.

**Other**
- Command palette groups pages under different clusters than the nav ("Fund research" is listed under Portfolio, nav puts it in Research). One source of truth in `lib/nav.ts`.
- Event rules (14 identical selects) is a natural **severity matrix** (event type × 0-3 as a segmented control / heat strip), far shorter and scannable.
- Jobs: each schedule shows cron text + a plain-English line ("At 22:30 every day") — good; add a week timeline above and collapse to a one-line summary per schedule.
- Public disclosures landing: shows a "Loading issuers…" spinner above 6 section anchors; sections are cards with only descriptions until an issuer is chosen — the page should open on a portfolio-wide summary (alerts by company + coverage matrix), with issuer picking as a filter.

---

## 2. Design principles (v2)

Inherited from the old plan: summary first, progressive disclosure, no raw data in UI, one primitive per job, mobile not broken.
Added by the owner brief:

| # | Principle | Concrete rule |
|---|---|---|
| P1 | **One answer per page** | Every page opens with an *answer strip* (3-4 numbers/status that answer the page's question) and **one hero visual**. Everything else is below, in tabs or collapsed. |
| P2 | **Simple to use** | ≤7 top-level destinations; ≤7 tabs per page (overflow → "More"); one primary action per page; every empty state says what to do next. |
| P3 | **Nothing lost** | Every route, field and action is mapped in §5 (coverage matrix). Old URLs redirect. Collapsing is allowed, deleting is not. |
| P4 | **Visual first, text always** | Every chart has a **Chart ⇄ Table** switch over the same rows (`<ViewToggle>`), the table is also the screen-reader/print/export view, and the choice is remembered per page. |
| P5 | **Right chart for the data** | Chart is chosen from the data's *job* (table below), not from what exists in the kit. Misfit charts are replaced (§6.3). |
| P6 | **Show what is missing, once** | Empty/unknown is a *coverage strip* ("4 of 9 inputs ready · run X"), not a card per gap. Reason strings appear once per page. |
| P7 | **Names, not ids** | Company/fund name + symbol chip everywhere; raw ids only in a copy-able "Details" row. |
| P8 | **Fast to feel** | Skeletons with the final aspect ratio; ≤1 batched summary request per page; show last-known values while refreshing; lists paginate or virtualise at >50 rows. |
| P9 | **Calm density** | One focal visual, then quiet tables. Max 2 accent colours per view (+ confidence hues where data is evidence). Matches Ledger tokens; no new palette. |

### 2.1 Chart choice by data job (applies to every page)

| Data job | Use | Avoid |
|---|---|---|
| Value over time | line / area (flow-adjusted where money moves); sparkline in tiles | donut/bars for time |
| Change over time vs a benchmark | line pair + shaded spread; drawdown as underwater area | dual y-axes |
| Part-to-whole, ≤6 parts | stacked bar (single row) or donut | pie with >6 slices |
| Part-to-whole, many parts / hierarchy | treemap (sector → holding), sunburst only for 2-level | donut with "Other 40%" |
| Ranking / comparison | sorted horizontal bar; dumbbell for before→after | vertical bars with long labels |
| Positive/negative contribution | waterfall or diverging bars (shared zero line) | stacked bars crossing zero |
| Value vs target/limit/band | bullet chart / range bar | gauge/speedometer |
| Distribution | histogram, box/strip, CDF; empirical tail marked at VaR/ES | mean-only |
| Relationship between two measures | scatter with labelled outliers | table of coefficients |
| Many-to-many (correlation, overlap) | heat matrix, clustered, diverging palette | table of pairs |
| Flow / supply chain / money movement | Sankey (≤30 nodes), else ranked bars | node-link hairball |
| Network (≤60 nodes) | force/radial graph, edge style = confirmed vs suggested | list of 21 rows |
| Events in time | timeline / lollipop, Gantt for durations | table sorted by date |
| Calendar patterns | calendar heatmap | long table |
| Geography | choropleth/bubble map *only if* location is the point; else ranked bars | map for 3 countries |
| Status across items | status matrix (issuer × dataset), coloured + labelled (never colour alone) | one card per item |
| Estimate/forecast uncertainty | fan / range band with the point estimate | single number |
| Ordered confidence | ordinal stacked bar (existing `ConfidenceBar`) | rainbow |

---

## 3. Information architecture v2

### 3.1 Top level (replaces 4 clusters / 60 links)
Six labels + Settings gear. Each label opens a **single page with tabs**, not a mega-menu; the command palette (⌘K) still reaches any page.

| Label | Question | Contains |
|---|---|---|
| **Portfolio** (home `/`) | What do I own and how is it doing? | Overview · Holdings · Performance · Allocation (today's X-Ray) · Risk |
| **Companies** | Tell me about one company/fund | Single company page (merges holding page + workbench) · Funds |
| **Research** | Is my thesis still true? What should I look at? | Theses · Research reports · Discover & Screens · Journal · What changed |
| **Markets** | What is happening outside? | News · Macro · Filings (public disclosures) · 13F investors |
| **Briefings** | What did I miss? | Daily/weekly reports · Monthly reviews |
| **Inbox** (count badge) | What needs a decision from me? | All review/decision queues (§3.4) |
| **System** | Can I trust it, what does it cost? | Activity · Data sources · Data quality · Verification · Release checks · API status · AI decisions (audit view) |
| ⚙ **Settings** | How should it behave? | Config (grouped sidebar) |

Desktop: Portfolio · Companies · Research · Markets · Briefings · Inbox · System · ⚙ (7 labels + gear; decisions in §10).
Mobile: bottom bar **Portfolio · Research · Markets · Inbox · More** (Companies via search/tap-through, Briefings/System/Settings under More).
Glossary → "?" help menu + inline term tooltips. Chat stays the global slide-over (Ctrl+J / ⌘K).

### 3.2 Page grammar (every page)
```
eyebrow (Area)  ·  H1 = this tab's name  ·  one-line purpose            [primary action]
[ Tabs (≤7, URL-addressable sub-routes) ]
[ Answer strip: 3-4 numbers / statuses ]  [ Coverage strip if inputs missing ]
[ Hero visual (Chart ⇄ Table) ]
[ Secondary panels in a 2-column grid, content-height, no stretched empty cards ]
[ Detail tables below, paginated; "Details / Raw" collapsed ]
```
Fixes in passing: per-tab `<title>`/h1, breadcrumbs, sticky right-rail TOC (F1) only on genuinely long pages, `items-start` grids so cards never stretch to the tallest neighbour.

### 3.3 One company page
`/companies/[ticker]` (old `/holdings/[ticker]` and `/companies/[ticker]/*` redirect here). Header: name + symbol chip, price, position summary, thesis verdict, **ticker switcher**, Ask/Research actions. Tabs:
**Overview** (price, position, thesis, next event, key KPIs) · **Thesis & evidence** (case, assessment, facts, KPIs, news) · **Financials** (statements, ratios, segments, estimates, capital allocation, valuation) · **Peers** · **Ownership & governance** (13F, stakes, insiders, governance) · **Network** (customers/suppliers/patents/network) · **Report** · **Reference data** (identity, listings, price completeness, corporate actions). Funds keep `/funds/[isin]` with the same header pattern (Overview · Holdings · Performance · Style · Bond · Checklist).
*Tab visibility rule:* a tab with no data shows a muted dot and opens to a coverage card, but is never hidden (P3).

### 3.4 Inbox (replaces 8 queues)
One page, filter chips with counts: **Escalations · Figures to check · Relationships · Disputed news · Disclosure alerts · Accuracy checks · Deal parties · Verification flags · AI decisions to veto**. Each item is actionable inline (confirm / reject / snooze / open evidence). Empty chips collapse into one line. Replaces the full-page empty states of Review queue, Relationship review, Disputed, Deal parties. AI decisions *history* stays under System (audit); *rejectable* items surface in Inbox.
Alerts are **grouped by company** with a severity strip and "mark all read", with the list virtualised (25,622px → one screen of groups).

### 3.5 Settings
Grouped sidebar (kills the 12-card hub): *Investment rules* (Strategy, Theses, Holding profiles, Event rules) · *Sources & data* (News sources, Calendar, Research profiles, Verification) · *Automation* (Jobs, Budget, Prompts, Notifications) · *History & export*. `/config` → Strategy. Each editor: summary header, sticky SaveBar, **fix false dirty-state (clean load must not be dirty)**. Budget gets a read view under System → Data sources & cost.

**Editor pattern (Phase 0: editors are 2-31k px tall):** every large editor becomes **master-detail**: searchable list on the left with a one-line status per row (enabled, cron in words, last result), one item's form on the right (full-screen sheet on mobile). A **summary visual sits above the list**, and edits update it live:
| Editor | Today | v2 |
|---|---|---|
| Holding profiles | 30 holdings flat, 1,261 inputs, 30.8k px | list of holdings (name chip + completeness ring) → profile form in detail pane; "needs attention" filter |
| Jobs | 51 schedules, 584 inputs, 27.9k px | **week timeline** of all schedules on top (from `describeCron`), list with enabled toggle + plain-English cron; detail pane: cron, timezone, model, caps |
| Event rules | 14 selects + overrides, 6k px | **severity matrix** (event type × Ignore/Digest/Highlight/Alert segmented control, heat-coloured), overrides in a collapsible table |
| News sources | 144 inputs | table with trust bar, last-fetch dot, enabled switch; row-expand editor |
| Strategy | 7 sections, 39 inputs | tabs *Targets · Rules · Limits*, live "sums to 100%" donut + drift preview in a sticky side summary |
| Notifications / Verification / Research profiles | long forms | grouped accordions with one-line summaries; "Send test alert" as a primary action |
Every editor keeps: versioned save with note, Validate, Preview-and-save (event rules replay), history link — nothing removed.

### 3.6 Mobile spec (first-class target, 390px baseline, also checked at 360 and 768)
- **Shell:** bottom bar (Portfolio · Research · Markets · Inbox · More), 44px touch targets, Inbox badge; top bar reduces to logo, search, Ask Steward, sync dot. Remove the horizontally scrolling nav. "More" sheet holds Companies search, Briefings, System, Settings, theme, sign out.
- **Tabs:** page tabs become a horizontally scrollable chip row with edge fade and the active tab scrolled into view; more than 5 tabs collapse the rest into a "More" chip. Never 13 tabs in one row (Risk, workbench today).
- **Answer strip:** 2×2 tiles on mobile; hero visual full-bleed, height ≤ 55% of viewport; secondary panels stack in one column.
- **Tables:** `<ResponsiveTable>` renders a card list under `md`: primary value + name on the first line, 2-3 key fields, "Details" expander for the rest; sort/filter in a bottom sheet. Wide matrices (overlap heat matrix, status matrix) get a pinned first column and pinch-free horizontal scroll *inside the card only*, with a "Switch to list" fallback.
- **Charts:** touch tooltips (tap to pin, tap away to dismiss), no hover-only information, labels thinned by width, legends become tappable chips that filter; Chart ⇄ Table toggle is a pill directly above each chart. Force graph and Sankey show a ranked-list view by default on mobile with "Open diagram" for full-screen landscape.
- **Forms/editors (Settings, What-if, Pre-trade):** single column, sticky SaveBar above the bottom bar, number inputs use numeric keypads, date inputs native.
- **Inbox on mobile:** swipe actions (confirm / reject / snooze) with visible buttons as fallback; one item per screen in "review mode".
- **Performance budget:** skeletons, route-level code splitting, charts below the fold lazy-mounted, no list over 50 rows; target LCP < 2.5 s on mid-range phone over 4G.
- **Acceptance:** no horizontal document scroll and zero overflowing elements on all routes at 360/390; every action reachable with one thumb; Lighthouse mobile a11y ≥ 90 on 5 key pages. Add a Playwright smoke test that visits every route at 390px and asserts `scrollWidth <= innerWidth`.

---

## 4. Shared foundations (do first)

Old F1-F8 stand (SectionNav, ResponsiveTable, formatters/NullCell, Panel/Heading/Loading/JsonDetail, label maps, `useConfigEditor`, ResponsiveNav, Select/FilterChips). Additions:

| # | Component | Purpose |
|---|---|---|
| F9 | `<ViewToggle>` + `<ChartOrTable>` | Chart ⇄ Table over one `rows` prop; remembers choice (try/catch localStorage); table doubles as a11y/print/CSV export |
| F10 | `<AnswerStrip>` | 3-4 stat tiles with sparkline, delta, tone; used by every page |
| F11 | `<CoverageStrip>` | "n of m inputs ready", lists missing, links to the job that fills them; replaces per-card empty states |
| F12 | `<EntityChip>` | name + symbol chip, resolves ids → names, copy-id on hover; used in all lists |
| F13 | `<PageTabs>` + route-title helper | URL sub-routes, per-tab h1/title, overflow "More" menu, badge counts |
| F14 | `<GroupedList>` (virtualised) | day/ticker group headers, collapse, "n similar" grouping (news 8-Ks, alerts), pagination |
| F15 | `<ReasonOnce>` | page-level legend for repeated "Unknown / minimum is 250" reasons |
| F16 | `<BottomBar>` / drawer nav | mobile shell (extends F7) |
| F17 | `<Skeleton aspect>` + batched page-summary hook | P8 |

New chart components (add to the old list: Bullet, RangeBar, RunTimeline/Gantt, CalendarHeatmap, Slope/Dumbbell, ForceGraph, PercentileBar, Histogram/Box, DiffRows):
`HeatMatrix` (clustered, diverging) · `Underwater` (drawdown area) · `Tornado` (scenario/factor impact) · `Lollipop timeline` (insiders, deals, alerts) · `StatusMatrix` (issuer × dataset) · `CDF/Lorenz` (concentration) · `FanBand` (estimates/forecast) · `Sunburst-lite` (look-through, only 2 levels).
All built with `lib/palette`, `components/charts/shared.tsx` tooltip/axis rules, and the **dataviz skill** when implementing.

---

## 5. Coverage matrix — every route has a home

*(Legend: ▣ = hero visual, ☷ = table twin always available. "→" = new location. All old URLs get redirects.)*

### Portfolio
| Today | → v2 | Hero visual (right type) | Notes |
|---|---|---|---|
| `/` Overview (6.6k px) | Portfolio › **Overview** | ▣ Portfolio vs benchmark line (existing); answer strip: value · P/L · cash · Sharpe · drift status | Move Risk, Heatmap, Concentration, Cash flow, Exposure, Contribution, Valuation to Overview sub-tabs *Performance · Allocation · Risk · Cash & flows · Data quality*; merge Drift + Weights-vs-targets; names not tickers |
| `/holdings` | Portfolio › **Holdings** tabs: Positions · Watchlist · Transactions · Funds & ETFs | ▣ Treemap (size = value, colour = P/L) beside table; Watchlist ▣ RangeBar (buy ← price → sell) | Card list < md; mute 30× "NO TARGET"; sort/filter/search; row-expand editor for watchlist |
| `/transactions` | Holdings › Transactions | ▣ Calendar heatmap of flows + net-flow bars | Pagination |
| `/watchlist` | Holdings › Watchlist | RangeBar per row | Replaces per-row form grid |
| `/funds`, `/funds/compare` | Holdings › Funds & ETFs; Compare as a mode | ▣ Cost/return scatter; compare = small multiples + dumbbell | |
| `/performance` (+4) | Portfolio › **Performance**: Overview · Attribution · Core vs stocks · Ledger · Lots | Overview ▣ line + drawdown underwater; Attribution ▣ **waterfall** (+ Brinson bars); Core vs stocks ▣ line pair + spread area; Ledger ▣ cash-flow bars; Lots ▣ age ladder + inline gain bar | Distinct h1 per tab |
| `/xray` (+7) | Portfolio › **Allocation**: Exposure · Overlap · Concentration · Fundamentals · Benchmark · Supply chain · What-if · Your data | Exposure ▣ treemap + sector/country ranked bars (+ map only if ≥6 countries); Overlap ▣ **HeatMatrix** (fund×fund) + shared-holdings bars; Concentration ▣ Lorenz/CDF + HHI bullet; Fundamentals ▣ strip plot vs benchmark; Benchmark ▣ diverging active-weight bars; Supply chain ▣ Sankey | As-of date picker stays (global control in the header strip); `xray/proposed` and `xray/inputs` become the *What-if* and *Your data* tabs |
| `/risk` (+10) | Portfolio › **Risk**: Overview · Today · Bad months · Past episodes · Factors · Stress · Themes · **Decide** (What-if · Rebalance · Pre-trade · Deposit) · Does it hold up | see §6.1 | One banner for sample-size caveat (F15); 13 tabs → 7 + grouped "Decide" |

### Companies
| `/holdings/[t]` + `/companies/[t]/*` (13) | Companies › **[Company]** (one page, tabs per §3.3) | Overview ▣ price line with events; Financials ▣ small multiples (revenue, margins, FCF), cash→FCF waterfall, debt ladder; Segments ▣ donut ≤7 + hbar delta; Peers ▣ strip plot with median line (hide metric if <2 peers); Network ▣ force graph; Ownership ▣ holder-class stacked area + stakes timeline | Ticker switcher; resolve names; lazy tabs; paginate 431-row identity table |
| `/funds/[isin]/*` (6) | Companies › Fund page | Holdings ▣ treemap; Performance ▣ line + calendar returns; Style ▣ style box; Bond ▣ maturity ladder + duration bullet; Checklist ▣ status list | |

### Research
| `/theses` | Research › **Theses** | ▣ Board grouped by verdict, `ConfidenceBar` per holding, KPI bullet vs target | |
| `/research`, `/research/[id]` | Research › Reports | TOC rail; title + short id chip | |
| `/screens`, `/discover`, `/discover/backtest` | Research › **Discover**: Screens · Discover · Backtest | ▣ Percentile bars per metric; backtest ▣ equity line vs benchmark + hit-rate bars | |
| `/journal`, `/journal/scorecard` | Research › Journal / Scorecard | ▣ Calibration dots (confidence vs outcome) | |
| `/insights`, `/insights/sectors` | Research › What changed | ▣ Diverging bars of change by sector; timeline | |
| `/funds/research` | Research › Fund research | Table + inline sparkline | |

### Markets
| `/news` (+9) | Markets › **News**: Feed · Stories · Wire · Brief · Ask · Sources · Disputed · Review(ops) | Feed ▣ severity timeline (keep) + source-reliability small multiples; default hides *Ignored*; group "34 8-Ks mention tariffs"; day headers; virtualise (431 SVGs → <60) | Disputed also a filter in Inbox |
| `/macro` | Markets › Macro | ▣ Small multiples **with axes, dates, units**; drop repeated "Not tracked" rows; geography select | |
| `/disclosures` (+12) | Markets › **Filings**: Timeline · Ownership · Insiders · Network · Deals · Estimates · Screen · Look-through · Coverage · Replay | Ownership ▣ stacked area by holder class + top-holder bars with QoQ arrows; Insiders ▣ lollipop timeline over price + net-buy bars; Network ▣ Sankey/radial; Deals ▣ timeline; Estimates ▣ beat/miss bars + revision lines; Look-through ▣ treemap; Coverage ▣ **StatusMatrix** | `alerts`, `adjudication`, `deals/parties` → Inbox; Replay keeps known-at scrubber |
| `/investors`, `/investors/[cik]` | Markets › 13F investors | ▣ Treemap of positions; delta bars previous→reported | |

### Briefings
| `/reports`, `/reports/[id]`, `/reviews`, `/reviews/[id]` | Briefings › Reports / Reviews | TOC rail; collapse low-priority sections; **Sources** rail grouped by domain with counts, capped height | `/reviews` retitled |

### Inbox / System / Settings
| `/escalations(/id)`, `/review`, `/relationships`, `/news/disputed`, `/disclosures/{alerts,adjudication,deals/parties}`, `/verification` flags | **Inbox** | ▣ Count strip by queue; age ladder; ack-latency histogram | Relationships ▣ force graph, holding picker (select, default first) |
| `/activity`, `/jobs/[id]` | System › Activity | ▣ **Run timeline/Gantt** (lane = job type); job detail ▣ step Gantt (duration, cost) | Label maps, no raw JSON, paginate (14k → 1 screen) |
| `/data-sources` | System › Data sources & cost | ▣ Coverage StatusMatrix + budget burn-up vs cap | 57-row table paginated; budget moved in |
| `/quality`, `/verification`, `/release(/audit,/pilot)`, `/status` | System › Quality · Verification · Release · Status | Pass-rate trend line; rule×source heatmap; release checks as checklist matrix | |
| `/ai-decisions` | System › AI decisions (audit) | ▣ Stacked bars by type/outcome + confidence histogram; list virtualised, filter "confirmed / rejected / disagree" | 29k px → one screen |
| `/config/*` (13) | Settings (master-detail editors, §3.5) | Budget ▣ stacked bars/treemap by job & model + burn-up; Jobs ▣ week timeline; Event rules ▣ severity matrix; News sources ▣ trust bars; Holding profiles ▣ completeness rings; History ▣ timeline + DiffRows | 30.8k/27.9k px editors → ≤3 screens |
| `/chat`, `/glossary`, `/evidence/[id]`, login/mfa | unchanged (chat slide-over, help menu, evidence page, auth) | | |

---

## 6. Data visualisation plan

### 6.1 Risk (11 tabs, today almost all text)
| Tab | Question | Visual |
|---|---|---|
| Overview | How risky am I? | Answer strip (vol, max drawdown, VaR/ES status, Sharpe) + **Underwater** drawdown ▣ + return-vs-risk scatter of holdings; sample-size caveat once |
| Today's risk | What moves me today? | Contribution-to-risk hbar (sorted, names) with weight overlay |
| Bad months | How bad can a month be? | Histogram of monthly returns with worst 5% shaded + calendar strip |
| Past episodes | How did I do in past crashes? | Timeline of episodes, loss bars (portfolio vs benchmark) |
| Factors | What am I really betting on? | Diverging hbar of factor loadings + R² ring; small rolling-loading lines |
| Stress test | What if X? | **Tornado/waterfall** of scenario P&L by holding; scenario picker |
| Themes | Hidden clusters? | Treemap by theme; **HeatMatrix** of correlations (clustered) |
| Decide: What-if / Rebalance / Pre-trade / Deposit | What would this trade do? | **Dumbbell** before→after weights, risk deltas as bullets; trade list table; empty form shows an example result, not a blank page |
| Does it hold up | Is the model calibrated? | VaR-breach strip plot + hit-rate vs expected bullet |
Show "Unknown" once via F15 with the reason and what unlocks it ("needs 250 observations; have 191").

### 6.2 Holding/company visuals (from old plan §6, kept)
Business network (force graph, dedupe counterparties), Revenue mix (donut ≤7 + hbar delta + region map/bubble), Financial history (small multiples + peer-median), Cash & debt (waterfall + maturity ladder), Dependencies (Sankey), Country/industry (ranked bars/map), Trade (Sankey/ranked bars), Input costs (rebased-100 lines + rolling correlation), Correlation (heat matrix + diverging bars), Ownership (holder-class area + stakes timeline), Peers (strip plots, hide <2 peers), Insider trades (lollipop over price), Estimates (beat/miss + revisions), Price completeness (calendar strip). All with Chart ⇄ Table. **Add a coverage check first**: many of these panels are empty for most holdings, so fix data jobs per `empty_data_fill_plan.md` before polishing.

### 6.3 Existing visuals to review for fit
Donuts with >6 slices → treemap/sorted bars; Overview "Cash" drift card (one row) → merge; monthly heatmap with a single row → calendar strip; Peers "Position" mini-scale for one value → hide; macro small multiples without axes → add axes/units; "Unknown 100%" filled bar for 0% mapped → striped "unmapped" pattern + text.
Added by Phase 0 (live): Activity **speedometer gauge** → burn-up line + bullet; News severity as **three blues** → hue + pattern; Escalations empty/one-bar charts → hide below 3 points; Contribution "Not assigned" bar same colour as real contributions → grey/striped and sort by magnitude; Overview/Activity **ring gauges** for coverage % → keep only as small status rings next to a count ("27 of 30"), never the sole encoding.
**Spread the winner:** the Activity job-run heatmap pattern (row = item, column = day, colour = status, legend, last value at row end) becomes the shared `StatusMatrix` and is reused for Inbox ageing, Data coverage (issuer × dataset), Release checks (check × week) and Verification (rule × source).

---

## 7. Page-by-page quick wins (before the big IA move)
Independent of the restructure, can ship first:
1. Per-tab titles/h1 for Risk, Performance, X-Ray, Reviews; company name instead of raw id in `<title>`, breadcrumbs and headings.
2. Fix false "leave page?" prompt in config editors.
3. Resolve ids → names in alert cards, AI-decision rows, relationship lists, workbench header.
4. Paginate/virtualise: AI decisions, Disclosure alerts, Relationships, Activity, Accuracy checks, Data sources, News stories/wire.
5. News: hide *Ignored* by default, group repeated 8-K keyword hits, day headers.
6. Risk: show sample-size reason once, not per row.
7. Skeletons with fixed aspect ratios; drop duplicate cards that stretch (`items-start`).
8. Mobile: drawer/bottom bar, tab overflow menu, card layout for tables, fix the 34 overflowing elements on ownership.
9. Config hub → redirect to Strategy (sidebar already is the hub).
10. Fix false-dirty editors (`event-rules`, `news-sources`, `notifications`) and add a "clean load is not dirty" test per editor.
11. Replace raw provider/API errors with plain status + collapsed Details: Escalations E-1, Data coverage, Data sources ("HTTP 406").
12. Show the Risk sample-size caveat once; give each Risk/Performance/X-Ray tab its own subtitle (today the same 170-char line sits under all 11 Risk tabs).
13. Collapse the 25-ticker warning on Contribution into "25 holdings couldn't be attributed (expand)", names not ids.
14. Raise the text floor to 12px (Holdings 66, News 74, Transactions 32, Overview 13 sub-11px nodes).
15. Hide empty/one-point chart boxes on Escalations; replace the Activity budget gauge with a burn-up line; make news severity colours distinguishable in dark mode.
16. Relationships: holding selector as a searchable select defaulting to the first holding; paginate (15k px).
17. Command palette: take cluster labels from `lib/nav.ts` so palette and nav agree.

---

## 8. Sequencing

| Phase | Work | Verify |
|---|---|---|
| 0 | **Done 2026-10-11** — digest of every static route, ~25 visual reviews, dark mode and mobile spot-checks; results in §1 | §1.4 |
| 0b | Walk the dynamic detail routes (`escalations/[id]`, `evidence`, `jobs/[id]`, `reports/[id]`, `reviews/[id]`, `research/[id]`, `investors/[cik]`, `funds/[isin]/*`, `news/stories/[id]`, `config/history/[domain]`) and interaction states (menus, drawers, form submits, empty→filled); add findings to §1.4 | one screenshot per route-state |
| 1 | §7 quick wins (now 17 items) | before/after heights; no raw ids; mobile 390 no horizontal scroll; "clean load not dirty" tests green |
| 2 | Foundations F1-F17 + chart components (unit tests for formatters/labels/ViewToggle) | `tsc` in `apps/web`, touched-file lint, vitest for new components |
| 3 | IA v2: `lib/nav.ts` model (+tabs, badges, `REDIRECTS`), Inbox page, Settings sidebar, Companies merge, mobile bottom bar | every old URL redirects (test that every nav/redirect target resolves); nav tests pass |
| 4 | Page restructure by area, in this order: Overview → Holdings → Company → Risk → Allocation → Performance | desktop + mobile screenshots; answer strip + hero visual on each |
| 5 | Markets & Filings, Research, Briefings | same |
| 6 | System & Settings (master-detail editors per §3.5, StatusMatrix reuse), viz wave 2 (Gantt, week timeline, budget burn-up, graph) | editors ≤ 3 screens; same screenshots |
| 7 | A11y (every chart has table twin + aria-label; no colour-only status), perf (LCP, requests per page), Lighthouse a11y ≥ 90 on 5 key pages | numbers recorded |

Ship in two steps for IA: (a) tabs + redirects with the old nav intact, (b) switch nav once every page is reachable. Implementation of each UI step: load the `frontend-design` skill (stay inside Ledger tokens) and the `dataviz` skill for any chart work. Run `tsc`/lint only in `apps/web`, once per batch.

## 9. Success criteria
- Longest page ≤ 3 screens (~2,700px) at 1440px, **including Settings editors** (today 30.8k px); no list renders >50 rows unpaginated; no editor shows >60 inputs at once.
- Every editor loads clean (not dirty) and leaving it without edits never prompts.
- No raw HTTP/provider error text outside a collapsed Details row.
- Every chart has a table view; every table page with numeric columns has at least one visual (or a stated reason it doesn't).
- Zero raw entity ids in headings, titles, list rows (outside Details).
- ≤7 top-level destinations; any page reachable in ≤2 clicks (or ⌘K).
- 390px: no horizontal page scroll; no clipped nav.
- Coverage matrix (§5) checked off route by route: no route, field or action lost.
- No "Loading…" spinner visible >1 s without a skeleton; one batched summary request per page.

## 10. Decisions (owner delegated all calls, 2026-10-11)
1. **Briefings is its own top-level label.** It serves the distinct "what did I miss" habit, and folding it into Research would push Research past 7 tabs.
2. **Name: "Inbox"** (with count badge). Short, familiar, and fits the mobile bottom bar.
3. **Merge holding page and workbench into one Companies page.** Two pages for one entity is the biggest source of confusion; old URLs redirect, nothing is dropped.
4. **Mobile is a real usage target:** bottom bar, card-style tables, no horizontal page scroll.
5. **AI-decision vetoes live in Inbox;** the full decision history stays under System as the audit view.
6. **Filings stays under Markets** (it is external public-record data); alerts, accuracy checks and deal-party review move to Inbox.
7. **Settings stays a gear icon, not a menu label,** with the grouped sidebar.
8. **IA ships in two steps** (tabs + redirects first, nav switch second) so nothing is unreachable at any point.

No open questions remain; Phase 0 (visual audit of the remaining routes) is the next step and may refine page-level details, not these decisions.
