# UX / UI final redesign — implementation plan

Date: 2026-10-16. Scope: all of `apps/web` (147 routes). Status: **plan, nothing implemented; all decisions made (§10); implementation tasks in §8.**

This is the one plan to build the redesign from. It folds together:
- `ux_ui_redesign_v2_plan.md` (2026-10-11, IA + principles P1-P15 + foundations F1-F32). **v2 was never implemented**; its decisions (§10 there) stand unless this plan changes them, and its Phase 0 audit (§1 there) is still valid where not re-measured below.
- The Simply Wall St visual language: `docs/research/simplywallst_visualization_catalog.md` and `simplywallst_feature_and_analysis_review.md`.
- What shipped since v2 was written: the **insight layer** (`aecd5c4`): `components/insight/*` (ScoreMark, CheckList, DotStrip, BandGauge, MonthCalendar, ForecastBars, StepLine, ChartFooter, SideDrawer, GroupedTable, CalcChain, SliderRange, VerdictLine, AnswerStrip, ScorecardPanel, StripsPanel, UpdatesFeed), the pages `/portfolio/income`, `/portfolio/valuation`, company tabs `checks`, `dividends`, `consensus`, `allocation`, holdings presets, `/config/checks`; plus `/inbox`, `/autopilot`, `/engine-ledger`, `/thesis` (workup), `/methods`. None of these were part of the v2 audit.

**Companion: `ux_ui_visual_spec.md`** holds exact visual detail (token values, component/chart spec sheets, per-page specs, formulas and sentence templates). This plan owns scope and order; the spec owns how it looks.

Backend/data work for insight features stays in `insight_features_implementation_plan.md` (I-phases). This plan only owns the UI, plus a list of **data defects the UI exposes** (§7) that must be fixed for the visuals to be honest.

**Design direction (owner, 2026-10-16): the Simply Wall St design language is the house style for the *entire* app**, not only for the screens that resemble Simply Wall St. That means its visual system (theme, type, cards, colour semantics, KPI style, chart furniture, navigation, density), its page anatomy and its interaction patterns apply to every page, including pages it has no equivalent for (Activity, Settings, Filings, Inbox, Jobs, Release checks). §2A defines the system, §2B the rules, §5.0 how each page type is translated, §5.14 maps every catalog item to a slot. What is *not* copied: name, logo, brand colour, the word "Snowflake", wording, proprietary scoring, community features, paywalls and pop-ups. The Ledger tokens are re-skinned to this system (§2A, phase R1a), not kept as-is. Load `frontend-design` and `dataviz` skills when implementing any step.

---

## 1. Evidence (Playwright walk, 2026-10-16, production, logged in)

**Method.** Every static route plus one real id for every dynamic route (147 routes) was loaded at 1440×900 and measured after 5 s: page height, tables/rows, SVGs, inputs, raw-id count (`*_EQ` pattern), "Loading", empty/unknown markers, error words, `<pre>` JSON, false-dirty editors, headings. ~25 pages were reviewed by screenshot (overview, holdings + Check-in preset, income, valuation, inbox, autopilot, engine ledger, company overview/checks/valuation/dividends, holding page, risk, X-Ray fundamentals, discover). 16 routes were measured at 390×844 for overflow, plus two mobile screenshots. Dark mode for the new insight pages was **not** verified (theme toggle not reachable by automation); Phase 6 covers it.

### 1.1 Route metrics (worst first; H = page height at 1440 px)

| Route | H px | Notable |
|---|---|---|
| `/holdings/[t]` (AAPL) | **50,102** (68k mobile) | 21 tables / 866 rows, **525 raw ids, 531 empty markers**; page-level horizontal scroll on mobile |
| `/config/jobs` | 32,096 | 652 inputs, 51 schedules flat |
| `/config/holding-profiles` | 30,833 | 1,273 inputs |
| `/disclosures/alerts` | 25,367 | 225 raw ids |
| `/activity` | 17,087 | 460 SVGs, 81 raw ids, 52 "failed" words |
| `/relationships` | 15,496 | 106 inputs, 51 raw ids |
| `/disclosures` | 14,638 | 300 SVGs, 13 error words |
| `/disclosures/adjudication` | 13,204 | 108 inputs |
| `/reports`, `/reports/[id]` | 12,886 | 528 SVGs; h3s print name+raw id glued ("AppleAAPL_US_EQ") |
| `/autopilot` | 12,091 | **78 near-identical cards** ("Write down why you bought SXRVd_EQ") |
| `/research/[id]` | 11,397 | 82 raw ids |
| `/data-sources` | 10,778 | 34 error words (HTTP 406 rows) |
| `/config/research-profiles` | 9,822 | 281 inputs |
| `/xray/supply-chain` | 9,354 | |
| `/` Overview | 8,785 (15k mobile) | 23 panels, 77 raw ids, 23 tiny-text nodes |
| `/news` | 7,385 | 424 SVGs, 74 tiny-text nodes, 110 raw ids |
| `/news/stories`, `/news/wire` | 8,336 / 7,362 | ~100 SVGs each |
| `/insights` | 6,885 | **132 raw ids** |
| `/release` | 6,438 | 17 tables |
| `/ai-decisions` | 6,369 | 75 raw ids (was 29k in v2 audit; partly fixed) |
| `/config/prompts` | 6,219 | **33 `<pre>` blocks** |
| `/config/event-rules`, `/config/news-sources` | 6,009 / 6,078 | **load dirty** |
| `/engine-ledger` | 5,703 | settings inputs inside a report page |
| `/portfolio/valuation` | 5,552 | 69 empty markers, 25 raw ids in one banner |
| `/funds` | 5,349 | 34 empty markers |
| `/xray/overlap` | 5,258 | 0 charts |
| `/quality` | 5,278 | 33 inputs, 13 error words |

Short/empty-looking (≤ 1 viewport): `/theses` ("No theses configured"), `/watchlist` (empty), `/thesis`, `/risk/{replay,themes,whatif,rebalance,pretrade,deposit}`, `/disclosures/{replay,deals/parties}`, `/news/disputed`, `/investors`, `/funds/compare`, `/funds/[isin]` overview, `/evidence/[id]`, `/jobs/[id]`, `/news/stories/[id]`, `/config/history`.

**Titles.** Company and fund pages still use the raw id as `<title>` (`AAPL_US_EQ · StewardAI`, `IE00B5BMR087 · StewardAI`). All 12 Risk tabs: h1 "Risk and decisions" + the same subtitle. Performance ×5, X-Ray ×9: one h1 each. `/reviews` h1 = "Reports". `/methods` and `/methods/[slug]` **render Overview on production** (the "Learn" links in every `ChartFooter` therefore go to the home page).

**False-dirty editors (still).** `event-rules`, `news-sources`, `notifications`, and now `checks` show "Unsaved changes" on a clean load.

### 1.2 What the insight layer got right (keep and spread)
- Company header with `ScoreMark` + three passed and three failed checks as sentences + "See all checks" (`/companies/[t]`): exactly the Simply Wall St rewards/risks pattern.
- `CheckList` on the Checks and Dividends tabs: icon + sentence with figures + "See the figures", failed first, "n of m applicable".
- `ChartFooter` (Data · Learn · dataset · as-of · source) is present on income, valuation and dividend charts.
- `GroupedTable` on Valuation (Overvalued 4 / Stale 0 / Undervalued 1 / No fair value 25) and holdings **view presets** (Check-in, Holding, Income, Growth, Value, Score, Fundamentals, Custom).
- Income contributors bars, dividend-quality tier rows + one stacked share bar, the dividend calendar.
- Overview contribution diverging bars (green right, red left, sorted) read instantly.
- Mobile card list for holdings exists (Check-in preset renders as cards).

### 1.3 New findings (not in v2)

**A. Three "needs you" places, plus the old queues.** `/inbox` (agent proposals + "Everything else that changed" feed), `/autopilot` (78 "Needs you" cards + Decisions / What it handles / How sure vs right), `/escalations`, and still `/review`, `/relationships`, `/news/disputed`, `/disclosures/{alerts,adjudication,deals/parties}`, `/ai-decisions`. v2 planned one Inbox; the code grew two more. Autopilot repeats one card per purchase ("Write down why you bought X"), 20+ times.

**B. The Simply Wall St language is applied, but inconsistently.**
1. *Sign colour is not one thing (breaks P10).* Holdings treemap and the holding candle chart use **blue for up**, red for down (`components/charts/candle-chart.tsx` uses `accent` for up candles; treemap uses the `heat-up` blue scale). Overview contribution, P/L figures and checks use **green** for good. Scorecard "Lifting the score" and "Holding it back" bars are both blue.
2. *ScoreMark is a uniform-blue pie of petals,* not one shape whose colour carries the overall verdict; at 24 px in rows it is absent (holdings rows show a ticker box instead of the mark).
3. *DotStrip:* dots are unlabeled (names sit as chips below the axis); the "forecast earnings growth" strip has one outlier at ~25,000% that squeezes all other dots into one pixel column, and the portfolio line reads "2128%"; on "price against fair value" the tinted zones look reversed relative to the Valuation page (ASML is "63% above" fair value there but its dot sits in the left/undervalued half). Needs a fixed sign convention, labels and winsorised axis with an outlier arrow.
4. *BandGauge* renders an empty three-zone band with no marker when the DCF is not computable (company Valuation).
5. *ForecastBars* (Income) draws a 36-month axis where only the last ~4 months have data; estimates are a lighter blue, not the forecast hue.
6. *Radar* still used for Overview "Portfolio risk, normalised" (v2 said BandGauge); Overview "Portfolio scorecard" and the risk radar sit side by side as two different polar glyphs.
7. *VerdictLine* exists but most tiles still lack a sentence (Overview stat tiles, X-Ray, Performance, Funds).

**C. Empty and limited states still drawn as charts (breaks P15).**
- Risk: the correlation triangle is drawn with every cell "–" (10 overlapping observations); "Where the risk sits" shows one holding; the 5×5 DCF sensitivity grid is 25 "Unknown" cells; the Valuation hero chart is a large dashed empty box ("Not enough stored history to draw yet"); Overview "Knock-on between holdings" draws a full-width Sankey for a single link; "How complete the return data is" shows three 0% bars.
- The same caveat banner appears twice on `/risk` ("Read these with the score" and "Read this first", identical text).
- Repeated "unknown — missing X" lines in CheckList (Apple Value 4 of 6 unknown, Risk checks 5 unknown) push the real results down.

**D. Raw and technical text.**
- Raw ids at scale: holding page 525, `/insights` 132, `/news` 110, `/disclosures/look-through` 84, `/research/[id]` 82, `/activity` 81, Overview 77, AI decisions 75, Evidence screen 71, Data coverage 62, Attribution 53, Lots 57.
- Raw machine names: `ProductMember`, `IPhoneMember`, `WearablesHomeandAccessoriesMember` (company overview segment bars, while the panel below has clean names); `commodity_wti`, `us_10y`, `fed_funds` (Overview macro, Macro page h3s); `rule:max-country:geography:Unclassified` (Inbox); `monthly_review` (Autopilot); job keys in Engine ledger (`news_triage`, `inbox_agent`); jobs config h3s "Portfolio sync portfolio_sync".
- Methodology jargon in primary copy: "sector-label candidates, not verified comparable", "FMP not checked yet", "mean of 1 of 1 matches with computed checks".
- HTML entities and untranslated headlines in the Check-in "Last event" column (`F&amp;O`, German headlines).
- Feed noise: Inbox "Everything else that changed" and Overview "What happened to your holdings" list corporate-structure decisions as **News** ("GOOGL_US_EQ: subsidiary: GOOGLE BELGIUM … confirmed → confirmed", dozens per company); 654 unread.

**E. Pages doing two jobs.** Engine ledger and Discover mix reporting with settings forms ("What a stricter floor must prove first", "When the engine tells you…", inputs + Save). Company Valuation opens on a 3-column scenario *editor* before showing any answer. Overview carries 23 panels incl. data-quality diagnostics (macro mapping, return-data completeness, fund overlap unknowns).

**F. Two company pages, still.** `/holdings/[t]` (price chart, position, valuation snapshot, drawdown, next report, 35+ panels, 50k px) and `/companies/[t]` (16 tabs with a horizontal scrollbar under them, **no price chart and no position on its Overview**). Each has what the other lacks.

**G. Mobile (390 px).** Top nav still a clipped horizontal scroller ("Overs…"); KPI tiles stack one per row; holdings cards are ~330 px each (every field on its own line) so 30 holdings ≈ 10k px; page-level horizontal scroll on `/companies/[t]/valuation` (473 px) and `/holdings/[t]` (392 px); Overview 15k px.

**H. Data contradictions visible in the UI** (see §7): AAPL dividends "Years without a cut 9" next to the check "2 years without a cut"; "Buy before Buy before 2026-11-09…" duplicated phrase; Netflix fair value USD 41 vs price 72 (likely split); Apple shows Revenue growth / ROIC / FCF yield "Unknown" (operating cash flow missing); Risk computed on 10 aligned observations; overview volatility 62.0% vs Risk 61.5%; Valuation covers 5 of 30 holdings.

---

## 2A. House visual system (Simply Wall St language, applied to everything)

Source: `simplywallst_visualization_catalog.md` §1-§7. Implemented as tokens in `apps/web/src/app/tokens.css` and `globals.css` (light and dark), so every page inherits it without per-page work; components only reference roles.

### 2A.1 Theme and surfaces
| Element | Today (Ledger) | House style |
|---|---|---|
| Default theme | light, blueprint grid | **dark charcoal is the default**, light kept as an equal alternative (both fully designed; toggle stays) |
| Page background | light grey with crosshair-corner blueprint cards | charcoal page; cards one step lighter, **rounded corners (12 px), no hard borders, no crosshair corners**; separation by surface step and spacing, not lines |
| Card density | many equal-weight cards in grids | fewer, larger cards; one focal card per row; generous inner padding (24 px desktop, 16 px mobile) |
| Text levels | several greys | exactly three: primary, soft, faint |
| Dividers | 1 px rules everywhere | only inside tables and between list rows; never around cards |

### 2A.2 Typography
| Role | House style |
|---|---|
| Page title, panel title, KPI hero number, headline in feed cards | **serif display face** (one family, 2 weights) |
| Body, labels, table cells | clean sans |
| Numbers | sans with tabular figures (mono only for code, ids in Details, and cron) |
| Eyebrow labels | small sans, sentence case (drop the all-caps mono eyebrows) |
| Floor | 12 px minimum anywhere; KPI captions 12-13 px under a 28-40 px figure |

### 2A.3 Colour semantics (one meaning per colour, every page)
| Role | Hue family | Used for |
|---|---|---|
| Good / cheap / pass / gain | green | up moves, undervalued zone, passed checks, positive contribution |
| Bad / expensive / fail / loss | red | down moves, overvalued zone, failed checks, negative contribution |
| Brand / primary action / selected tab | **StewardAI accent** (keep our own hue, not amber) | buttons, selected tab, the user's own series, links |
| Forecast / estimate / AI-projected | purple | estimated dividends, analyst forecasts, projected values; always dashed or hatched |
| Fair value | magenta, stepped line | every fair-value line and chip |
| Revenue / earnings series | blue / teal | financial series pairs |
| Risk scale | blue → purple → red gradient | risk score bar only |
| Warning / limited data | amber chip | "Limited data", stale, partial |
| Unknown | neutral + hatch | missing values, never zero |
| Score mark fill | weak (red-orange) / mixed (yellow) / strong (green) | overall score blob only |
Past is solid, future/estimated is a different hue *and* dashed, never just lighter. Every green/red value also carries a sign or arrow (no colour-only meaning).

### 2A.4 Numbers and sentences
- **KPI style**: big serif figure, small caption under it, coloured left rule or arrow for sign, one-line interpretation ("vs market 12.9%, more volatile") from `VerdictLine`. Used for every answer strip on every page, including System and Settings (e.g. Activity: "99.3% ran first time · above your 95% target").
- **Check rows**: icon + bold lead-in + sentence with the figures + section link. Used for every pass/fail list in the app: company checks, risk checks, release checks, verification gates, data-quality issues, autonomy targets, strategy rule compliance, editor validation.
- **Badges**: `LIMITED DATA`, `ESTIMATED`, `STALE`, `n/a` as small amber/neutral chips; an explicit sentence when a check does not apply.

### 2A.5 Chart furniture (every chart in the app)
- Title in serif with an `i` tooltip (definition + method link).
- Footer: `Data` (table twin) · `Learn` (method page) · `…` menu (download CSV, copy link, full screen) · dataset, last updated, timeframe, source.
- Zones instead of gridlines; 3-5 axis ticks; labelled reference lines (portfolio, benchmark, median, limit, target).
- Hover/tap card with date, the series values and a one-line reading; hover panels list items for bar charts (as dividends do).
- Range chips (1M · 3M · YTD · 1Y · 2Y · All) on every time series; series chips that toggle lines.
- Expand icon opens the chart full size.

### 2A.6 Navigation and page shell
- **Top bar**: logo, primary destinations (§3.1), global search with ⌘K centred, notifications bell (Inbox count), Ask Steward, avatar menu (theme, settings, sign out). The market strip moves into a slim row under the bar on Portfolio pages only.
- **Page tabs under the title** for every area (Portfolio, Markets, System, Settings alike).
- **Entity pages (company, fund, 13F manager, news story, research report, job run, escalation) use a sticky left sidebar**: ScoreMark or status mark, name, key figure, primary action, and **numbered sections 1-n** that scroll-spy the page. Sections lazy-load as you scroll. This replaces horizontal tab rows on entity pages.
- Mobile: bottom bar, sidebar collapses into a section chip row under the header.

### 2A.7 Interaction
- Row click opens a right **side drawer** with tabs and "Full page" on every list in the app (holdings, inbox, discover results, jobs, news, alerts, AI decisions, sources, data-quality issues).
- **View presets** on every large table (as on Holdings): each list page offers 2-5 named column sets + Custom (e.g. Activity: Overview · Cost · Failures; News sources: Health · Trust · Volume).
- **Grouped tables with counts and an explanatory line** for every list that has a verdict or state (Inbox by kind, Release checks by status, Data quality by severity, Jobs by state, Alerts by company).
- **Prompt cards** inline where the user's input unlocks value ("Write a thesis for 30 holdings to score them").
- Filter chips with counts and unread dots for every feed (news, updates, alerts, activity).
- No pop-ups, no modals over content except confirm dialogs and full-screen chart/drawer.

### 2A.8 Motion and loading
Skeletons in the final shape of each card; sections lazy-load on scroll; numbers count up once on first paint only; no spinners longer than 1 s without a skeleton.

## 2B. Design principles (final)

v2 P1-P15 stand (one answer per page, ≤7 tabs, nothing lost, Chart⇄Table, right chart for the data, show what is missing once, names not ids, fast to feel, calm density, colour means one thing, zones not gridlines, every number gets a sentence, one score mark at every scale, footer on every chart, honest limited states). Added from this walk:

| # | Principle | Rule |
|---|---|---|
| P16 | **Answer before tools** | A page or tab opens on its conclusion (number + verdict + hero visual). Editors, scenario inputs, filters and settings sit behind a button, drawer or a lower section, never above the answer. |
| P17 | **A chart needs data to exist** | Every chart declares a minimum (`minPoints`, `minItems`, `minCoverage`). Below it, the slot collapses to a one-line `LimitedState` ("Needs 60 shared trading days, has 10 · what fills it"). No empty frames, no all-dash matrices, no one-link Sankeys, no 25-cell "Unknown" grids. |
| P18 | **Green/red are sign, blue is you** | `gain`/`loss` for up/down and good/bad *everywhere* (treemaps, candles, contribution, checks, lifting/holding back). `accent` blue = the user's own series, selection and links only. Heat scales are diverging gain↔loss with neutral grey at 0. |
| P19 | **One place for decisions** | Anything the owner must confirm, reject, write or snooze lives in Inbox. Other pages may show a count chip linking there, never their own queue. |
| P20 | **Reports read, Settings change** | Report pages (Engine ledger, Discover, Activity, Data sources) contain no editable settings; each settings form moves to Settings with a "Change in Settings" link back. |
| P21 | **Signal over records** | Feeds show events a person would want to read. Structural record changes (subsidiary confirmed, identifier matched) are grouped into one collapsed line per company per day ("12 corporate-structure updates") and never typed as News. |
| P22 | **Unknown is grouped, once** | In check lists and tables, unknown/not-applicable rows collapse under one line ("4 checks couldn't run: missing operating cash flow · fix"), shown after pass/fail rows. |

### 2.1 Colour roles as tokens (implements §2A.3; `lint:colors` enforced)

| Role | Token | Used for | Never for |
|---|---|---|---|
| Gain / good / pass | `gain` | up moves, positive contribution, passed check, undervalued zone, "lifting the score" | series identity |
| Loss / bad / fail | `loss` | down moves, negative contribution, failed check, overvalued zone, "holding back" | series identity |
| You / selection | `accent` | portfolio series, selected tab/axis, links, primary button | sign |
| Benchmark | `chart-benchmark` (grey dashed) | index lines and reference bars | |
| Forecast / estimate | `--chart-forecast` (purple, + dashed/hatched) | estimated dividends, analyst forecasts, projected values | historical data |
| Fair value | `--chart-fair-value` (magenta, stepped line) | fair value lines, StepLine overlays, fair-value chips | anything else |
| Revenue / earnings | `--chart-revenue` (blue), `--chart-earnings` (teal) | financial series pairs | sign |
| Risk scale | `--risk-low` → `--risk-mid` → `--risk-high` (blue → purple → red) | risk score bar | anything else |
| Limited / stale | `--warn` (amber chip) | LIMITED DATA, STALE, ESTIMATED badges | sign |
| Unknown / unmapped | neutral + diagonal hatch | missing data | zero |
| Score mark fill | `--score-mark-{weak,mixed,strong}` | ScoreMark blob by overall score | per-axis colour |
| Confidence | existing ordinal blues | evidence confidence only | sign |

---

## 3. Information architecture (final)

### 3.1 Top level
v2 §3.1 stands: **Portfolio · Companies · Research · Markets · Briefings · Inbox (badge) · System · ⚙ Settings**, command palette for everything, "?" help menu (Glossary + Methods). Mobile bottom bar: **Portfolio · Research · Markets · Inbox · More**.

### 3.2 Coverage matrix: every route has one home (147 routes)

"→" = redirect target. Every old URL gets a permanent redirect in `lib/nav.ts` `REDIRECTS`; a test asserts each one resolves.

**Portfolio** (`/`)
| Tab | Today's routes | Notes |
|---|---|---|
| Overview | `/` | slimmed to 7 panels (§5.1); diagnostics move to System › Data quality |
| Holdings | `/holdings`, `/transactions`, `/watchlist`, `/funds` (portfolio-fund views) | sub-tabs Positions · Watchlist · Transactions · Funds |
| Performance | `/performance`, `/performance/{attribution,core-vs-stocks,lots,ledger}` | tab titles: Returns · Contribution · Core vs stocks · Lots & tax · Ledger |
| Income | `/portfolio/income` | |
| Valuation | `/portfolio/valuation` | |
| Allocation | `/xray`, `/xray/{diversification,concentration,overlap,benchmark,fundamentals,supply-chain,proposed,inputs}` | Exposure · Diversification · Concentration · Overlap · Benchmark · Fundamentals · More (Supply chain, What-if, Your data) |
| Risk | `/risk`, `/risk/{ex-ante,tail,replay,factors,stress,themes,validation}` | Overview · Today · Bad months · Past episodes · Factors · Stress · More (Themes, Does it hold up) |
| Decide | `/risk/{whatif,rebalance,pretrade,deposit}`, `/xray/proposed` | one tab with a mode switch; empty state shows a worked example |

**Companies**
| Page | Today's routes |
|---|---|
| Company `/companies/[t]` | `/holdings/[t]` → here; `/companies/[t]/*` (16 tabs) regrouped into 9 numbered sections (§5.3); old tab URLs redirect to their section |
| Fund `/funds/[isin]` | `/funds/[isin]/{holdings,performance,style,bond,checklist}`, `/funds/compare`, `/funds/research` (as "Find a fund") |

**Research**: Theses (`/theses`, `/thesis`, `/thesis/memos/[id]`, `/thesis/reviews/[id]`, `/config/theses` editor link) · Reports (`/research`, `/research/[id]`) · Discover (`/discover`, `/discover/backtest`, `/screens`) · Journal (`/journal`, `/journal/scorecard`) · What changed (`/insights`, `/insights/sectors`).

**Markets**: News (`/news`, `/news/{stories,stories/[id],wire,brief,ask,sources}`) · Macro (`/macro`) · Filings (`/disclosures`, `/disclosures/{ownership,insiders,network,deals,estimates,screen,look-through,replay}`) · 13F (`/investors`, `/investors/[cik]`).

**Briefings**: Daily & weekly (`/reports`, `/reports/[id]`) · Monthly reviews (`/reviews`, `/reviews/[id]`).

**Inbox** (`/inbox`): absorbs `/autopilot` (Needs you), `/escalations` + `/escalations/[id]` (item detail drawer/page), `/review`, `/relationships`, `/news/disputed`, `/news/ops` "worth a second look", `/disclosures/{alerts,adjudication,deals/parties}`, AI-decision vetoes. Filter chips with counts by kind (§5.8).

**System**: Autonomy (`/engine-ledger`, Autopilot's Decisions / What it handles / How sure vs right) · Activity (`/activity`, `/jobs/[id]`) · Data sources & cost (`/data-sources`, budget read view) · Data quality (`/quality`, `/disclosures/coverage`, Overview diagnostics) · Verification (`/verification`) · Release (`/release`, `/release/{audit,pilot}`) · AI decisions (`/ai-decisions`, audit) · API status (`/status`).

**Settings** (gear): Investment rules (Strategy, Theses, Holding profiles, Event rules, **Checks**) · Sources & data (News sources, Calendar, Research profiles, Verification) · Automation (Jobs, Budget, Prompts, Notifications, **Autonomy thresholds** moved from Engine ledger, **Starter screens** moved from Discover) · History & export (`/config/history`, `/config/history/[domain]`). `/config` → Strategy.

**Global / unchanged**: `/chat` (slide-over + page), `/glossary` and `/methods/*` (help menu; fix the production routing first), `/evidence/[factId]` (drawer from any "See the figures" link, page for deep links), auth routes.

---

## 4. Shared foundations

v2 F1-F17 still to build (none exist yet except where noted): SectionNav, ResponsiveTable, formatters/NullCell, Panel primitives, label maps, `useConfigEditor` clean-baseline fix, ResponsiveNav/BottomBar, FilterChips, ViewToggle/ChartOrTable (partly: ChartFooter "Data"), AnswerStrip (exists in `components/insight`), CoverageStrip, EntityChip, PageTabs, GroupedList (virtualised), ReasonOnce, Skeleton + batched summary hook.

### 4.1 Fixes to the shipped insight components (`components/insight/*`)
| Component | Change |
|---|---|
| `score-mark.tsx` | One filled blob (smoothed polygon) on a 0-6 ring scale; fill from `--score-mark-*` by mean score; open notch for unknown axes; sizes 24 / 64 / 240 px; 24 px variant used as the row avatar in every company list |
| `scorecard-panel.tsx` | "Lifting" bars `gain`, "Holding back" bars `loss`; company names via EntityChip; selected-axis explanation as a `VerdictLine` |
| `dot-strip.tsx` | Direct labels on dots (collision-avoid, max 12, rest in "+n" tooltip); size by weight optional; winsorise to p2-p98 with "›" outlier markers and values; a declared sign convention prop (`"price-vs-fair"`: negative = cheaper = gain zone) and zone labels ("cheaper than fair value" / "dearer"); portfolio + benchmark reference lines with labels; hide strip below 3 placed holdings (P17) |
| `band-gauge.tsx` | Must have a marker or render `LimitedState` instead of an empty band |
| `forecast-bars.tsx` | Start the axis at the first non-zero month (or 12 months before the first estimate); estimates in `--chart-forecast` with hatch; Monthly/Annual toggle keeps |
| `check-list.tsx` | Order: fail → pass → one collapsed "n couldn't run" group listing missing inputs once (P22) |
| `chart-footer.tsx` | "Learn" link must resolve (blocked by `/methods` production bug); add "known at" |
| `updates-feed.tsx` | Type taxonomy (News, Earnings, Analysts, Dividends, Insiders, Risk, Valuation, Structure); Structure grouped per company/day and hidden by default (P21); decode HTML entities; show translated title with original-language chip |
| `side-drawer.tsx` | Use from holdings rows, Inbox items, Discover results, "See the figures" links |

### 4.2 New foundations
| # | Component | Purpose |
|---|---|---|
| F33 | `<LimitedState>` + `useMinData()` | P17 guard: every chart wrapper takes `{min, have, unlocks, fixHref}` and renders the chart or a one-line state |
| F34 | `<EntityName>` resolver | One hook/component to turn any instrument id into name + symbol chip (+ ScoreMark avatar); lint rule/test that fails on `_EQ` text in rendered output of page tests |
| F35 | `<InboxItem>` | Uniform card: kind chip, entity, one-sentence why, evidence link, primary action(s), snooze, dismiss; batch variant ("20 purchases need a reason · Write all") |
| F36 | `<KpiTile>` | Big number, small label, sign arrow, `VerdictLine`, optional sparkline; grid 4-up desktop, 2×2 mobile |
| F37 | `<CompactRow>` | Mobile list row: avatar + name + 1 primary value on line 1, 2-3 secondary values on line 2, chevron to drawer (≤ 72 px) |
| F38 | `<TabBar>` with overflow | ≤7 visible tabs, rest under "More ▾", no visible scrollbar; per-tab `<title>`/h1 helper |
| F39 | `<LabelMap>` registry | Machine names → human labels (XBRL members, macro series ids, rule ids, job keys, provider names); unknown keys fall back to a humanised string, never the raw key |
| F40 | `<SettingsLink>` | "Change in Settings" pointer used where P20 moves a form out |

---

## 5. Page-by-page target

### 5.0 Page types: how the house style applies to every page, including ones Simply Wall St has no equivalent for

Every route is one of six page types. The type fixes its anatomy; §5.1-5.13 only add the content.

| Type | Anatomy (house style) | Routes |
|---|---|---|
| **T1 Dashboard** (like SWS Holdings/Analysis) | serif H1 + strategy-style one-liner → page tabs → KPI strip (big serif figures, captions, verdict lines) → one hero chart with range chips and footer → 2-col cards → grouped table with view presets | Portfolio tabs, Inbox, System › Autonomy/Activity/Data sources/Data quality/Release/Verification, Markets › News/Macro/Filings landing, Briefings list |
| **T2 Entity report** (like SWS stock report) | sticky left sidebar (mark, name, key figure, action, numbered sections) → header strip (price/status, chips, returns) → numbered sections each opening with "n of m checks" + key figures + check rows + charts → data-status footer table | Company, Fund, 13F manager, Research report, Monthly review, Daily digest, News story, Escalation, Job run, Evidence |
| **T3 Feed** (like SWS Updates) | filter chips with counts and unread dots → day-grouped cards (logo/mark, entity, type chip, age, serif headline, 2-line summary + "Show more", right-side price and move) → grouped "n similar" | News feed/wire/stories, Updates, Disclosure alerts, AI decisions, Activity job log |
| **T4 Screener** (like SWS Screener/Discover) | idea chips + plain-English box → filter cards with chips → result count + "n new in 7 days" → results table/grid with ScoreMark | Discover, Screens, Evidence screen, Backtest, Funds research, 13F investors list |
| **T5 Workbench / calculator** (like SWS narrative valuator) | answer card first (result + verdict) → "Edit assumptions" opens a side panel with sliders showing default / historical / industry value beside each input → printed `CalcChain` → saved versions | Valuation scenarios, Decide (what-if, rebalance, pre-trade, deposit), Stress, Themes, Factors "move the factors", Decision replay, Disclosures replay, Backtest setup |
| **T6 Settings** (like SWS portfolio settings) | grouped left menu → master list with one-line status per row → detail form in a card with plain labels, help text and "default / current" beside each value → sticky save bar; a small live summary visual at the top of each editor | all `/config/*`, Autonomy thresholds, Starter screens |

Translation rules for pages with no SWS equivalent:
- **Operational lists become check rows or a status matrix.** Release checks, verification gates, data-quality issues, endpoint access and autonomy targets render as `CheckList` rows (icon + bold lead-in + sentence with figures + "See details"), grouped by state with counts. Job runs, source health and coverage render as the job-run heatmap (`StatusMatrix`).
- **Every system number gets a verdict sentence** against its target ("AI spend $18.52 of $25 · on track to finish the month at $24").
- **Every queue item is a feed card** with type chip, entity mark, age and the action buttons on the card.
- **Every editor shows its effect** in one summary visual at the top (Jobs → week timeline; Event rules → severity matrix; Checks → "n holdings would change"; Strategy → target donut; Budget → burn-up on a band gauge; News sources → trust bars; Notifications → week heat of expected alerts).
- **Every entity gets a mark**: companies the ScoreMark; funds a five-axis fund mark (Cost · Diversification · Tracking · Size · Data quality) on the same component with its own axes; jobs, sources and checks a status dot; 13F managers a portfolio-concentration mark. Marks appear at 24 px in every row.

Each page below follows its type plus: eyebrow · H1 (= tab name) · one-line purpose · primary action → tabs → **answer strip** (3-4 `KpiTile`s) → **hero visual** (Chart ⇄ Table, `ChartFooter`) → 2-column secondary panels (`items-start`) → detail tables (paginated) → Details/Raw collapsed.

### 5.1 Portfolio › Overview (8.8k → ≤ 2.7k px)
- Answer strip: Value (with day change) · Total return vs benchmark · Unrealised P/L · Income next 12 m.
- Hero: value line vs benchmark (keep), with zone shading where the portfolio leads/lags.
- Row 2: **Portfolio scorecard** (large ScoreMark + selected axis, lifting/holding back in gain/loss) | **Risk at a glance**: risk score `BandGauge` with portfolio and benchmark markers + 3 KPI tiles with verdicts (replaces the risk radar).
- Row 3: **Why it moved** (contribution diverging bars, top 8 + "others") | **Allocation** (treemap sector → holding, gain/loss colour toggle, "Other 21" not allowed: treemap shows all).
- Row 4: **What happened** — `UpdatesFeed` limited to 8 typed items, Structure hidden; link to full feed (Markets › News filtered to holdings).
- Move out: drift (merge into Holdings › Positions weight column + Strategy), rule compliance (Inbox when breached, Strategy otherwise), monthly heatmap (Performance), effective holdings / look-through (Allocation), macro moves (Markets › Macro), fund overlap (Allocation › Overlap), return-data completeness (System › Data quality), knock-on (Allocation › Supply chain), valuation vs benchmark (Valuation).

### 5.2 Portfolio › Holdings
- Positions: answer strip (keep 5 tiles, 2×2 on mobile); treemap in gain/loss colours (P18) behind a "Map" toggle, table default.
- Rows: 24 px ScoreMark avatar + EntityName; presets keep. Check-in preset: event chip with type icon and date, decoded/translated headline (relevance ≥ threshold, else "No notable event"), price vs fair value chip (gain/loss), thesis column becomes a "Write thesis" action when empty (not 30× "No thesis"), sparkline with `StepLine` fair-value overlay.
- "No target" ×30 → one banner "No target weights set · Set targets" and the column hides until a target exists.
- Row click → `SideDrawer` (Overview · Position · Updates · Notes · Full page).
- Mobile: `CompactRow` (≤ 72 px), preset chips scroll horizontally.
- Watchlist (empty today): empty state with "Add from Discover" and the fair-value range bar spec from v2.
- Transactions: calendar heatmap of flows + net-flow bars; table paginated.

### 5.3 Companies › Company (merge holding page + workbench; 50k → ≤ 3 screens per tab)
Header (sticky on scroll, compact): name + symbol + exchange, price + day change, **position summary if held** (value, P/L, weight), fair value chip with its basis, ScoreMark 64 px, verdict line, Ask / Research buttons, ticker switcher.

Layout: page type T2. A sticky left sidebar (ScoreMark 120 px, name, price, fair-value chip, "Ask" and "Start research", position card if held) lists **numbered sections; each section is a URL sub-route and the sidebar scroll-spies within it**. Each section opens like the reference stock report: "n of 6 checks" + key figures + the check rows for that axis + its charts. "See all checks" opens the full CheckList dialog, failed first (the Checks route stays as that dialog's deep link).

1. **Overview** (`/companies/[t]`) — price chart (line by default, candles toggle; `gain`/`loss`; events as lollipops; fair-value StepLine; range chips), rewards/risks sentences (exists), latest results AI note with "Ask about this release" question chips, shareholder returns vs industry vs market (7D, 1Y) with a pass sentence, price volatility vs industry and market, About block, competitors row (four cards with ScoreMarks), next event.
2. **Valuation** (`/valuation`, `/consensus`, `/peers`) — headline `BandGauge` (20% under · about right · 20% over) on the chosen basis, with the basis named; price vs every available fair value as one labelled strip (DCF, multiple path, analyst mean, thesis target) so two valuations never compete silently; price vs fair-value history; peer bars with average line and growth column; industry histogram with highlighted bin and per-bin table; multiple history; fair multiple; analyst target chart with dispersion band and 12-month cone; scenarios open in the T5 side panel ("Edit assumptions") with `CalcChain`; sensitivity grid only when computable.
3. **Future** (`/estimates`) — revenue and earnings history joined to forecasts (past shaded, forecast purple dashed, series chips), growth checks, estimate revisions, earnings surprise.
4. **Past** (`/financials`, `/segments`) — revenue → gross profit → earnings Sankey with a year strip, quarterly history table, past-performance checks, segments (LabelMap names) as donut ≤ 7 + delta bars.
5. **Health** (`/ratios`, `/allocation`) — paired short/long-term assets vs liabilities bars, debt-to-equity history, debt maturity ladder, cash conversion, capital allocation ("where the cash went"), health checks.
6. **Dividend** (`/dividends`) — exists; add yield vs market bottom 25% / top 25% / industry bar and the "buy before" countdown; fix contradictions (§7) and partial years (hatched, labelled "partial").
7. **Ownership & management** (`/governance`) — management stat tiles (CEO, pay, tenure, ownership), CEO pay analysis, board table, insider transactions + insider volume chart with verdict, holder-type stacked bar, top holders table.
8. **Research & thesis** (`/case`, `/assessment`, `/report`, thesis + KPIs, research reports for this ticker) — thesis verdict, KPI bullets vs targets, claim ledger.
9. **Network & reference** (`/network`, identity, listings, price completeness, corporate actions from the holding page) — network graph, patents, then reference data.
- Data-status footer on every section: dataset, last updated, timeframe, source.
- Every holding-page panel is assigned to one of these sections; the PR carries a "moved here" table. Sections below the fold lazy-load; ≤ 1 summary request for the sidebar and header.
- Fund page uses the same T2 layout with sections Overview · Holdings · Performance · Style · Bond · Checklist and the five-axis fund mark (§5.0).

### 5.4 Portfolio › Income
- Answer strip: next-12-month income (largest), vs last 12 m, monthly average, yield, yield on cost.
- Hero: `ForecastBars` from first payment; history `gain`-neutral solid, estimate forecast hue; hover panel lists payments.
- Calendar (`MonthCalendar`) beside "Upcoming" list.
- Contributors: one diverging/ranked list (largest → smallest) instead of two lists with the same five names; hide "smallest" when payers ≤ 8.
- "What the figures leave out" banner: names, not ids; one line with count + expand.
- Quality tiers + table (keep).

### 5.5 Portfolio › Valuation
- Answer strip: coverage first ("5 of 30 holdings have a fair value on this basis") · weighted gap vs fair value · overvalued weight · stale count.
- Basis switcher (keep).
- Hero: portfolio `DotStrip` of price vs fair value (sizes = weight, labelled), not the empty history chart; the price-vs-fair-value history chart appears only when ≥ 30 stored days (P17), with a "recording since 16 Oct" note.
- Grouped table (keep) + "No fair value 25" group collapsed with "Add a scenario" actions; the raw-id banner becomes that group.

### 5.6 Portfolio › Allocation (X-Ray) and Risk
- Allocation per v2 §5 (treemap + Sankey sector → industry → holding; Overlap HeatMatrix triangle; Concentration Lorenz + HHI bullet + verdict; Fundamentals DotStrips per §4.1; Benchmark diverging active weights). `/xray` landing and `/xray/benchmark` must render a heading and skeleton immediately.
- Risk per v2 §6.1, plus: risk score `BandGauge` shows "Not scored: needs beta and expected shortfall (250 obs, have 196)" as one `LimitedState`; correlation triangle hidden until ≥ 60 shared days with the reason once; one caveat banner per page (ReasonOnce); per-tab h1 and subtitle.
- Decide tab: What-if · Rebalance · Pre-trade · Deposit as a mode switch with a dumbbell before→after result; each empty form shows a pre-filled example result.

### 5.7 Performance
v2 §5 stands (returns waterfall with currency, attribution waterfall + ranked bars, underwater drawdown, core vs stocks line pair, lots age ladder). Contribution warning with 25 tickers → "25 holdings couldn't be attributed (expand)". Monthly heatmap moves here from Overview.

### 5.8 Inbox (one place for decisions)
- Answer strip: items needing you · oldest age · decided this week · auto-handled this week (link to System › Autonomy).
- Filter chips with counts: Proposals (agent research/rebalance proposals) · Escalations · Journal prompts · Figures to check · Relationships · Disputed news · Disclosure alerts · Accuracy checks · Deal parties · AI decisions to veto · Jobs stopped. Empty chips fold into "Nothing waiting in 6 queues".
- Items are `InboxItem`s, grouped by company then kind; repeated prompts batch ("Write down why you bought 20 holdings · Start" opens a journal form stepping through them).
- Item detail opens in `SideDrawer` (escalation note, sources, thesis link, proposed action with Confirm/Decline).
- "Everything else that changed" leaves the Inbox (it is a feed, not decisions) → Markets › News (holdings filter) and Overview row 4.
- Mobile: one item per screen review mode with swipe + visible buttons.

### 5.9 Research
- Theses: board grouped by verdict; while "No theses configured", the page is a guided start (pick holdings → Thesis workup) instead of an empty strip; workup (`/thesis`) becomes the "New thesis" flow inside Theses.
- Discover: results first for a saved/starter screen (ScoreMark rows, grid ⇄ list); idea cards with aggregate ScoreMark and plain copy (no "FMP not checked yet" in cards; coverage note moves to the footer); "Ask for a screen" fills editable chips; advanced filters in a dialog with `SliderRange`; starter-screen editing moves to Settings (P20). Scope note stays visible: "29 instruments covered — your holdings".
- Reports (`/research`): list with status chips; report page with TOC rail, names not ids, claim ledger collapsed.
- Journal / Scorecard: calibration dots; What changed: names not ids (132), diverging bars by sector.

### 5.10 Markets
- News: v2 spec (typed chips with counts, hide Ignored, group repeats, day headers, virtualise) + P21 structure grouping + translated titles; Sources, Brief, Ask, Wire, Stories as tabs; Disputed → Inbox.
- Macro: human labels (LabelMap), small multiples with axes/units, drop "No change computed" rows into one line.
- Filings: landing opens on a portfolio summary (alerts by company + coverage StatusMatrix), issuer as a filter; per-tab hero visuals per v2; Evidence screen and Look-through use EntityName.
- 13F: treemap of positions; manager page with delta bars.

### 5.11 Briefings
Reports list as a timeline with one row per digest (date, material stories count, cited sources); report detail with TOC rail, company sections titled by name (fix glued "AppleAAPL_US_EQ" h3), Sources rail grouped by domain with counts, capped height. `/reviews` h1 "Monthly reviews".

### 5.12 System
- Autonomy (Engine ledger + Autopilot decisions): KPI rows become **bullet charts** (actual vs target, met/not met), decisions list with filters; thresholds forms move to Settings › Automation.
- Activity, Data sources, Data quality, Verification, Release, AI decisions, Status per v2 (StatusMatrix pattern from the job-run heatmap, paginated lists, plain status instead of raw HTTP errors, Details collapsed). Data quality absorbs the Overview diagnostics and Filings coverage.

### 5.13 Settings
v2 §3.5 master-detail editors (Holding profiles list + form, Jobs week timeline + list, Event rules severity matrix, News sources trust bars, Strategy tabs with live 100% donut). Additions: **Checks** editor as threshold rows with live "n holdings would change state"; Prompts list with a read view (no 33 `<pre>` blocks open at once; code view on expand); Research profiles as accordions with one-line summaries; job keys hidden behind names. All editors load clean.

### 5.14 Every catalog item and where it lands

Each row of `simplywallst_visualization_catalog.md` (§1-§7) with its StewardAI slot and phase. "Skip" items are listed with the reason, so nothing is silently dropped.

| Catalog item | StewardAI slot | Phase |
|---|---|---|
| Dark charcoal theme, rounded borderless cards, serif display headings, 3 grey levels | §2A tokens, all pages | R1a |
| Colour carries meaning across every chart | §2A.3 / §2.1 tokens + `lint:colors` rule | R1a |
| Zones instead of gridlines | chart kit defaults (`components/charts/shared.tsx`) | R1b |
| Sentence beside every number | `VerdictLine` on every `KpiTile` | R1b, all page phases |
| Data / Learn / … footer + dataset table | `ChartFooter` everywhere + T2 data-status footer | R1b |
| Big headline numbers, coloured left rule | `KpiTile` | R1b |
| LIMITED DATA badges, "n/a", "does not apply" sentences | `Badge`, `LimitedState`, CheckList not-applicable rows | R1b |
| Top bar with ⌘K, notifications, avatar; page tabs; sticky stock sidebar with numbered sections; lazy sections | shell (§2A.6), T2 sidebar | R2, R4 |
| Snowflake at every size (avatar, sidebar, large, card, aggregate, filter) | `ScoreMark` 24/64/120/240; portfolio and idea aggregates; Discover filter with draggable nodes | R1b, R5, R6 |
| Holdings KPI strip, value-over-time with cost basis line, Stacked/Combined, Performance vs market tab | Holdings › Positions answer strip + Performance › Returns hero (cost-basis line added) | R5 |
| Return split cards (unrealised, realised, dividends, currency, estimated dividends) | Performance › Returns KPI row | R5 |
| Holdings table with mark, fair-value chip, presets incl. Fundamentals and custom | Holdings presets (exist) + ScoreMark avatar + fair-value chip | R5 |
| Check-In view with event chip and sparkline + stepped fair value + today marker | Holdings Check-in preset | R5 |
| Side drawer (Overview, Holding, Updates, Notes, Full analysis) | `SideDrawer` on every list (§2A.7) | R1b, R5 |
| Prompt cards and editable strategy line under portfolio name | prompt cards (§2A.7); strategy one-liner under Overview H1, edit opens Strategy | R5 |
| Scorecard with clickable axes and lifting / holding-back lists | Overview row 2 | R5 |
| Ranked dot strips per axis with portfolio and market lines, tinted halves, basis tabs, expand | Allocation › Fundamentals + Valuation hero | R5 |
| Diversification Sankey sector → industry → ticker + concentration bar + revenue-by-geography bars | Allocation › Exposure | R5 |
| Correlation lists + lower-triangle matrix | Risk › Overview (when ≥ 60 shared days) | R5 |
| Risk gradient bar with markers + six metric cards + per-holding ranked bars with reference bars | Risk › Overview + Today | R5 |
| Valuations header KPIs, price vs stepped fair value, summary row, quick-assign basis pills, grouped tables | Portfolio › Valuation (exists, fix per §5.5) | R5 |
| Dividends hero, monthly bars with hover panel, contributors, quality tiers + stacked bar, estimated payments in forecast hue, calendar + events table, "Discover quality payers" link | Portfolio › Income (exists, fix per §5.4) + footer links to Discover screens | R5 |
| Returns waterfall, contributors centred on zero, detailed report with liquidated holdings, transactions and CSV | Performance › Returns / Contribution / Lots | R5 |
| Updates feed with typed chips and unread dots; dashboard portfolio tile | Markets › News (holdings filter), Overview row 4; T3 feed type for all feeds | R5, R6 |
| Watchlist rows with 7D/3M, sparkline + fair value, narrative selector | Holdings › Watchlist (thesis selector instead of narrative) | R5 |
| Stock header with fair value + source label + % chip + returns + portfolio-value strip | Company sidebar and header | R4 |
| Rewards / risks bullets + "See all risk checks" dialog | Company Overview (exists) + all-checks dialog | R4 |
| Earnings-call card with "Ask anything" and suggested questions | Company Overview latest-results note + question chips (opens Ask Steward scoped to the release) | R4 |
| Community fair values, narrative cards, likes, comments | **Skip**: single-user app has no community; the slot shows the owner's own scenarios and thesis target instead | — |
| Competitors row with marks, price-history table, shareholder returns, price volatility, About | Company Overview | R4 |
| Valuation: three-band gauge, price vs DCF history, peer bars + growth column + Edit peers, industry histogram + bin table, multiple history, fair ratio, analyst target chart + table | Company › Valuation | R4 |
| Future: area chart joining history to forecasts with series chips, numbered subsections with pass/fail sentences | Company › Future | R4 |
| Past: income-statement Sankey with year strip + quarterly table | Company › Past | R4 |
| Health: paired assets/liabilities bars + debt/equity history + checks | Company › Health | R4 |
| Dividend: stability chart with series chips, yield vs market bar, "buy in next n days" countdown | Company › Dividend | R4 |
| Management: stat tiles, CEO pay analysis, leadership and board tables | Company › Ownership & management | R4 |
| Ownership: insider table with Buy/Sell tags, insider volume chart + verdict, holder-type breakdown, top shareholders | Company › Ownership & management (holder-type as a stacked bar, which the reference lacks) | R4 |
| Narrative page: fair value header, price chart with dashed target, thesis text, revenue/earnings projection to year 5, valuator with sliders showing default / historical / industry values, calculation chain | Research › Theses detail + Company › Valuation scenario panel (T5) | R4, R6 |
| Screener top block: idea chips, plain-English box filling chips | Discover (§5.9) | R6 |
| Snowflake filter with draggable nodes; Markets / Industries / Metrics cards; keywords | Discover filter block | R6 |
| Advanced filters dialog with grouped metrics, definitions and non-linear dual-handle sliders | Discover advanced dialog with `SliderRange` | R6 |
| Results table with mark, multiples chosen per company, star to watch, sort, list/grid; grid cards with large mark | Discover results (+ "Watch" adds to Watchlist) | R6 |
| Discover idea cards with illustration, aggregate mark, avatar stack, "n new" | Discover idea cards: aggregate ScoreMark as the image (no illustrations), avatar stack of holdings matched, "n new in 7 days" | R6 |
| Idea page: hero, short essay, market/industry selectors, results, "Get new stock alert" | Discover saved-screen page with "Alert me on new matches" (uses `ops/opportunities.py`) | R6 |
| Paywall, top-4 limit, sign-up modal, cookie banner over content | **Skip** (avoid list in catalog §8) | — |
| Avoid: wrongly signed raw figures, two headline valuations, zero score for not-applicable, wide scrolling tables, pop-ups | P15, P17, P22, §5.3 Valuation "basis named", ResponsiveTable, §2A.7 no pop-ups | all |

---

## 6. Mobile (390 px baseline; also 360, 768)
v2 §3.6 stands. Concrete fixes from this walk: bottom bar replaces the scrolling top nav; `KpiTile` 2×2; `CompactRow` for holdings/lists (target: holdings list ≤ 3k px at 390); company tabs as chip row + More; no page-level horizontal scroll (fix `/companies/[t]/valuation` scenario grid → stacked bear/base/bull cards, holding/company page wide tables → ResponsiveTable); treemap and DotStrip get a list fallback below 480 px. Playwright smoke test: every route at 390 asserts `scrollWidth <= innerWidth`.

---

## 7. Data defects the UI exposes (fix or the visuals lie)
Owned by backend plans; listed here because the redesign's honest-state rules depend on them. Each needs an issue and a test.

| # | Symptom in UI | Likely owner |
|---|---|---|
| D1 | `/methods`, `/methods/[slug]` render Overview on production (force-static page reading markdown not present in the image?) | web build / Docker image |
| D2 | AAPL dividends: "Years without a cut 9" vs check "2 years without a cut"; "Buy before Buy before…" duplicated | insight I1/I3 sentences |
| D3 | Netflix fair value USD 41 vs price 72 (post-split basis?) | valuation inputs / corporate actions |
| D4 | Apple operating cash flow missing → Revenue growth / ROIC / FCF yield / DCF unknown | fundamentals ingestion (`empty_data_fill_plan.md`) |
| D5 | Risk uses 10-196 aligned observations; holding-level figures cover 2% of value; volatility 62.0% (Overview) vs 61.5% (Risk) | quant Q1/Q2 alignment |
| D6 | Forecast earnings growth outlier ~25,000%; portfolio 2128% | estimates normalisation / winsorise |
| D7 | Corporate-structure decisions emitted as News feed items (dozens per company) | updates feed typing (I13) |
| D8 | HTML entities and untranslated headlines in Check-in "Last event" | news ingest / translation |
| D9 | XBRL member names in segment bars | segment label mapping |
| D10 | Valuation basis covers 5 of 30 holdings | I5 coverage; funds need a "not applicable" basis, not "no fair value" |
| D11 | `/xray`, `/xray/benchmark` slow/blank first paint; DotStrip sign convention vs Valuation page | xray API / component |

---

## 8. Implementation plan

### 8.0 How to work every task
- **Visual detail lives in `ux_ui_visual_spec.md`**: token values (A), component and chart specs B1-B44 (B), per-page specs with source fields and wireframes (C), formulas, thresholds and sentence templates (D). Every task below builds to that spec; a PR that departs from it updates the spec in the same PR.
- Branch per phase off `claude/nice-noether-jbgj4d` (`ux/r0-quick-wins`, `ux/r1a-visual-system`, …); one PR per phase, or per numbered task group when a phase is large (R4, R5).
- Before UI code: load `frontend-design`; before any chart: load `dataviz`. Read the matching guide in `node_modules/next/dist/docs/` before touching routing, layouts, redirects or metadata (Next 16 differs from training data, per `apps/web/AGENTS.md`).
- Checks per batch of edits, scoped (never the whole repo): `npm run typecheck` in `apps/web`, `npx eslint <touched files>`, `npx vitest run <touched test files>`, `npm run lint:colors` when tokens or classes change.
- Every phase ends with the **route walk** (task R0.1) on production after deploy: desktop 1440 + mobile 390 screenshots of the pages the phase touched, plus the metrics table re-run; the numbers go into §11 of this file.
- Nothing is deleted: when a panel moves, the PR description carries a "from → to" table; when a route moves, `REDIRECTS` gets the entry and the redirect test covers it.
- Backend changes needed by a task are listed in §7 / the insight plan and done there; UI tasks render `LimitedState` until the data arrives, never fake values.

### 8.1 R0 — Quick wins and the measuring tool (no visual-system change yet)
| # | Task | Files | Done when |
|---|---|---|---|
| R0.1 | **Route-walk script**: add `@playwright/test`; `apps/web/e2e/route-walk.spec.ts` reads the route list from `lib/nav.ts` + a fixtures file of real ids, visits each at 1440 and 390, records height, raw-id count (`/_[A-Z]{2}_EQ\b\|[A-Za-z]_EQ\b/`), machine-name count (LabelMap misses), empty markers, "Unsaved changes" on load, `scrollWidth > innerWidth`; writes `e2e/out/route-metrics.json`. Runs against `BASE_URL` with a stored auth state the owner creates once (`npx playwright codegen --save-storage`), never committed | `package.json` (`test:routes`), `e2e/*`, `.gitignore` | script reproduces §1.1 numbers within ±5% |
| R0.2 | Fix `/methods` and `/methods/[slug]` rendering Overview in production (check the Dockerfile copies the methods markdown that `lib/methods/read.ts` reads; add a build-time assertion that every slug resolves) | `lib/methods/read.ts`, web Dockerfile, `app/(app)/methods/*` | `/methods/dividend_income` shows the method on production |
| R0.3 | Unique titles and h1 per tab: Risk ×12, Performance ×5, X-Ray ×9, Reviews; company and fund `<title>` from the resolved name (`Apple (AAPL)`) | `app/(app)/risk/**/page.tsx` metadata, `performance/**`, `xray/**`, `reviews/page.tsx`, `companies/[ticker]/layout.tsx` `generateMetadata`, `funds/[isin]/layout` | route walk: no duplicate titles; no `_EQ` in titles |
| R0.4 | False-dirty editors: baseline taken after defaults settle in `useConfigEditor`; add `"freshly loaded editor is not dirty"` vitest for every editor (event-rules, news-sources, notifications, checks + the rest) | `lib/hooks/use-config-editor.ts` + tests per `app/(app)/config/*` | route walk: 0 dirty-on-load |
| R0.5 | **LabelMap** (F39) with first maps: XBRL segment members, macro series ids, strategy rule ids, job keys, provider names; humanise fallback | `lib/labels/*.ts` (+ tests), used in segment bars, macro, inbox rule text, engine ledger, jobs | no `*Member`, `commodity_*`, `rule:` strings on those pages |
| R0.6 | Entity names instead of ids in headings and lists on the worst pages: reports h3 (`AppleAAPL_US_EQ`), insights, holding page, overview allocation, valuation banner, autopilot, inbox | resolver from holdings/reference API, `components/insight/*`, `overview-view.tsx`, reports components | raw-id count on those routes −90% |
| R0.7 | `LimitedState` (F33) and apply it to: one-link knock-on Sankey, all-dash correlation matrix, empty valuation history box, 25-cell Unknown sensitivity grid, BandGauge without marker, three 0% return-completeness bars | `components/insight/limited-state.tsx`, the six call sites | no empty chart frames on Overview, Risk, Valuation, company Valuation |
| R0.8 | P18 colour fix now (cheap, high impact): candles up = `gain`, treemap heat scale gain↔loss diverging, scorecard lifting/holding-back bars gain/loss | `components/charts/candle-chart.tsx`, `components/portfolio/portfolio-treemap.tsx`, `tokens.css` heat scale, `scorecard-panel.tsx` | screenshot review |
| R0.9 | Copy and noise: one Risk caveat banner; decode HTML entities in headlines; Contribution warning collapsed to "25 holdings couldn't be attributed"; "Buy before Buy before" sentence; Autopilot journal prompts batched into one card | risk view, news formatter, contribution card, dividend sentence builder, autopilot list | screenshot review |

### 8.2 R1a — Visual system re-skin (tokens first, then shell)
| # | Task | Files | Done when |
|---|---|---|---|
| R1a.1 | Fonts: **IBM Plex Serif** (display; already installed) for titles, panel titles, KPI figures, feed headlines; **IBM Plex Sans** (add `@fontsource/ibm-plex-sans` 400/500/600) for body; IBM Plex Mono kept for code/ids/cron only. Remove Barlow and Barlow Condensed | `app/layout.tsx`, `globals.css` `@theme` | no Barlow requests in network log |
| R1a.2 | Tokens: charcoal dark palette as default (`page`, `surface-1/2/3`, three text levels), light palette redesigned to match; colour roles of §2.1 incl. purple forecast, magenta fair value, revenue/earnings, risk gradient, warn; radius scale (`--radius-card: 12px`, controls 8 px, chips full); remove the "every radius resolves to 0" rule; shadows none, surface steps only | `app/tokens.css`, `globals.css`, `lib/palette.ts` + `palette.test.ts`, `lint:colors` rules | `lint:colors` green; palette tests updated |
| R1a.3 | Theme default: dark unless the user picked light (next-themes `defaultTheme="dark"`), keep toggle + CVD mode | `components/theme-provider.tsx`, `theme-toggle.tsx` | fresh session opens dark |
| R1a.4 | Card primitive: `Blueprint` → `Card` with rounded surface, no crosshair corners, no outer border; keep the export name as an alias during migration, then codemod usages | `components/ui/blueprint.tsx`, `components/ui/card.tsx`, all importers (codemod) | no crosshair corners anywhere |
| R1a.5 | Typography components: `PageHeader` (serif H1, sentence-case eyebrow, one-liner, actions), `PanelTitle` (serif + `i` tooltip), drop all-caps mono eyebrows | `components/page-header.tsx`, new `components/ui/panel-title.tsx` | screenshot review on 10 pages |
| R1a.6 | Gallery page `/dev/gallery` (dev-only, excluded from prod nav) rendering every token, card, KPI tile, badge and chart in dark, light and 390 px | `app/(app)/dev/gallery/page.tsx` guarded by env | owner signs off the look here before R1b |

### 8.3 R1b — Components
| # | Task | Files | Done when |
|---|---|---|---|
| R1b.1 | `KpiTile` (F36) + `AnswerStrip` update: serif figure, caption, left rule/arrow, `VerdictLine`, optional sparkline; 4-up / 2×2 | `components/insight/answer-strip.tsx`, new `kpi-tile.tsx` | vitest snapshot per state |
| R1b.2 | Chart kit defaults (P11, §2A.5): zones API, reference-line API, 3-5 ticks, hover card, range chips, series chips, expand-to-full-screen, `…` menu with CSV | `components/charts/shared.tsx`, each chart file | every chart in gallery shows them |
| R1b.3 | `ChartFooter` everywhere: wrap every chart via `ChartFrame` (title + i + chart + footer + `LimitedState` guard + Chart⇄Table) | new `components/charts/chart-frame.tsx`; call sites migrate per page phase | gallery + lint rule: chart components only render inside `ChartFrame` |
| R1b.4 | §4.1 fixes: ScoreMark blob + sentiment fill + sizes; DotStrip labels/winsorise/sign/zones; BandGauge marker-or-limited; ForecastBars range + forecast hue; CheckList ordering + unknown group; UpdatesFeed taxonomy + grouping | `components/insight/*` | vitest: ScoreMark agrees with table to the decimal; DotStrip outlier test |
| R1b.5 | `EntityName` (F34) with ScoreMark avatar, `CompactRow` (F37), `InboxItem` (F35), `SideDrawer` usage hook `useRowDrawer`, `GroupedTable` with view presets (generalise holdings presets into `lib/views/presets.ts`), `StatusMatrix` (from the Activity job heatmap), `Badge` (LIMITED DATA, ESTIMATED, STALE, n/a), `PromptCard`, `SettingsLink` (F40) | `components/ui/*`, `components/insight/*`, `lib/views/*` | gallery |
| R1b.6 | Remaining v2 foundations: ResponsiveTable (card list < md), FilterChips with counts + unread dots, ReasonOnce, Skeleton by shape, `usePageSummary` batched hook, formatters/NullCell | `components/ui/*`, `lib/hooks/*`, `lib/format.ts` | vitest for formatters and NullCell |
| R1b.7 | T5 `AssumptionPanel`: slider rows showing default / historical / industry beside each input, live result card, `CalcChain`, save version | `components/insight/assumption-panel.tsx` | gallery with DCF inputs |
| R1b.8 | T2 `EntityLayout`: sticky sidebar with mark, key figure, actions, numbered section list with scroll-spy; sections as sub-routes; data-status footer | `components/entity/entity-layout.tsx` | gallery with a fake entity |

### 8.4 R2 — Shell and IA step (a): new nav alongside the old
| # | Task | Files | Done when |
|---|---|---|---|
| R2.1 | `lib/nav.ts` v3 model: 7 destinations + Settings, each with tabs (href, label, title, badge source), `REDIRECTS` map for every moved route (§3.2), palette groups derived from it | `lib/nav.ts`, `nav.test.ts`, `components/command-palette.tsx` | test: every route in `app/(app)` appears exactly once in nav or `REDIRECTS`; every redirect target exists |
| R2.2 | Redirects wired in Next 16 config/middleware from `REDIRECTS` (permanent) | `next.config.*` or `proxy.ts` per Next 16 docs | route walk: old URLs 308 to the new home |
| R2.3 | Top bar per §2A.6 (logo, destinations, centred ⌘K search, notifications bell = Inbox count, Ask, avatar menu with theme/settings/sign out); market strip only on Portfolio | `components/app-nav.tsx`, `market-strip.tsx`, `app/(app)/layout.tsx` | screenshot desktop |
| R2.4 | `TabBar` (F38) on every area page with overflow "More"; per-tab title helper | `components/ui/tab-bar.tsx`, area layouts | no horizontal scrollbars under tabs |
| R2.5 | Mobile bottom bar + "More" sheet; top bar reduced; no scrolling nav | `components/bottom-bar.tsx`, layout | 390 walk: nav never overflows |
| R2.6 | Settings grouped left menu, `/config` → Strategy; help "?" menu with Glossary and Methods | `app/(app)/config/layout.tsx`, `components/help-menu.tsx` | route walk |

### 8.5 R3 — Inbox (one place for decisions)
| # | Task | Files | Done when |
|---|---|---|---|
| R3.1 | Inbox data adapter: one client-side list built from existing endpoints (inbox proposals, autopilot needs-you, escalations, review queue, relationships, disputed news, disclosure alerts, adjudication, deal parties, AI vetoes, stopped jobs) normalised to `InboxItem` (`kind`, `entity`, `why`, `evidenceHref`, `actions[]`, `createdAt`, `batchKey`) | `lib/inbox.ts` (+ tests) | vitest: each source maps; unknown kinds rejected |
| R3.2 | Inbox page (T1): KPI strip, kind chips with counts, grouped by company then kind, batch cards (`batchKey`), drawer details reusing the existing escalation/review components, swipe review mode on mobile | `app/(app)/inbox/*` | all actions available today work from Inbox (manual checklist per source) |
| R3.3 | Move "Everything else that changed" out of Inbox to Markets › News (holdings filter) | inbox view, news view | |
| R3.4 | System › Autonomy page = Engine ledger KPIs as bullet charts + Autopilot Decisions / What it handles / How sure vs right; thresholds forms → Settings › Automation › Autonomy | `app/(app)/engine-ledger`, `autopilot`, new `config/autonomy` | P20 check: no inputs on Autonomy page |
| R3.5 | Old queue routes redirect to Inbox with the matching chip preselected (`/inbox?kind=escalation`), detail routes (`/escalations/[id]`) open Inbox with the drawer open | `REDIRECTS` | redirect test |

### 8.6 R4 — Company and fund pages (T2)
| # | Task | Files | Done when |
|---|---|---|---|
| R4.1 | Inventory: list every panel on `/holdings/[t]` (`holding-view.tsx`, `holding-overview.tsx`) and on the 16 company tabs; assign each to a §5.3 section; commit the table into the PR | — | table reviewed |
| R4.2 | `companies/[ticker]/layout.tsx` → `EntityLayout` with sidebar (ScoreMark 120, price, fair-value chip, position card from holdings API when held, actions, ticker switcher) | `app/(app)/companies/[ticker]/layout.tsx`, `components/company/company-shell.tsx` | screenshot |
| R4.3 | Sections 1-9 as sub-routes; move panels per R4.1; each section opens with "n of 6 checks" + key figures + its CheckList rows; "See all checks" dialog | `app/(app)/companies/[ticker]/*` | every section ≤ 2.7k px for AAPL |
| R4.4 | Valuation section rebuilt answer-first; scenario editor into `AssumptionPanel` | `companies/[ticker]/valuation/*` | no Unknown grid; basis named |
| R4.5 | Overview additions: shareholder returns vs industry/market, volatility vs industry, competitors cards, release question chips | `companies/[ticker]/overview-view.tsx` | |
| R4.6 | `/holdings/[t]` and old tab URLs redirect; holdings rows, overview lists, inbox items link to the company page | `REDIRECTS`, link sites | redirect test; holding page gone from route walk |
| R4.7 | Fund page on `EntityLayout` with the five-axis fund mark (axes defined in `lib/funds/mark.ts`, unknown axes as gaps) | `app/(app)/funds/[isin]/*` | screenshot |
| R4.8 | Other T2 pages adopt `EntityLayout`: research report, monthly review, digest, news story, escalation (drawer + page), job run, 13F manager, evidence | respective `[id]` routes | route walk heights ≤ 3 screens |

### 8.7 R5 — Portfolio area
Order: Overview (§5.1) → Holdings (§5.2) → Income (§5.4) → Valuation (§5.5) → Performance (§5.7) → Allocation (§5.6) → Risk + Decide (§5.6). Per page the same five tasks:
1. Answer strip with `KpiTile`s and verdict sentences (backend sentence where it exists, else a deterministic frontend template in `lib/verdicts/*` with tests).
2. Hero in `ChartFrame` with the §5 visual, zones and reference lines.
3. Secondary panels re-laid as cards (`items-start`), moved panels per the §5 "move out" lists with redirects/anchors.
4. Tables → `GroupedTable` / `ResponsiveTable` with presets, pagination > 50 rows, drawer on row click.
5. Mobile pass (2×2 KPIs, `CompactRow`, chip tabs) and route walk.
Done when each page ≤ 2.7k px desktop, 0 raw ids, every chart framed, 390 px no page scroll, holdings list ≤ 3k px on mobile.

### 8.8 R6 — Research, Markets, Briefings
Same five tasks per page, plus: T3 feed component for News feed/wire/stories, disclosure alerts and the updates feed (typed chips, unread dots, day groups, "n similar", structure grouping, translated titles; virtualised list); T4 screener layout for Discover/Screens/Evidence screen/Funds research/13F list (idea chips, plain-English box filling chips, filter cards, `SliderRange` dialog, results with ScoreMark rows, grid/list, "Alert me on new matches"); Theses guided start + workup as "New thesis" flow; Macro LabelMap + small multiples with axes; Filings landing summary + StatusMatrix; Briefings list timeline + T2 report pages. Starter-screen editing → Settings (P20).
Done when route walk shows ≤ 2.7k px per page (feeds paginate/virtualise), news SVG count < 60, 0 raw ids.

### 8.9 R7 — System and Settings; IA step (b)
- System pages per §5.12 with `CheckList` rows for release checks / verification gates / data-quality issues / endpoint access, `StatusMatrix` for job runs / source health / coverage, plain status for provider errors with Details collapsed, presets on large tables.
- Settings editors to T6 master-detail with the summary visual per §5.0 (Jobs week timeline, Event rules severity matrix, Checks live impact count, Strategy donut, Budget burn-up on BandGauge, News sources trust bars, Notifications expected-alert heat); Prompts read view; Research profiles accordions.
- Switch the nav to v3 only (remove old cluster menus); command palette from nav.
Done when editors ≤ 3 screens and ≤ 60 visible inputs; 0 dirty-on-load; old nav gone; redirect test green.

### 8.10 R8 — Quality pass
A11y (table twin + aria-label on every chart, no colour-only status, focus order in drawers), dark and light screenshots of every page type, perf (LCP < 2.5 s on a mid phone profile, one summary request per page, lazy sections), Lighthouse a11y ≥ 90 on Overview, Holdings, Company, Inbox, Discover; full route walk at 360/390/768/1440 with results in §11.

### 8.11 Order and parallelism
R0 → R1a → R1b → R2 → R3 → R4 → R5 → R6 → R7 → R8. R0 can start immediately and run alongside the insight plan's backend phases. R3 and R4 can run in parallel (disjoint files) once R2 lands. R4 precedes R5 because holdings rows, Overview lists and Inbox items link into the company page. The §7 data defects are filed as issues at the start of R0 and tracked in the insight / empty-data plans; R5 pages ship with `LimitedState` where a defect is still open.

## 9. Success criteria
- Longest page ≤ 2.7k px at 1440 (today 50k); no list renders > 50 rows unpaginated; no editor shows > 60 inputs.
- Exactly one page holds owner decisions (Inbox); no report page contains a settings form.
- 0 raw instrument ids and 0 raw machine names in headings, titles, rows and chart labels (automated check on the route walk).
- Every chart: table twin, footer (Data · Learn that resolves · as-of · source), meets its `minData` or shows a one-line limited state; no empty frames.
- One sign colour system across all pages (P18) verified on treemap, candles, contribution, checks, scorecard.
- ScoreMark appears in every company list row and header and agrees with its table twin.
- 390 px: no page-level horizontal scroll on any route; nav is a bottom bar.
- All editors load clean; every old URL redirects; coverage matrix §3.2 checked off route by route.
- The house style (§2A) is on every page: no crosshair cards, no Barlow, serif titles and KPI figures, rounded surfaces, dark default; every page matches one of the six page types (§5.0); every catalog row in §5.14 is either shipped or listed as a deliberate skip.

## 10. Decisions (all made; owner delegated every call on 2026-10-16)

**Look**
1. The Simply Wall St design language is the house style for every page (§2A); the Ledger look (blueprint crosshair cards, square corners, condensed headings, all-caps mono eyebrows) is retired.
2. **Dark charcoal is the default theme**; light is redesigned to the same system and stays one click away; CVD mode stays.
3. Fonts: IBM Plex Serif (display), IBM Plex Sans (body, tabular numbers), IBM Plex Mono (code, ids in Details, cron). Barlow and Barlow Condensed are removed. One superfamily keeps the numbers consistent across faces.
4. Brand colour stays StewardAI's own accent (not amber); amber is used only for warning/limited chips.
5. Card radius 12 px, controls 8 px, chips full; no outer borders on cards; no shadows.
6. Green/red is sign everywhere, blue (accent) is "you"; forecast purple, fair value magenta stepped, risk blue→purple→red (P18, §2.1).

**Structure**
7. Seven destinations + Settings gear (v2 §3.1); Briefings stays top-level; Filings stays under Markets.
8. **Autopilot and Escalations fold into Inbox**; their audit/statistics views go to System › Autonomy (P19).
9. **Engine-ledger thresholds and Discover starter-screen editing move to Settings** (P20).
10. **One company page** with a sticky sidebar and 9 numbered sections mirroring the reference stock report (Overview, Valuation, Future, Past, Health, Dividend, Ownership & management, Research & thesis, Network & reference); `/holdings/[t]` and the 16 old tab URLs redirect; Checks become the "See all checks" dialog plus per-section check rows.
11. Every entity (fund, report, review, digest, story, escalation, job, 13F manager, evidence) uses the same T2 layout.
12. Funds get a five-axis fund mark: Cost · Diversification · Tracking · Size · Data quality, on the ScoreMark component, unknown axes as gaps; defined in `lib/funds/mark.ts` with tests and published in Methods.
13. Overview keeps 7 panels; diagnostics go to Data quality, Allocation and Performance.
14. Holdings default preset is **Check-in** (what changed first, as in the reference); "Holding" stays one click away; the choice is remembered.
15. Decide (what-if, rebalance, pre-trade, deposit) is one Portfolio tab with a mode switch, not four Risk tabs.
16. Glossary and Methods live in a "?" help menu, not the nav.

**Behaviour**
17. Charts below their minimum data do not render; the slot shows the reason and what fills it (P17).
18. Feeds hide structural record changes by default, grouped per company per day (P21).
19. Unknown/not-applicable checks are grouped once at the end (P22); a not-applicable axis is a gap, never zero.
20. When several fair values exist, the headline basis is the user's own scenario if saved, else analyst consensus if ≥ 3 analysts, else the default DCF; the basis is always named next to the number, and the Valuation section shows all bases on one strip.
21. Every list opens a side drawer on row click; the full page is one click further.
22. Every large table offers 2-5 presets + Custom; the choice persists per page (versioned config for holdings as in insight I9, localStorage for other pages).
23. Verdict sentences: the backend writes them where a method exists (checks, risk, valuation); elsewhere a deterministic frontend template in `lib/verdicts/*` with tests; no AI-written sentences beside numbers.
24. Foreign headlines show the stored translation as the title with an original-language chip; untranslated items show the original with a "Translate" action.
25. Community features from the reference (community fair values, narratives by others, likes) are not built; their slots show the owner's own scenarios and thesis.
26. Mobile is a first-class target: bottom bar, 2×2 KPI tiles, compact rows, entity sidebar becomes a section chip row.

**Process**
27. IA ships in two steps: new nav and redirects alongside the old (R2), old nav removed in R7.
28. The visual re-skin (R1a) lands before page restructures so every later PR is reviewed in the final look; the owner signs off the gallery page once before R1b.
29. Playwright route walk (`npm run test:routes`) is the acceptance tool for every phase; auth state is created by the owner and never committed.
30. `ux_ui_redesign_v2_plan.md` remains the reference for its unchanged sections; where this plan differs, this plan wins. No open questions remain.

## 11. Results log
Filled in at the end of each phase from `e2e/out/route-metrics.json`: date, phase, longest page, raw-id total, dirty-on-load count, 390 px overflow count, Lighthouse a11y scores.

**2026-10-16 — R0 to R5, R6 started (code only, not yet measured).** Typecheck, eslint, `lint:colors` and vitest are run per batch. The route walk has not run: it needs the owner's auth state and a configured `.env`. Fill in the metrics from the first walk after deploy. Open items carried forward:
- R0: `/methods/dividend_income` to be checked after deploy (methods are synced at prebuild).
- R1b: the `/dev/gallery` page; CompactRow, StatusMatrix, ResponsiveTable, ReasonOnce and Skeleton as shared primitives; EntityLayout lives in `components/company` and is not yet generic. The ~300 `text-[10px]/[11px]` and uppercase usages still need a sweep.
- R3: no mobile one-per-screen review mode. "AI decisions to veto" and "Jobs stopped" queues are not in the Inbox yet.
- R4: see `r4_moved_here.md`. Not built: shareholder returns, volatility and competitor cards (no endpoint), per-section key figures, the data-status footer, and the analyst-mean and thesis-target dots on the fair-value strip.
- R5: Allocation and Decide are untouched. Also not built: the Overview zone shading, the treemap in Allocation, the Performance waterfall and underwater charts, and calendar-heatmap transactions.
- R6: done for Briefings (quiet-day folding, titles), Theses (strip, guided start), Macro (groups), News (type chips, day headers, Disputed to Inbox) and the Journal strip. Not done: the Discover T4 rework, the Filings landing summary, 13F treemap, research report TOC rail, and the T3 feed component for wire, stories and alerts.

**2026-10-16 — open items closed (code only, not yet measured).**

New backend reads, owner-only, no migrations. Tests in `tests/test_company_page_reads.py` are pure-function; the SQL has not run against a database yet.
- `GET /companies/{ticker}/summary`
- `GET /companies/{ticker}/market-context`
- `GET /companies/{ticker}/fair-values`
- `GET /portfolio/performance/drawdown`
- `GET /portfolio/performance/waterfall`
- `GET /portfolio/xray/allocation`

Closed:
- R1b: `/dev/gallery` (404 in production unless `NEXT_PUBLIC_DEV_GALLERY=1`). The shared primitives are in `components/ui` and `components/charts`: Skeleton, CompactRow, ResponsiveTable, ReasonOnce, StatusMatrix (B23), LorenzChart, Dumbbell and ActivityCalendar. The type sweep is done: `text-[10px]`/`text-[11px]` became `text-xs`, and label `uppercase` and tracking are gone outside chart SVGs and code inputs. `BlueprintCorners` now renders nothing. The `hover:bg-border-soft` hovers are now `hover:bg-surface-muted`.
- R3: review mode, one item per screen with swipe. The Inbox now has "AI decisions to check" and "Stopped jobs" queues.
- R4:
  - The company shell reads `/summary` instead of the whole holdings list. It shows the day change and the stored fair value until Valuation computes one.
  - Overview gains the market-context panel: shareholder returns, a volatility scale, and competitor cards with marks.
  - The valuation strip adds the analyst mean, thesis target and peer multiple, plus the analyst range.
- R5:
  - Allocation opens with a sector → industry → holding tree and a concentration curve. The effective-holdings gauge carries a verdict.
  - Performance gains the waterfall and underwater charts.
  - Transactions gains an activity calendar.
  - Decide becomes a four-mode switch (the Risk tab strip no longer repeats it). Rebalance gains a before → after Dumbbell.
  - The watchlist empty state links to Discover. KpiStrip spans an odd last tile on mobile.
  - Remaining `/holdings/{ticker}` links now point at `/companies/`.
- R6:
  - Discover: idea cards with marks, results first, list or cards, and advanced filters in a dialog.
  - Filings landing summary.
  - 13F: list and manager treemap.
  - TOC and sources rails on research, digest and monthly review.
  - Shared feed list for wire and stories.

Still open:
- 13F per-position change bars (the API has no previous-quarter position).
- Overview hero lead/lag zone shading.
- Per-section key figures and the data-status footer on company sections.
- `screens/` is untouched.
- Monthly review keeps its own source sidebar.
- The shell still makes its overview and checks reads, because child pages consume them through context.
- Fund mark tooltips.
- EntityLayout is still under `components/company`.

**2026-10-16 — remaining items, R7, R8 and the first local walk.**

New backend, owner-only, no migrations; pure-function tests pass, but the SQL has not run against a database:
- `POST /checks/preview`: a live before/after count for the Checks editor.
- 13F positions now carry the previous quarter's shares, a change class and the share change, plus up to 10 sold-out positions.

Closed from the list above:
- 13F per-position change bars, moves, and the "Shares since last quarter" column.
- Lead/lag shading on the Overview hero.
- Section key figures with the data-status footer.
- The `screens/` T4 rework.
- Monthly review sources by domain.
- Fund mark axis names.
- EntityLayout moved to `components/layout`.
- The shell keeps its overview and checks reads on purpose, because child pages consume them through context.

R7 (Settings and System):
- Every config editor follows T6: a summary first, details collapsed, the save bar only when dirty.
  - Theses is master-detail.
  - Research profiles are accordions.
  - The exchange calendar shows upcoming closures.
  - Autonomy shows threshold tiles.
  - History shows timelines per domain.
  - Jobs has a 7-day timeline and schedules grouped by job group.
- System pages lead with KpiStrip and StatusList. Activity uses ResponsiveTable, with a 14-day run grid grouped by job group and cost by job. The Verification fetches run once.

R8 (first walk, 2026-10-16, local dev against the production API through a CORS proxy, owner logged in). Fixed during the walk:
- Panel kickers now sit under the title as a context line, never above it. `PageHeader` no longer renders its cluster kicker.
- The header sync label is shortened to "Synced 6 h ago" and the search box is fixed in size, so the 1440 px header no longer overflows.
- Contribution shows the 6 biggest lifts and drags with the rest folded into "Other". It uses names, not tickers.
- The updates feed has unique keys, no "X → X" non-changes, and no instrument ids in headlines.
- `formatDate` accepts full timestamps, which fixes "Invalid Date".
- The legal group shows 8 rows with a "Show more" toggle, and confirmed links no longer use the gain colour.
- An unscored company mark draws unknown axes instead of an empty box.
- DotStrip draws at the container's width, so labels stay 11 px. Only unlabelled dots get chips.
- The screener funnel uses outcome colours.
- Inbox batch cards list each company once and have no arrow.
- `PanelHeading` wraps its controls below the title on phones.
- The Jobs page went from about 6,400 px to 1,350 px.

R7, closed after the walk:
- The engine-ledger gates are a Guardrails StatusList. It shows one row per decision kind with precision against the bar and links to change the bar. The ledger rows show short tickers and statuses in words.
- Data quality shows one block per issue kind: the count and share, the fix said once, and the items collapsed.
- Data sources: the coverage matrix is one collapsible row per provider, endpoint access shows only what needs attention, and provider and endpoint names are in words.
- AI decisions shows one row per company and quote, with a chip and a reject button per input.
- Between lg and xl, the header actions are icon-only with aria-labels, so the header fits at 1040 px.

Not yet measured:
- Lighthouse.
- The full route walk at 360/768 px. 390 px was spot-checked with no horizontal overflow on Overview.
- Pages that call this branch's new endpoints (`/summary`, `/market-context`, `/fair-values`) show their fallbacks against the production API until deploy.
