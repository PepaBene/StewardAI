# StewardAI: insight features implementation plan

Date: 2026-10-15. Scope: `apps/backend`, `apps/web`, `supabase/migrations`. No code is changed by this document.
Source: `docs/research/simplywallst_feature_and_analysis_review.md` (what to build and why) and
`docs/research/simplywallst_visualization_catalog.md` (how it should look). Display work in this plan is specified
through the foundations F18-F32 defined in `ux_ui_redesign_v2_plan.md` §4b; this plan names them and never redefines them.
Companion plans: `morningstar_features_implementation_plan.md` (M phases, mostly built), `quant_risk_implementation_plan.md`
(Q phases), `ux_ui_redesign_v2_plan.md` (information architecture and visual language).

Phase prefix: **I** (insight). Task ids are `I<phase>.<n>`.

---

## 1. Goal and the line this plan does not cross

Give the owner, for every holding and for the portfolio, a **transparent, repeatable verdict layer** (five-axis checklist and
risk checks, written as sentences), a **dividend income view**, a **fair-value-versus-price view that keeps history**, and
faster ways to find and read things (holdings presets, plain-English screens, a typed feed). Everything stays inside
StewardAI's rules: figures are computed in backend code from versioned observations, an LLM may only explain or parse, and a
missing input is shown as missing.

Not in scope, and not to be added later under this plan: social features (community narratives, likes, follows), broker
sync, proprietary scores that cannot be explained from stored inputs, and any claim of "all global stocks" coverage.

## 2. Corrections to the review after a code audit (2026-10-15)

The review recommended some things StewardAI already partly has. Verified in code:

| Review item | What exists | Consequence for this plan |
| --- | --- | --- |
| Item 1 dividends: "no forward view" | `public.corporate_action` stores `cash_dividend` with `ex_date`, `record_date`, `payment_date`, `cash_amount`, `currency`, `status` (suggested, confirmed, rejected). The job `twelve_data_corporate_actions` (weekly, Saturdays 10:50) writes Twelve Data dividends as *suggested* actions; a few holdings per run inside the Basic plan credits; a plan denial latches the endpoint paid-only. Trading212 dividends are in `public.transaction` (`type='dividend'`). Ratios `payout_ratio`, `dividends_per_share_paid`, `total_shareholder_yield`, `buybacks_per_share` exist in `analytics/ratio_set.py` | Phase I1 builds the **read model and forecast** on existing tables; the open work is coverage, confirmation workflow and a second source, not a new schema from scratch |
| Item 2 checklist: "no stock-level pass/fail" | `discovery/composite.py` has `red_flags()` returning `Flag(id, label, state in flag/clear/unchecked, detail)` from `RED_FLAG_RULES`, a versioned quality-value-momentum composite (`composite_qvm`) and `analytics/thresholds.py` (`THRESHOLDS`) | Generalise `Flag` into a `Check` with an axis and a pass/fail/not-applicable/unknown state; reuse `THRESHOLDS`; do not build a second rule system |
| Item 5 scenario calculator | `company/valuation.py`: explicit-year DCF with a discount-rate build-up, reverse DCF, bear/base/bull (`SCENARIO_NAMES`), sensitivity grid, versioned `company_valuation_scenario` (assumptions jsonb, `method_version`, `input_hash`, `note`) and `valuation-view.tsx` | Phase I6 adds a **second method** (earnings-multiple path with a printed calculation chain) to the same table and view; it does not replace the DCF |
| Item 9 natural-language screener | `discover/filter-builder.tsx`, `screen_definition` plus versions and `screen_run`, `discovery/metrics.py` (about 40 metrics grouped identity, financials, ratios), `ops/opportunities.py` (a stock that newly passes a saved screen becomes a "Needs you" line), `llm/` with pydantic structured output (`req.schema`) | I10 adds only the text-to-filter step, metric parity, a 7-day new-match counter and curated templates |
| Item 12 correlation | `risk-view.tsx` already draws a correlation matrix and rolling benchmark correlation | I12 changes presentation (triangle, diverging scale), not computation |
| Holdings views | `holdings-view.tsx` has table, charts, heatmap switch only | I9 is still needed |

Not found in code and therefore genuinely new: forward dividend schedule and calendar, income share by quality tier, stored
fair-value history, analyst consensus snapshots (the FMP client reads `profile` and `analyst-estimates` only, no price
target), per-axis stock scores, a portfolio scorecard, a headline risk score, a typed update feed, a chart provenance
footer.

A short corrections note is added to the top of the review file so the documents do not disagree.

## 3. Engineering rules for every task

1. Backend computes; the browser formats; an LLM parses or explains. The only LLM-in-the-loop features here are I10 (parse a
   sentence into a validated filter object) and I13 (summarise a cited document); both validate output against a schema and
   never execute free text.
2. Every scored or aggregated output uses the existing `AnalyticsResult` contract (`steward/analytics/contract.py`):
   `value`, `unit`, `as_of`, `known_at`, `method_version`, `sources`, `coverage`, `warnings`. `null` is never replaced by zero.
3. **Four-state checks.** A check returns `pass`, `fail`, `na` (does not apply, such as a dividend check for a non-payer) or
   `unknown` (input missing). Scores count `na` out of the denominator and show it ("4 of 5 applicable"); `unknown` lowers the
   shown coverage, never the score. This avoids the review's observed flaw (a company with a safe, growing dividend scoring
   0/6).
4. Thresholds live in one place (`analytics/thresholds.py` for numeric limits, a versioned method table for sentences), and
   every verdict names the basis next to it ("vs peer median of 6", "vs your base scenario v3").
5. Peer medians require `THRESHOLDS.min_peers_for_median` comparable peers on comparable periods; otherwise the check is
   `unknown` with the reason, never a global average.
6. History-bearing tables are append-only with `known_at`; a later correction adds a row, it never rewrites what the owner saw
   (matches Morningstar plan rule 4).
7. Free-tier endpoints are runtime constraints: record access state (`complete`, `partial`, `unavailable`, `paid_only`) per
   source and show it, as the data-sources page already does.
8. UI: follow `ux_ui_redesign_v2_plan.md` page grammar (answer strip, one hero visual, Chart ⇄ Table twin). Use only tokens
   from `apps/web/src/app/tokens.css` (`lint:colors` forbids literals; `bg-surface-muted` for table headers). Load the
   `frontend-design` skill before writing UI and the `dataviz` skill before any chart.
9. Each phase ends with: migrations applied locally, `uv run pytest <touched tests>`, `ruff` and `mypy` on touched backend
   files, `npm run gen:api` after OpenAPI changes, `tsc` and lint in `apps/web` for touched files only, and one screenshot per
   new screen at 1440 and 390 px. Run checks once per batch of edits.
10. Everything is owner-only behind existing auth; no new write route touches broker holdings.

## 4. Feature inventory and where each is built

| Review item | Feature | Phase |
| --- | --- | --- |
| 1 | Forward dividend income, calendar, yield on cost, quality tiers | I1 (data and model), I2 (screens) |
| 2 | Five-axis checklist and risk checks | I3 |
| 3 | Portfolio scorecard, lifting and holding-back lists | I4 |
| 4 | Portfolio valuation against fair value, stored history, stale flag | I5 |
| 5 | Earnings-multiple scenario with calculation chain | I6 |
| 6 | Holdings view presets, custom view, side drawer, Check-In view | I9 |
| 7 | Peer bars, industry histogram, multiple history, fair multiple | I7 |
| 8 | Analyst consensus, agreement, target track record | I8 |
| 9 | Plain-English screener, metric parity, new-match counter, templates | I10 |
| 10 | Returns breakdown: currency component, closed positions, splits, CSV | I11 |
| 11 | Diversification: sector sankey, geography, concentration verdict | I11 |
| 12 | Headline risk score, verdict lines, triangle correlation | I12 |
| Tier 3 | Typed feed, earnings-release summary and questions, ownership by holder type, CEO pay panel, provenance footer and Data toggle, watchlist fair-value column | I13 |
| All | Foundations, data clocks, validation, release | I0, I14 |

## 5. Delivery order and relation to the other plans

1. **I0 first, and immediately**: it starts recording the histories that cannot be reconstructed later (fair values, consensus
   snapshots, dividend schedule snapshots) and measures coverage that decides what I1, I3, I8 may claim.
2. Then in this order: I1, I3 (backend only, in parallel), I2, I4, I5, I6, I9, I10, I7, I8, I11, I12, I13, I14. I8 depends on
   weeks of recorded snapshots, so its screen ships late by design.
3. Q-phase interaction: I12 reads the Q1/Q2 risk outputs and must not recompute them; I5 and I6 feed Q8's decision loop only
   through stored fair values, no new decision logic here.
4. UX interaction: new screens land in the v2 slots (Portfolio › Income, Portfolio › Valuation, Portfolio › Overview scorecard,
   Companies › Overview checks). Until the v2 navigation ships they are reachable under the current routes with the same
   component set; no screen is built twice. UX v2 Phase 2 (foundations F18-F32) is a hard dependency of I2, I4, I5, I7, I9, I12.

## 6. Phase I0: gates, clocks and foundations (backend first)

Purpose: decide what is real, then start the clocks.

| Task | Work | Files | Done when |
| --- | --- | --- | --- |
| I0.1 | **Coverage audit.** For each of the 30 holdings measure: confirmed `cash_dividend` rows and years of history; ex-date and pay-date completeness; Trading212 dividend transactions matched to a corporate action; statements with `dividends_paid`, `dividends_per_share`, shares, debt, interest, accruals inputs; FMP estimate coverage (13 of 24 per the 2026-10-07 report); peer-median availability (>= 5 peers). Write the result to `docs/data/insight_coverage.md` and as a read endpoint feeding the existing data-quality cockpit | `steward/ops` (new `insight_coverage.py`), `api/` data-quality route | The report lists, per holding and per planned check, `ready`, `partial`, `unavailable` with the reason |
| I0.2 | **Source access tests** (follow `docs/data/provider-access.md` method) for a second dividend source (Yahoo chart `events=div` through `governed_http`; note `sources/yahoo.py` today reads closes only and states they are not dividend-adjusted) and for a consensus price target on free plans (FMP price-target endpoints, Finnhub, Twelve Data). Record verdicts in the provider access store; no feature assumes an endpoint that fails the test | `steward/sources/yahoo.py`, `fmp.py`, `finnhub.py`, `provider_jobs.py` | Each candidate endpoint has an access verdict with a date |
| I0.3 | **Fair-value recorder.** Migration `fair_value_point`: `id`, `owner_id`, `ticker`, `basis` (`dcf_base`, `dcf_bear`, `dcf_bull`, `multiple_base`, `thesis_target`, `peer_multiple`, `consensus_target`), `value`, `currency`, `as_of`, `known_at`, `scenario_id`/`scenario_version` (nullable fk to `company_valuation_scenario`), `method_version`, `input_hash`, `source`; unique `(owner_id, ticker, basis, as_of)`. Daily job `fair_value_snapshot` writes one row per holding per available basis using existing `company/valuation.py` outputs; no new valuation logic | `supabase/migrations/<ts>_fair_value_point.sql`, `steward/valuation/fair_value.py`, `jobs/registry.py`, `config/jobs.py` | After a run, holdings with a saved scenario have rows; re-run is idempotent |
| I0.4 | **Consensus recorder.** Migration `consensus_snapshot`: `ticker`, `as_of`, `source`, `analyst_count`, `target_mean/high/low` (nullable), `eps_next_fy`, `revenue_next_fy`, `known_at`, `access_state`, `raw`. Job `consensus_snapshot` (weekly at first) writes what the tested endpoints return; absence is recorded as access state, not as zero | migration, `steward/sources/consensus.py`, registry | Rows accumulate weekly; coverage is visible |
| I0.5 | **Dividend schedule snapshot.** Table `dividend_forecast_snapshot` (`ticker`, `as_of`, `schedule jsonb`, `method_version`, `known_at`) so a forecast made today can be compared with what was actually paid (I1.6) | migration, `steward/dividends/` | Written by I1.4 job |
| I0.6 | **Tokens.** Add semantic aliases to `tokens.css`: `--chart-forecast` (estimate, analyst), `--chart-fair-value`, `--tier-low`, `--tier-medium`, `--tier-high` mapped to existing `loss`, `warning-signal`, `gain` roles, `--score-mark-*` ramp. Light and dark values; `npm run lint:colors` passes | `apps/web/src/app/tokens.css`, `lib/palette.ts` | Palette test covers the new roles in both themes |
| I0.7 | Add the F18-F32 components from UX v2 §4b in the order that unblocks features (see §15). Tracked in the UX plan; this task is the gate | UX v2 plan | Components exist with table twins and tests before a phase uses them |

Verification: coverage report reviewed by the owner; recorders produce rows on two consecutive days; `pytest` for the
recorders with idempotency and "endpoint paid-only" cases.

## 7. Phase I1: dividend income engine (backend)

Builds on `corporate_action`, `transaction`, `lots`, statements. Method name `dividend_income` v1.

| Task | Work | Files | Done when |
| --- | --- | --- | --- |
| I1.1 | **Event reconciliation.** Match each Trading212 dividend transaction to a confirmed `cash_dividend` by ticker, listing and pay date window; carry `received_amount`, `declared_amount`, withholding gap, currency and FX used. Unmatched on either side is shown, not dropped | `steward/dividends/reconcile.py`, `reference/returns.py` reuse | A holding page lists received payments with their declared counterpart or "no declared record" |
| I1.2 | **Confirmation workflow.** Twelve Data actions arrive as `suggested`; add an Inbox queue item type "dividend events to confirm" with the evidence row and bulk confirm for a ticker when amounts match the received transactions (auto-confirm rule documented, owner can turn it off). Respect the existing `status` check constraint | `api/free_sources_routes.py` (existing action endpoint), inbox scan | Confirmed rows rise without manual work for matched payments |
| I1.3 | **Second source** if I0.2 passes: Yahoo dividend events through `governed_http` into `corporate_action` as `suggested` with `source='yahoo'`; cross-check against Twelve Data and flag disagreement; US issuers also cross-check against annual `dividends_per_share_paid` from XBRL | `steward/sources/yahoo.py`, `provider_jobs.py` | Disagreements listed; both sources visible in provenance |
| I1.4 | **Forward schedule.** Per holding: declared future payments (ex-date or pay date after today) exactly as stored; estimated payments for the next 12 months from the trailing cadence (detected frequency: monthly, quarterly, semi-annual, annual, irregular) times the last amount, optionally grown by the holding's 5-year dividend growth only when the owner enables "assume growth" (default off). Every estimated row is flagged `estimated`. Convert to the account currency with the latest stored FX and say so. Irregular or special dividends are excluded from estimates and listed separately. Writes the snapshot (I0.5) and `AnalyticsResult` with coverage = holdings with a usable history / paying holdings | `steward/dividends/forecast.py`, `jobs` (`dividend_forecast`) | Golden tests: quarterly payer, annual payer, cut, special dividend, new payer (< 4 payments, `unknown`) |
| I1.5 | **Income metrics.** Trailing 12-month received; next 12-month declared + estimated; yield (price basis) and **yield on cost** (from open lots) per holding and portfolio; 5- and 10-year dividend growth CAGR; years without a cut; largest and smallest contributors (last 12 months) with share of total; income by calendar month for the last 36 months and the next 12 | `steward/dividends/metrics.py` | Reconciles to the ledger totals already shown on the Performance returns table (sum of months equals existing `returns-view` dividends column) |
| I1.6 | **Forecast accuracy.** Compare an old snapshot with realised payments (a 12-month-old snapshot against what was paid) and expose hit rate and mean error; hidden until a year of snapshots exists | `steward/dividends/accuracy.py` | Function tested with synthetic snapshots; screen shows "available from <date>" |
| I1.7 | **Dividend quality checks** (axis checks, built in I3 on this data): earnings payout, cash payout, years without a cut, 10-year DPS growth, yield versus peer median, total shareholder yield covered by free cash flow. All `na` for non-payers | `steward/checks/dividend.py` (I3) | Defined in I3 |
| I1.8 | **API.** `GET /portfolio/dividends` (answer strip figures, monthly history and forecast, contributors, tier summary, coverage), `GET /portfolio/dividends/calendar?month=YYYY-MM` (ex-dividend, payment, estimated variants), `GET /companies/{ticker}/dividends` (history, schedule, quality checks, provenance). Status and `explain` as per Morningstar build contract. Update `openapi.json`, run `gen:api` | `api/dividend_routes.py` | Contract tests with `complete`, `partial`, `unavailable` fixtures |

Acceptance: next-12-month income equals sum of declared plus estimated rows; moving a holding to `confirmed` changes the
number the same day; a holding with no history reads "no schedule, history too short" rather than 0.

## 8. Phase I2: dividend screens (frontend)

Uses F22 MonthCalendar, F23 ForecastBars, F26 ChartFooter, F28 GroupedTable, F31 VerdictLine, F19 CheckList.
Route: `/portfolio/income` (v2: Portfolio › Income tab). Company panel: Companies › Financials › Dividends.

| Task | Screen | Notes |
| --- | --- | --- |
| I2.1 | Answer strip: next-12-month income with change against the last 12 months, monthly average, current yield, yield on cost, coverage chip | One batched summary request (v2 P8) |
| I2.2 | Hero: monthly income bars for 36 months of history plus 12 months forecast; history in `--chart-line` family, estimated in `--chart-forecast`; Monthly/Annual toggle (annual shows 3 years); hover or tap pins a panel listing that month's payments (symbol, date, amount, declared or estimated) | Chart ⇄ Table twin is the same payment rows |
| I2.3 | Contributors: two ranked bar lists (largest, smallest), names not ids (F12), amount and share | |
| I2.4 | Quality tiers: three tier rows (low, medium, high) with holding count and income, one stacked share bar of income by tier, table with score mark (F18), score as "n of m applicable", growth. Link "Find quality payers" to Discover with the dividend template (I10.7) | Tier boundaries are in config, default thirds of the applicable score |
| I2.5 | Calendar: month grid with typed dots (ex-date, estimated ex-date, payment, estimated payment), legend, previous and next month, chips with ticker and amount; on mobile the month collapses to a list by default with "Open calendar" | Also shows earnings dates from the existing earnings calendar as a different dot (reuses `earnings/calendar`) |
| I2.6 | Upcoming events table (date, ticker, event, estimated total) and a "Buy before <ex-date>" line on the company page | |
| I2.7 | Company Dividends panel: DPS history chart with forecast band only when estimates exist, yield against peer median bar, payout on earnings and cash, the six dividend checks as a CheckList with sentences | |
| I2.8 | Empty and partial states: "n of m paying holdings have a schedule", missing ones listed once (F11 CoverageStrip), unconfirmed suggested rows count with a link to Inbox | |

Acceptance: sum of bars equals backend totals (unit test on the adapter); every number has a sentence or label; screenshots
at 1440 and 390; keyboard path to every calendar event; table twin shows identical rows.

## 9. Phase I3: check framework, stock checklist, risk checks (backend)

Generalise `Flag`. New package `steward/checks/`.

| Task | Work | Files |
| --- | --- | --- |
| I3.1 | **Contract.** `Check(id, axis, label, state in pass/fail/na/unknown, value, threshold, basis, sentence, inputs, section_ref, method_version)`; `AxisScore(axis, passes, applicable, unknown, max=6)`; keep `Flag` as a thin adapter so the idea pipeline and Discover red flags continue to work unchanged | `steward/checks/contract.py`, `discovery/composite.py` (adapter only) |
| I3.2 | **Sentence templates** in a versioned table, one per check and state, using the inputs ("Debt to equity (9%) is below 40%") with the unit-aware formatters shared with the frontend through the API (the backend returns the final sentence and the structured values) | `steward/checks/sentences.py` |
| I3.3 | **Value axis (6):** (1) earnings multiple at or below the peer median (EV/EBIT for loss-makers, price to sales if both negative; peer rule §3.5); (2) price at or below the base DCF scenario value; (3) price at or below the owner's selected scenario or thesis fair value; (4) PEG at or below 1.5 where growth exists; (5) free cash flow yield above the scenario risk-free rate plus a margin from config; (6) price at or below consensus target when a snapshot exists, else `unknown` | `steward/checks/value.py` |
| I3.4 | **Future axis (6), estimates required:** (1) forecast earnings growth above risk-free; (2) above peer median forecast growth; (3) forecast revenue growth above peer median; (4) EPS growth forecast positive; (5) forecast ROE at least the configured floor; (6) analyst count at least the configured minimum. All `unknown` when FMP/consensus data is missing (coverage 13 of 24 now) | `steward/checks/future.py` |
| I3.5 | **Past axis (6):** (1) five-year earnings growth positive; (2) above peer median; (3) ROE at least floor; (4) ROCE at least floor; (5) net margin up year on year; (6) earnings quality: operating cash flow at least net income or `accruals_ratio` below limit | `steward/checks/past.py` |
| I3.6 | **Health axis (6):** (1) short-term assets exceed short-term liabilities; (2) short-term assets exceed long-term liabilities; (3) debt to equity below limit or falling over five years; (4) operating cash flow covers debt by at least the configured share; (5) interest coverage at least limit, `na` for net interest earners (fixes the -705x display problem); (6) cash at least debt, or net debt to EBITDA below limit. Financial-sector issuers: checks 1, 2, 4, 6 are `na` with a stated reason | `steward/checks/health.py` |
| I3.7 | **Dividend axis (6):** the six checks from I1.7; all `na` for non-payers and for payers below a configured yield floor where "yield versus peers" is not meaningful, with the sentence saying why | `steward/checks/dividend.py` |
| I3.8 | **Risk checks (about 14, a separate list):** insider net selling over 90 days (Form 4 data, existing `insider_transaction`); share dilution year on year above limit; negative equity; interest coverage below limit; high accruals; margin decline; low liquidity (average volume against a floor); price volatility against benchmark; small market cap; negative free cash flow three years; near-term debt maturity wall (debt maturities are in the statement catalog); customer concentration (existing facts); restatement or amended filing in 12 months (`is_amendment` rows); earnings date within 14 days (informational). Each returns pass/fail/na/unknown with section reference. Reuse `RED_FLAG_RULES` entries rather than duplicating | `steward/checks/risk.py` |
| I3.9 | **Aggregation and caching.** `GET /companies/{ticker}/checks` (all five axes plus risk checks, scores, coverage, basis) computed from existing ratio and statement read models; cache by `input_hash`; method version recorded; no LLM | `api/check_routes.py` |
| I3.10 | **Thresholds into config.** Extend `analytics/thresholds.py` for numeric limits and add a "Checks" config page under Settings › Investment rules so limits are versioned and replayable like other config | `config/` new `checks.py`, `config-sidebar` entry |
| I3.11 | **Tests.** Golden examples per check with pass, fail, na and unknown fixtures; a fixture for a bank, a loss-maker, a non-payer, a net-interest earner; property test that score never counts `na`/`unknown` as pass or fail; snapshot test of every sentence | `tests/test_checks_*.py` |
| I3.12 | **Discover integration.** Expose axis scores and each check as screenable metrics (`checks_value_score`, and so on) so the screener and the saved-screen alerts can use them; follow `MetricDef` shape and coverage rule (`min_metric_coverage_for_rank`) | `discovery/metrics.py` |

Thresholds proposed as defaults (owner may change in I3.10): payout limits 75% on earnings and cash; ROE and ROCE floor 15%;
interest coverage 5x; debt to equity 40%; operating cash flow over debt 20%; dilution 5% a year; liquidity floor from the
existing liquidity metric; analyst count floor 5. These are StewardAI's own named rules, not a reproduction of anyone else's.

Acceptance: for every holding, the checklist page renders with a state per check and a sentence; the number of `unknown`
checks equals what I0.1 predicted; a non-payer shows the dividend axis as "not applicable", not 0.

## 10. Phase I4: score mark and portfolio scorecard

Backend then UI. Uses F18 ScoreMark (the compact five-axis mark, 24 px to 300 px), F19 CheckList, F20 DotStrip, F31.

| Task | Work |
| --- | --- |
| I4.1 | **Portfolio aggregation** `GET /portfolio/scorecard`: per axis, the value-weighted mean of holdings' axis score scaled to the 0-6 range over applicable checks only; weights from the portfolio valuation date; holdings with unknown axes are excluded from that axis and the coverage ("11 of 14 holdings, 86% of value") is returned. Lists per axis: best five and worst five holdings with weight and score. Method `portfolio_scorecard` v1 |
| I4.2 | **Mark everywhere.** `ScoreMark` in holdings rows (24 px), company header (64 px), Discover result rows and a card layout (large), fund pages show it only where checks are defined (stocks); unknown axes render as an open gap, never as zero |
| I4.3 | **Scorecard panel** on Portfolio Overview: large mark with clickable axis points; selected axis shows `n / 6`, a one-line explanation, and "Lifting the score" and "Holding it back" lists with thin progress bars, `n of m` and weight |
| I4.4 | **Company header verdict line:** the strongest and weakest axis in one sentence plus up to three "rewards" and three "risks" from checks (pass and fail) with "See all checks" opening the CheckList modal sorted failed first with section references |
| I4.5 | **Ranked dot strips per axis** on the Allocation tab: valuation basis tabs (DCF, scenario, multiple, consensus), forecast growth, ROE, debt to equity, forecast yield; reference lines for portfolio and benchmark; tinted zones; Chart ⇄ Table |
| I4.6 | **Idea pages.** Aggregate mark for a saved screen (mean of members' axis scores) on Discover templates and saved screens |

Acceptance: mark and table agree to the decimal; selecting an axis updates the lists; coverage line present on every aggregate;
no axis shows 0 when it is unknown.

## 11. Phase I5: portfolio valuation against fair value

Uses `fair_value_point` (I0.3), F25 StepLine overlay, F28 GroupedTable, F20 DotStrip, F31.

| Task | Work |
| --- | --- |
| I5.1 | **Read model** `GET /portfolio/valuation?basis=...`: per holding price, fair value for the chosen basis, discount or premium, age of the underlying inputs, stale flag (basis inputs older than 90 days, or latest filed fundamentals changed since the scenario's `input_hash`); portfolio value, sum of fair values over covered holdings, over- and undervalued weight, weighted upside in currency, count of stale; history series from `fair_value_point` joined to portfolio value by date with the as-of caveat (the step line starts at the first stored value) |
| I5.2 | **Basis switch (non-persisting):** DCF base, owner scenario, multiple scenario (I6), thesis target, consensus (I8). The UI recomputes from the backend with the chosen basis; nothing is saved |
| I5.3 | **Valuation tab** (v2: Portfolio › Valuation): answer strip (portfolio value, fair value, percent difference, stale count); hero chart with price series and stepped fair-value line, a vertical cursor at today; grouped tables (Overvalued n, Stale n, Undervalued and fair n) each with an explanation line and rows (name chip, return, price and premium chip, fair value with basis label, age, last event) |
| I5.4 | **Per-holding sparkline with step line** in Check-In view and watchlist (I9) |
| I5.5 | **Watchlist fair-value column:** choose a basis per watchlist item (default the latest saved scenario); "Evaluate like a holding" opens the company valuation view |
| I5.6 | **Stale alert:** an Inbox item and a notification (existing notification config) when a holding's fair-value inputs pass 90 days or fundamentals change enough to move the scenario by more than a configured percentage; one item per holding, snoozable |

Acceptance: portfolio fair value equals the sum of per-holding fair values times shares (unit test with FX); a holding with
no fair value is excluded and counted, never zero; the chart states its start date.

## 12. Phase I6: earnings-multiple scenario method

Extends `company/valuation.py` and `company_valuation_scenario`. Method `multiple_path` v1, alongside `dcf` v current.

| Task | Work |
| --- | --- |
| I6.1 | **Model.** Inputs: horizon (3-10 years), revenue growth per year, net margin per year, exit P/E, diluted share change per year, discount rate (reuse the build-up). Compute revenue path, earnings path, exit-year market cap, per-share price at exit, discounted to today; produce bear, base, bull; seed from filed history, peer P/E and the owner's thesis fields. Output carries every intermediate so it can be printed |
| I6.2 | **Storage.** Add `method text not null default 'dcf'` to `company_valuation_scenario` (migration with default so existing rows are valid) and a `method_version`-aware reader; keep `unique (owner_id, ticker, name, version)` |
| I6.3 | **Calculation chain** returned as ordered steps (`label`, `expression`, `value`, `unit`) and rendered by F29 CalcChain exactly as the model computed it; the chain is also the evidence the AI layer may cite |
| I6.4 | **Staleness and drift.** Flag a scenario when inputs are older than 90 days or when reported growth or margin over the last four quarters departs from the assumed path by more than a configured band; surfaces in I5 and the thesis review |
| I6.5 | **Reverse solve:** the exit P/E, or the growth rate, that justifies today's price under the other inputs; shown next to the existing reverse DCF |
| I6.6 | **UI** in `valuation-view.tsx`: method tabs (DCF, Multiple path), input rows each showing the owner value beside the company's history and the peer or industry value, a projection chart with past shaded and forecast curves, bear/base/bull selector, the printed chain, a comparison strip of both methods with each labelled |
| I6.7 | **Tests:** golden example reproducing a hand calculation; round trip of a saved scenario; reverse solve returns the input that reproduces the price |

Acceptance: a saved scenario reproduces the same fair value after a reload; two methods are never shown under one unlabelled
number; the headline fair value on any screen states its method and scenario version.

## 13. Phase I7: valuation pictures

Uses F20 DotStrip, hbar with reference line, histogram helper (`lib/charts/histogram.ts`), F24 FanBand, F31.

| Task | Work |
| --- | --- |
| I7.1 | **Peer comparison bars:** the key multiple (chosen by the rule PE if profitable, else PS, else PB), company highlighted, peer median line, estimated growth column; Forward multiple toggle only when estimates exist; "Edit peers" reuses the peer set editor |
| I7.2 | **Industry histogram** with the company's bin highlighted, an average line and a per-bin table. **Universe decision:** use the covered discovery universe (`discovery/universe.py`) grouped by sector, with the count shown; if the sector has fewer than `min_universe_for_percentile` members, the picture is replaced by the peer bars with the reason. No claim about a global industry |
| I7.3 | **Multiple history:** price over trailing EPS series built from price bars and XBRL EPS with the as-reported fiscal alignment; 1, 3, 5, 10 year selector; band at median plus and minus one deviation; table twin |
| I7.4 | **Fair multiple** (own method `fair_multiple` v1): a transparent regression of peer multiples on growth and margin, shown only when the peer set meets `min_peers_for_median` and the fit meets a configured R squared; reported with n, fit and an explicit "indicative" label; otherwise absent with the reason |
| I7.5 | Three-band gauge (F21) for DCF position (20% under, about right, 20% over) with markers; replaces any numeric-only verdict on the valuation page |
| I7.6 | Each picture carries the ChartFooter with data date, source and basis |

Acceptance: pictures disappear with a stated reason when data is too thin, never draw a median from two peers.

## 14. Phase I8: analyst consensus, agreement and track record

Depends on I0.2 and I0.4 and on elapsed time.

| Task | Work |
| --- | --- |
| I8.1 | **Read model:** latest consensus (mean, high, low, count), dispersion (high-low over mean and, if available, standard deviation), agreement class from configured cut points, target against price, history of targets |
| I8.2 | **Track record:** for snapshots at least 12 months old, record the price 12 months later and the hit (reached or not), expose hit rate and mean error per holding; hidden until 12 months of snapshots exist ("starts <date>") |
| I8.3 | **Chart:** price, consensus line, high-low band and a forecast cone to the 12-month target with a tooltip card (date, count, price, average target with percent, agreement text); requires at least 8 snapshots or hides itself and shows the table |
| I8.4 | **Honest coverage:** each holding shows `complete`, `partial`, `unavailable` with the source; foreign issuers likely `unavailable`; no synthetic consensus (Morningstar plan rule) |
| I8.5 | Feeds the Value check 6 (I3.3) and the consensus basis in I5 |

Acceptance: a holding with no accessible consensus shows an explicit unavailable state in every place consensus would appear.

## 15. Phase I9: holdings views, drawer and Check-In

Uses F27 SideDrawer, F25 StepLine, F18 ScoreMark, F28; extends `holdings-view.tsx` and `lib/portfolio/holdings-view`.

| Task | Work |
| --- | --- |
| I9.1 | **Column registry** with metric id, label, format, sortable, source endpoint field; one batched holdings summary request including the fields each preset needs |
| I9.2 | **Presets:** Check-In (1-day move, last event with icon and date, price against fair value, thesis or scenario title, sparkline with fair-value step line), Holding, Income (yield, yield on cost, next payment, income, quality score), Growth, Value, Score (mark and five axis scores), Fundamentals (net margin, past and future growth, debt to equity, ROCE, ROE), and **Custom** (column picker, drag order) |
| I9.3 | **Persistence:** presets and the custom view stored as a versioned config document `holdings_views` (history and export for free, same pattern as other config); selected preset remembered per owner |
| I9.4 | **Side drawer:** row click opens a right panel with tabs Overview, Position, Updates, Notes and a "Full page" button; keyboard accessible; becomes a full-screen sheet on mobile |
| I9.5 | **Mobile:** each preset renders as the card list from F `ResponsiveTable` with the preset's two or three most important fields on the first lines |
| I9.6 | **Notes:** stock notes are free text per ticker (new table `holding_note` with `owner_id`, `ticker`, `body`, timestamps), shown in the drawer, searchable in chat tools; no AI rewriting |

Acceptance: switching a preset does not refetch more than one summary; custom view survives reload; every preset has a table twin.

## 16. Phase I10: plain-English screener and discovery upgrades

| Task | Work |
| --- | --- |
| I10.1 | **Draft schema** `ScreenDraft` (pydantic): `kind`, `markets`, `industries`, `keywords`, `filters: list[{metric_id, op, value}]`, `sort`, `unmatched_phrases`. `metric_id` must exist in the metric registry; values are validated against each metric's unit and range |
| I10.2 | **Parse endpoint** `POST /discover/parse`: text in, `ScreenDraft` out through `steward/llm` with a strict schema and the cache; registry of metric ids and aliases is part of the prompt; the call is budgeted through the existing cost controls and logged with the other AI decisions. The endpoint does not run the screen |
| I10.3 | **UI:** an "Ask for a screen" box above the filter builder; the result fills visible filter chips the owner can edit, lists phrases that could not be mapped, and shows the result count only after the owner (or an auto-run toggle) runs it; never executes anything the owner cannot see |
| I10.4 | **Evaluation:** a fixed set of 40 sentences with expected drafts in `evals/`; scored for exact filter match, unit correctness and refusal on unsupported asks ("stocks that will double"); must pass a threshold before release; re-run when the metric registry changes |
| I10.5 | **Metric parity:** compare `discovery/metrics.py` against the review's list and add what statements already support: net debt to EBITDA (exists), buyback yield and total shareholder yield (ratio exists, expose as metrics), share dilution year on year, forward valuation metrics when estimates exist, insider buying ratio from Form 4, dividend growth, payout ratios, and the new axis scores (I3.12). Each new metric ships with a formula string and a coverage count |
| I10.6 | **New matches:** saved screens show "n new in the last 7 days" using `screen_run` differences; extend `ops/opportunities.py` so a watchlist name that starts to pass a saved screen, not only a non-held stock, produces the existing "Needs you" line |
| I10.7 | **Curated templates:** about 12 starter screens kept in a config document (for example quality payers, undervalued with insider buying, financially healthy with rising margins), each with a title, one-paragraph rationale and an aggregate score mark; owner-editable, versioned; not a claim of recommendation |
| I10.8 | **Range slider:** F30 dual-handle slider with non-linear tick scales per metric (defined in the metric registry) for the advanced filter dialog |

Acceptance: the sentence "US dividend payers yielding over 4% with low debt and positive earnings growth" produces visible filters
the owner can change; an unsupported phrase is reported, not guessed; evaluation set passes.

## 17. Phase I11: returns and diversification

| Task | Work |
| --- | --- |
| I11.1 | **Audit** of `performance/attribution` and `ledger` for: separate currency effect, closed positions, split rows, per-holding annualised return, CSV export. Write findings in the plan status; implement only what is missing |
| I11.2 | **Returns breakdown** hero as a waterfall (unrealised, realised, dividends, currency, fees, total; residual disclosed so it reconciles) with `charts/waterfall.tsx` |
| I11.3 | **Closed positions block** below the detailed table; expandable transaction list per holding showing buy, sell and split rows with cash flow; CSV download of the table |
| I11.4 | **Sector to industry to holding sankey** from portfolio weights with a Sector/Industry/Holding toggle (`charts/sankey.tsx`); funds use look-through weights where present and label the residual |
| I11.5 | **Revenue by geography** across the portfolio from `segment_revenue` and `company_segment_map`, shown with coverage ("geography known for n% of value") and a ranked-bar view; no map unless six or more countries |
| I11.6 | **Concentration verdict:** top-3 share and effective number of holdings (existing HHI) in one sentence with the largest name |

Acceptance: components of the waterfall sum to the reported total within the disclosed residual; geography coverage shown on every view.

## 18. Phase I12: headline risk score and plain verdicts

| Task | Work |
| --- | --- |
| I12.1 | **Method `risk_score` v1** (published formula): map portfolio volatility, maximum drawdown, expected shortfall, top-3 concentration (or HHI) and beta each to a 0-100 sub-score by position between a low and high reference (the benchmark and a configured "very high" level), then a weighted mean with documented weights; computed only from existing Q1/Q2 outputs; `unknown` when the Q-phase minimum observations (`THRESHOLDS.min_obs_*`) are not met |
| I12.2 | **Verdict phrases** table keyed by metric and band (for example Sharpe, Sortino, beta, drawdown, concentration), versioned, returned by the API next to each number |
| I12.3 | **UI:** gauge bar (F21) with portfolio and benchmark markers and the score, metric tiles each with a verdict line (F31), tabs Volatility, Drawdown, Beta over a ranked per-holding bar chart with portfolio and market reference bars; correlation shown as the triangular diverging matrix with "moves together" and "moves least together" lists |
| I12.4 | **Tests:** score is monotonic in each input; golden portfolio; unknown handling |

Acceptance: score never shown without its inputs and benchmark marker; sub-scores visible on expand.

## 19. Phase I13: typed feed, provenance, ownership, governance, earnings summary

| Task | Work |
| --- | --- |
| I13.1 | **Typed updates feed:** one event model for the Inbox, News and the dashboard feed with type (`news`, `earnings`, `analyst`, `valuation`, `dividend`, `insider`, `risk`, `other`), ticker, price and 1-day move, "what changed" line, age; filter chips with unread counts; cards share one component |
| I13.2 | **ChartFooter** (F26) adopted by every new chart and rolled into existing charts per UX v2: `Data` toggle (table twin), `Learn` link to the glossary entry or method document, and a footer row with dataset, as-of, known-at and source from existing evidence ids |
| I13.3 | **Ownership by holder type:** institutions, insiders, public float and others from `institutional_holding` and `insider_transaction` where the share of shares outstanding is known; top-25 table with position change and the holder's own portfolio share if the 13F supports it; stated coverage |
| I13.4 | **CEO pay and tenure:** audit `companies/[ticker]/governance`; add from DEF 14A and the XBRL executive-compensation tags where filed (US issuers): pay, salary share, tenure, ownership, management and board average tenure, with `unavailable` for others |
| I13.5 | **Earnings-release summary and questions:** from the IR release or 8-K exhibit already stored, produce a short summary and three to five questions to ask, as a cited AI note (each sentence links to a quoted span; sentences without a quote are dropped). No transcript is claimed |
| I13.6 | **Insider volume strip:** net buy and sell by month beside the insider table with the existing lollipop timeline |

Acceptance: every AI sentence in I13.5 has a citation; feed filter counts match the lists.

## 20. Phase I14: validation and release

| Task | Work |
| --- | --- |
| I14.1 | **Golden set:** hand-computed examples for forward dividend income, one check per axis, portfolio scorecard, multiple-path scenario, risk score; stored with the tests and referenced from the method documents |
| I14.2 | **Coverage matrix:** per feature and holding `ready`, `partial`, `unavailable` shown on the data-quality cockpit, regenerated by I0.1 |
| I14.3 | **Method documents** for `dividend_income`, checks (all five axes and risk), `portfolio_scorecard`, `multiple_path`, `fair_multiple`, `risk_score`, `ScreenDraft` evaluation, written in `docs/methods/` with formulas, thresholds and limits, linked from each screen's Learn button |
| I14.4 | **Release checks:** reproduce three sampled figures per feature from source observation to screen; run the 390 px smoke test; Lighthouse accessibility on the new screens; feature flags removed after a clean week |
| I14.5 | **Retrospective of clocks:** at 3, 6 and 12 months, report dividend forecast accuracy, consensus hit rate and the share of holdings with usable fair-value history |

## 21. Risks and decisions

| Risk | Handling |
| --- | --- |
| Free dividend data is incomplete for foreign issuers (CSG, Chugai, Daiichi, Nu, ASML) | `partial` and `unavailable` states; estimates only where at least four payments exist; Inbox queue to confirm suggested events |
| Consensus not accessible on free plans | I0.2 decides; the whole chain is built on snapshots and shows unavailable if none; no synthetic values |
| Peer sets are small, so peer-relative checks may be unknown for many holdings | Rule §3.5; show "needs 5 peers, has 3"; prefer owner-edited peers; an `unknown` never scores |
| The checklist could be read as advice or as a rating | Always labelled as rules with stated thresholds; one-line disclaimer under the checks; rules editable in Settings; no composite buy or sell label |
| LLM misparse in the screen box | Strict schema, visible chips, unmatched phrases listed, evaluation set, no auto-run default |
| Scope size | Phases are independently shippable; stop after I1-I5 still delivers the highest-value set |
| Token or palette drift | New roles only through I0.6; `lint:colors` stays green |

Decisions taken (owner delegated such calls, as recorded in the v2 plan):

1. The five axes, six checks each, are StewardAI's own named rules with editable thresholds; there is no copy of any vendor's
   scoring.
2. Not-applicable checks are removed from the score denominator and displayed; unknown checks lower coverage only.
3. Dividend estimates assume no growth unless the owner turns growth on.
4. Fair-value history starts when the recorder starts; no back-filling from today's scenarios.
5. Holdings view presets are versioned config, not browser storage.
6. The screen parser never runs a screen; the owner does.
7. Industry-level pictures use the covered universe with an explicit count, or are not drawn.

## 22. Suggested slices

| Slice | Phases | What the owner can do |
| --- | --- | --- |
| A: clocks and coverage | I0 | See what is ready, and history starts accumulating |
| B: income | I1, I2 | Know next year's dividend income, see the calendar, see income by quality |
| C: verdicts | I3, I4 | Read a sentence-by-sentence checklist and a portfolio scorecard |
| D: value | I5, I6, I7 | See portfolio fair value over time, a second valuation method, peer pictures |
| E: workflow | I9, I10 | Switch holdings views, ask for a screen in words, see new matches |
| F: evidence and risk | I8, I11, I12, I13 | Consensus with agreement, returns and diversification pictures, one risk score, typed feed |
| G: release | I14 | Reproducible figures and method documents |
