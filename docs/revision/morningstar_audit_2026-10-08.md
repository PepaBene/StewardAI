# Morningstar plan audit (8 October 2026)

Audit of everything built for [the Morningstar features plan](../plans/morningstar_features_implementation_plan.md), backend and frontend, plus the live StewardAI database. Every high-severity item was re-checked against the code or live data; medium and low items were each confirmed by reading the code and, where cheap, by running it. Unconfirmed suspicions were dropped.

Each finding has an id (`R<n>`) so fixes can refer to it. Status is tracked in the last column of each table.

## 1. Migrations, database and deployment

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R1 | medium | `20261014100000_performance_config` and `20261022100000_feature_activation` were applied by hand; their effects are live (`performance` domain allowed, `feature_activation` exists) but they are missing from `supabase_migrations.schema_migrations`. A `db push` would re-run them and `create table public.feature_activation` would fail. | `list_migrations` on project `axlcpdozhsvgtqdxbwvp` | open: record the two rows (SQL in the plan, "Migrations on the live project"); the live write was refused to Claude |
| R2 | low | The deployed worker runs older code: job 888 (`fund_issuer_facts`) failed with "unknown job type"; `change_alerts` has never run. Active jobs config v58 lists neither, but both default to enabled, so a redeploy is enough. | `public.job` rows | open (needs a redeploy) |
| R3 | medium | Append-only is enforced only by the API. `authenticated` keeps UPDATE/DELETE/TRUNCATE grants on `decision_journal`, `source_audit`, `feature_activation`, `pilot_run`, `pilot_defect`, `quality_snooze`, `usage_event` (RLS blocks UPDATE/DELETE, not TRUNCATE) and can INSERT directly, skipping the "review names the same security" rule and the release gate. | `information_schema.role_table_grants`, `pg_policies` | fixed (`20261023100000_audit_fixes`, applied live 9 Oct 2026) |
| R4 | medium | `company_report_version` and `saved_analysis` have "for all" owner policies for `authenticated`, so a saved report can be rewritten through PostgREST (engineering rule 4). | `pg_policies` | fixed (`20261023100000_audit_fixes`, applied live 9 Oct 2026) |

Clean: live schema matches every Morningstar migration, all rollback files exist, `openapi.json` and `schema.d.ts` match what the code generates, `.env` is gitignored.

## 2. High severity

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R5 | M6.6 | The stress-loss risk limit can never fire: it imports `Assumptions`, which does not exist (only `ShockAssumptions`); the ImportError is caught and the limit reads "unavailable". | `steward/portfolio/risk_limits.py:389` | fixed |
| R6 | M6.7 | Saving in the strategy editor drops the `pretrade` block (and its `risk_contribution_cap`): `strategyToBody` builds a fresh object without it. | `apps/web/src/lib/config/strategy-form.ts:342` | fixed |
| R7 | M6.4/M6.5 | Risk and what-if pages upper-case tickers; live, 19 of 135 `position_snapshot` rows are mixed-case (`…l_EQ`), so those holdings read "not held" and owner betas/durations are ignored. | `apps/web/src/lib/risk/risk.ts:56,150`, `app/(app)/risk/whatif/whatif-view.tsx:55,75` | fixed |
| R8 | M2.3/M2.7 | Forward P/E never shows on the ratios page: all 160 live FMP consensus rows have no currency and `_complete()` requires one. The screen uses the same rows without the check, so screen and ratios page disagree. | `steward/sources/fmp.py:395`, `steward/company/evidence.py:419`, `steward/discovery/read.py:371`, `build.py:337` | fixed |
| R9 | M3.4 | NAV premium for a pence (GBX) listing is ~100x too large: raw GBX closes are compared with a GBP NAV. | `steward/fundresearch/service.py:263` | fixed |
| R10 | M4.5 | A blend the settings accept (weights within 1e-6 of 1) makes the returns and attribution pages 500, because `blended_index` rejects anything beyond 1e-9. | `steward/config/performance.py:45`, `steward/performance/benchmark.py:76` | fixed |
| R11 | M2.8 | "New since last review" always falls back to 90 days: it looks for `report` rows with `type='review'` and a ticker; monthly reviews are stored with no ticker. | `steward/company/evidence.py:599`, `steward/review/monthly.py:845` | fixed |
| R12 | M1.7 | Backtest look-ahead: historical market cap multiplies today's split-adjusted closes by the share count filed then, off by the split ratio before a later split; flows into earnings yield, FCF yield, EV and P/B. | `steward/discovery/build.py:158-169`, `steward/discovery/read.py:251` | fixed: stored splits, else a split inferred from a later filing that restates the share count by a whole ratio (`build.split_factor`) |
| R13 | M7.8 | "Draft from research" calls the model inside the API request, outside any job, so no budget cap applies and the spend is not recorded. Only route that does this. | `api/company_routes.py:226`, `steward/ai.py:111` | fixed (DB test `test_thesis_draft_db.py`) |

## 3. Medium severity

### Phases 0–3

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R14 | M2.5 | A saved valuation scenario keeps only its assumptions, not its result (rule 4). Not listed in the plan. | migration `20261008100000`, `steward/company/service.py:604-628` | fixed (`result` column, migration applied live 9 Oct 2026) |
| R15 | M1.8/M2.15 | ROIC is defined two ways (screen: period-end capital, no NCI, tax 0–100%, no bank n/m; ratios page: average capital with NCI, tax capped at 50%, bank n/m). EV/EBIT is not n/m for banks on the screen. | `steward/discovery/peer_metrics.py:343-351`, `steward/analytics/ratio_set.py:173-196` | fixed: one definition everywhere (`ratio_set.nopat_roic` over `peer_metrics.invested_capital`, with non-controlling interest; bank n/m) |
| R16 | M1.8 | The idea pipeline treats "unchecked" red flags (missing data) as clean. | `steward/discovery/composite.py:219-221` | fixed |
| R17 | M2.15 | Peer comparison mixes periods: the company's latest point can be TTM while peers use their last fiscal year. | `steward/company/ratios.py:377,490` | fixed (test in `test_company_ratios.py`) |
| R18 | M1.4/M2.4 | Sector peers are defined by the provider sector label alone and then cut alphabetically. | `steward/company/service.py:204-227` | fixed |
| R19 | M1.5 | `screen_run` stores a null snapshot id when the snapshot was resolved by date, and inserts a new run row for each cursor page. | `api/discover_routes.py:318,970,984-1009` | fixed |

### Phases 4–5

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R20 | M4.8 | The exemption total always uses a 3-year time test, whatever the owner sets. | `steward/performance/lots.py:181`, `lots.py:110`, `service.py:494` | fixed |
| R21 | M4.3 | XIRR is annualised for 1M and YTD (a +3% month shows ~+43%); above ~+46% a month leaves the solver bound. Contradicts "annualized only from 365 days". | `steward/performance/windows.py:116-119`, `returns-view.tsx:107` | fixed |
| R22 | M4.9 | With less history than the lookback, the sleeve window silently starts at the first day while the review says "over N years". | `steward/performance/sleeves.py:106-108`, `review.py:47` | fixed |
| R23 | M4.6 | A ticker unattributable in some interval but sold before the end does not mark the bridge partial. | `steward/performance/attribution.py:231-239` | fixed |
| R24 | M5.7 | The same addition entered twice is added twice; the comparison keys by ticker and shows one. | `steward/xray/scenario.py:101-118,349` | fixed |

### Phase 6

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R25 | M6.7 | Adding to a position already over its cap shows "still breached" and a "No new breach" pass. | `steward/decision/pretrade.py:788-821` | fixed |
| R26 | M6.7 | Several sales are each checked against the same year-to-date proceeds, so together they can exceed the exemption unflagged. | `steward/decision/pretrade.py:546,757` | fixed |
| R27 | M6.4 | Rebalance target overrides are not validated (`model_copy` skips validation): −0.3 proposes selling 800 of a 500 position. | `steward/decision/rebalance.py:155` | fixed |
| R28 | M6.4 | A withdrawal never triggers a sale: withdrawing 600 with 100 cash leaves cash at −500. | `steward/decision/rebalance.py` | fixed |
| R29 | M6.1 | Per-holding returns treat the daily FX change on the cost basis as a trade, understating foreign holdings and skewing correlation and diversification. | `steward/decision/series.py:111-115` | fixed |
| R30 | M6.5 | A what-if withdrawal from a short or unheld split covers only part of the shortfall but says it comes "from sales". | `steward/decision/whatif.py:194-205` | fixed |

### Phases 7–8

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R31 | M7.9 | The scorecard's currency check never triggers: the current currency is set to the entry's own. | `steward/journal/service.py:78` | fixed |
| R32 | M7.9 | A back-dated entry counts targets hit before it was written (`decided_on` unchecked, price defaults to today's). | `steward/journal/entries.py:35,178`, `service.py:83,86` | fixed |
| R33 | M7.2 | Fund changes ignore the comparison date and whether the fund is held. | `steward/changes/feed.py:338-377` | fixed (DB test `test_changes_db.py`) |
| R34 | M7.2 | "Revised" link items have no "before" record (exit check: a change points at both). | `steward/changes/feed.py:395-397` | fixed |
| R35 | M8.3 | The usage beacon accepts lowercase paths only, so `/funds/IE00…` and `/companies/AAPL` are never counted. | `api/release_routes.py:26`, `components/release/usage-beacon.tsx:15` | fixed |
| R36 | M7.5 | A snoozed issue returns the day after the date the page shows. | `steward/quality/snooze.py:78` | fixed |

## 4. Low severity

| Id | Item | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| R37 | M3.7 | A missing expense ratio becomes 0. | `steward/fundresearch/analysis.py:1207` | fixed |
| R38 | M2.5 | DCF is unavailable for a company that files no long-term debt. | `steward/analytics/ratio_set.py:88` | fixed |
| R39 | M2.6 | FCF-per-share growth divides by the count of positive years, not the years elapsed. | `steward/company/autoevidence.py:218-223` | fixed |
| R40 | M4.1 | Fees in a currency other than the fill's are dropped silently. | `steward/performance/service.py:91` | fixed |
| R41 | M4.5 | The settings form accepts benchmark weights the backend refuses with a 422. | `apps/web/src/lib/performance/settings-form.ts:107` | fixed |
| R42 | M5 | Any `?as_of=` request skips the 60-second X-Ray cache. | `api/xray_routes.py:58-63` | fixed |
| R43 | M4.9 | The monthly review does not record which performance-settings version it used. | `steward/review/monthly.py:812-816` | fixed |
| R44 | M6.7 | The switch gap passes when tax is unknown (treated as 0). | `steward/decision/pretrade.py:610` | fixed |
| R45 | M6.6 | The valuation-flag query has no owner filter. | `steward/portfolio/risk_limits.py:413` | fixed |
| R46 | M6.5 | A saved what-if records the `scenario_shock` method version; the shock `reconciles` flag is always true by construction. | `api/decision_routes.py:258`, `steward/decision/shocks.py:281` | fixed |
| R47 | M8.5 | Release readiness shows every store input as "1 rows" (counts with `limit 1`). | `steward/release/readiness.py:239` | fixed |
| R48 | M7.7 | CSV export does not neutralise a formula padded with a leading space. | `steward/journal/export.py:263` | fixed |
| R49 | M8.3/M7.7 | The pilot report and owner report include snoozed issues. | `steward/release/pilot.py:183`, `steward/journal/export.py:68` | fixed |
| R50 | M7.1 | Review due dates set on review entries never alert (query requires `parent_id is null`). | `steward/changes/alerts.py:122` | fixed (DB test `test_changes_db.py`) |
| R51 | M8.3 | A defect typed with a bare ticker (AAPL) shows no portfolio weight. | `steward/release/defects.py:70,91` | fixed |
| R52 | M5.2 | Two directly held share classes of one issuer count as "shared" in the intersection. | `steward/funds/aggregate.py:408` | fixed |
| R53 | M7.2 | A `since` date that is not a snapshot day gives no price items while the area count says holdings were compared. | `steward/changes/feed.py:590-600` | fixed (DB test `test_changes_db.py`) |
| R54 | M1.2/M1.3 | Market cap computed when the statement currency is unknown; 1-year volatility has no stale-price check; cash runway uses annual cash flow though defined as TTM; fund top-10 and HHI keyed by ISIN or name, not issuer. | `steward/discovery/build.py:166,283,446-463` | fixed |
| R55 | M1.8/M2.14 | Screen results link only to the company overview, the screens page only to `/holdings`, the watchlist not at all; none reach the financials or ratios pages. | `results-panel.tsx:212` | fixed |

## 5. Plan text that is wrong

| Id | Finding | Status |
| --- | --- | --- |
| R56 | Lines 453, 554, 556, 558 contradict each other on which migrations were applied; line 556 says `performance_config` is not applied; `20261022100000_feature_activation` is not mentioned. | fixed |
| R57 | Lines 513 and 552 say the M8.5 switch-on decision is not stored or enforced; `feature_activation` and its 409 refusal exist. | fixed |
| R58 | Lines 386, 406, 438 say the bear-case and covariance checks cannot run; `covariance.py` and the thesis `bear_case` exist. Stale comments at `pretrade.py:10-12,84`. | fixed |
| R59 | Line 469 contradicts line 438 (scorecard in monthly review, filters); lines 474–477 contradict the "added later" notes at 439–443; line 439 "no relevance score" contradicts line 480; line 437 says research does not pre-fill drafts in the same row as "Draft from research". | fixed |
| R60 | Line 156 says settings live in `performance_setting`; line 205 says lots and ledger lack a freshness badge; line 217 says Phase 5 has no migration; line 442 says snoozes last 7–90 days (backend allows 180). | fixed |

## 5a. Fix pass (9 October 2026)

All findings except R1 and R2 have a code, migration or plan-text fix in the working tree (not committed). Notes:

- **Database:** new migration `20261023100000_audit_fixes` with its rollback (R3, R4, R14). Applied on the live project on 9 October 2026 through the Supabase tool (recorded as version `20261009105854`, name `audit_fixes`). Checked after applying:
  - `company_valuation_scenario.result` exists.
  - The seven `*_owner_insert` policies are gone.
  - `authenticated` holds only SELECT on the listed tables.
  - `app_backend` has no DELETE on `company_valuation_scenario`.
- **Live writes not done:** recording the two hand-applied migrations (R1). The automated permission check refused it for Claude. R2 needs a worker redeploy.
- **Method versions bumped:** `risk` 1.2.0, `rebalance` 1.1.0, `pretrade` 1.1.0, `screen` 2.1.0 (R12, R15, R54), `composite_qvm` 1.1.0 (R16), `company_ratios` 1.1.0 (R17). New entry `whatif_case` 1.0.0 (R46).
- **Ticker case (R7):** the backend now resolves typed tickers to the stored spelling: the latest snapshot first, then the instrument list (`decision/service.py`, `match_tickers`). The web no longer upper-cases them.
- **Shock `reconciles` (R46):** now checks that the factor split and the position lines each add to the P&L, and that cash, shocked and unshocked weight add to the leaf weight, which itself adds to 1.
- **Gaps closed in a second pass (9 October 2026):**
  - **R12:** without a stored split, the split factor comes from a later filing that restates the same year's share count by a whole ratio, within 2% (`build.split_factor`). Live, no split is stored in `corporate_action` and only adjusted bars exist, so this is the path that applies. Limit: a split after the newest annual filing shows only once a later filing restates it.
  - **R15:** the peer metrics, the older screen route and the discovery screen now use `ratio_set.nopat_roic` over one `peer_metrics.invested_capital`. That adds non-controlling interest when the equity element excludes it (US GAAP `MinorityInterest`, IFRS `NoncontrollingInterests`). The interest does not decide whether a period counts as reconciled.
  - **R17:** unit test with a four-quarter TTM fixture.
  - **R13:** `thesis_draft` jobs use the default per-job cap. A DB test runs the real job runner and budget guard: under the cap the job succeeds; at the cap the request is refused and the job fails.
  - **R33, R50, R53:** database-backed tests in `test_changes_db.py`.
  - The earlier note that `entity_relationship` has no status-log trigger was wrong: `20261005190000_entity_relationship_status_log` adds it.
- **API changes:** `openapi.json` and `schema.d.ts` were regenerated (additions only), adding:
  - `WindowOut.xirr_annualized`
  - `LedgerHealth.fees_excluded`, shown on the ledger page
  - `SavedScenario.saved_result`
  - `PeerStat.basis`
  - `Network.since_basis` value `last_journal_entry`
  - the `target_overrides` 0–1 bounds
- **Checks run:** backend ruff, ruff format and mypy are clean. 292 scoped backend tests pass (13 database-backed tests skipped). Web tsc and eslint are clean on the changed files, and 46 scoped vitest tests pass. Nothing was rendered in a browser.

## 6. Checked and clean

Ledger sign convention; `time_test_date` on 29 February; X-Ray lens sums and the "all other" bucket; HHI on identified weight; segment eliminations and share-0 removal; saved-analysis kinds match the migration and saved items reopen as stored; null-guarded number formatting in the company and X-Ray views.

## 7. Not covered

No page rendered in a browser; the cockpit and sector SQL and the chat tools not run against a database; the PDF not rendered; the backtest UI not reviewed; whether stored profile tickers are broker or bare symbols (affects the sector map's held weight).
