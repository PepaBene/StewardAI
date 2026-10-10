# Deep audit of the Morningstar and public-disclosure code (9 October 2026)

Audit of everything built for [the Morningstar features plan](../plans/morningstar_features_implementation_plan.md) and [the public-disclosure datasets plan](../plans/public_disclosure_datasets_implementation_plan.md): `c804420..121cecb` on `morningstar`, about 80K lines of backend, 30K of web and 2.7K of migrations. It follows the [Morningstar audit](morningstar_audit_2026-10-08.md) and the [public-disclosure audit](public_disclosure_audit_2026-10-08.md) of 8 October. Items those audits already fixed are not repeated. Where a finding is a second instance of an earlier one, it says so.

**Method.** The code was split into nine slices: analytics and discovery; company; funds; performance and X-Ray; decision, journal and release; disclosure ingest; ownership and network; deals and estimates; web. Each slice was read against the plan's specification and status tables, and every candidate was checked against its callers before it was kept. Gaps the plans already list as open were left out unless the code is worse than the plan says. The migrations, route auth and SQL construction were checked directly.

**Re-checking.** Every high and medium item was opened at the cited lines a second time, by a different reader, and confirmed. An adversarial pass tried to disprove the medium items. It found no false positives, but it moved D6, P2, D51, W4 and B1 down to low and narrowed D55. Low items are confirmed by the slice reader only. Items marked *verify* depend on outside behaviour that could not be confirmed from the code alone. Dropped as false positives or already documented:
- partial distributions counted as complete (documented under `M3.4`);
- missing owner auth on routes (every handler in every route file requires `Owner`);
- tables without RLS (all 53 new tables enable it, many inside `do` loops);
- f-string SQL (`baseline.py`, `coverage_report.py` and `deal_read.py` interpolate fixed fragments only).

Paths are under `apps/backend/src/` unless stated. Line numbers are approximate. All items are **open**; nothing was changed.

## Summary

| Severity | Count |
| --- | --- |
| High | 8 |
| Medium | 55 |
| Low | 96 |

The high items, in fix order:
- **D1:** duplicate migration versions break `db push` on a fresh database.
- **D3:** TTM share counts are four times too large, which feeds dilution, per-share values and the DCF value per share.
- **D2:** past-dated screens leak later filings.
- **D39:** the 13F ownership lane crashes on an untyped amendment.
- **D44, D45:** negative and footnoted numbers in estimate tables are misread, and non-GAAP figures are stored as GAAP.
- **D31:** the ingest cursor skips filings after a backlog.
- **D32 (now medium):** EDGAR acceptance times are read as UTC; `known_at` is a few hours off, likely late.
- **D19:** a pro-rata what-if overwrites the purchase it is funding.

## 1. Migrations, database and deployment

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D1 | high | Two pairs of migrations share a version: `20261007100000_disclosure_foundation` / `_discovery_universe_screens`, and `20261008100000_company_workbench` / `_defect_fixes`. The Supabase CLI keys `schema_migrations` by version, so `npx supabase db push` (the README's deploy step) or `db reset` on a fresh project fails or skips the second file. The test harness applies files by sorted glob, so tests pass. The live project has them applied, according to the 8 Oct disclosure audit 1.1. | `supabase/migrations/` | open · re-checked |
| D2a | low | `screen_run` (saved screen results) still grants `authenticated` select, insert, update and delete under a for-all owner policy, so a run's reproducible metadata can be rewritten or deleted through PostgREST. Same class as R3/R4, which fixed the other saved tables. | `20261007100000_discovery_universe_screens.sql:143` | open · re-checked |

## 2. Analytics, discovery and screens (M0, M1)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D2 | high | A past `as_of` without `known_at` leaks later data. `cutoff=None` makes `_facts` load every filing, and `latest_period` has no as-of guard, although the request field says "Past dates use … facts known by then". Catalogue, baselines and saved-screen reruns with `?as_of=` behave the same. `screen_run.as_of_cutoff` records a cutoff that was never applied. Only the backtest sets `known_at`. | `steward/discovery/read.py:186-196`, `api/discover_routes.py:87-93,316-320,1005` | open · re-checked |
| D4 | medium | `_shareholder_yield` treats a missing dividend or buyback as 0 (`abs(div or ZERO)`) when the other is known, so it publishes a partial figure as the whole yield. This breaks the "None is not zero" rule. | `steward/analytics/ratio_set.py:280-287` | open · re-checked |
| D5 | medium | `valuation_gate_only` filters after `run_screen` has applied `limit`. Matches past the limit that pass the gate are never returned, and the result is described as complete. | `api/screen_routes.py:130-152` | open · re-checked |
| D6 | low | Dividend yield is not split-adjusted. Pre-split dividends in the 365-day sum are divided by the post-split close, which overstates the yield after a split. | `steward/discovery/read.py:314-330`, `build.py:418` | open · re-checked (overstated, moved to low) |
| D7 | medium | The insider screen metric filters on `superseded_by_accession is null` as stored now. An original superseded after `as_of` and its not-yet-known amendment both vanish. A ticker whose filings all postdate `as_of` shows 0 buyers instead of unknown. Second instance: `disclosure/screening.py:350-361` (D52). | `steward/discovery/read.py:372-392` | open · re-checked |
| D8 | low | The watchlist universe selects `watchlist_item` with no `owner_id` filter, unlike `discover_routes.py:188`. This is harmless while the app is single-owner. | `api/screen_routes.py:59` | open · re-checked |
| D9 | low | The backtest signal and the entry fill both use the rebalance day's close (same-close look-ahead). With no filters and no `sort_by`, the picks are the first N tickers alphabetically, with no warning. | `api/discover_routes.py:667-679`, `steward/discovery/engine.py:233` | open |
| D10 | low | `_definition` clears `as_of` only for stock screens, so a saved fund screen reruns against its save date. A saved stock `known_at` pins the cutoff on every run. | `api/discover_routes.py:786-793` | open |
| D11 | low | The fund CSV export uses the request's own limit (default 50), not the 200 the docstring states. | `api/discover_routes.py:462` | open |
| D12 | low | `peers_of` ignores `valid_from` and `known_at`, so peers confirmed after `as_of` enter historical baselines. | `api/discover_routes.py:506-511` | open |
| D13 | low | The dividend and consensus currency check compares against the major unit (GBP). Dividends recorded in GBX never match, so LSE yields are always unknown; a pence amount labelled GBP would be 100× too high. | `steward/discovery/build.py:412,442` | open |
| D14 | low | `xirr` bisects without checking that the root is unique. With 3 or more sign changes it can return an arbitrary root where the plan says "no stable root → unavailable". | `steward/analytics/returns.py:281-301` | open |
| D15 | low | Fund `top10_weight` and `effective_holdings` are normalised over equity rows only, so a 60/40 fund reports its equity sleeve's concentration as the fund's. | `steward/discovery/build.py:545-583` | open |
| D16 | low | Market cap uses the last close with no staleness check (returns and volatility reject closes older than 10 days). `years_since_listing` can go negative for a historical `as_of`. | `steward/discovery/build.py:226-243` | open |
| D17 | low | Renaming a screen version to an existing name raises an unhandled UniqueViolation (500); `save_screen` handles the same case. | `api/discover_routes.py:879-885` | open |

## 3. Company workbench (M2)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D3 | high | `_ttm` sums the last four quarters for every duration concept, including `weighted_shares_basic`/`_diluted` (unit "shares"). A derived Q4 is FY minus 9M for those too. Whenever a quarter is newer than the last fiscal year, the TTM share count is about 4× the real one. It feeds `dilution_3y` (about +59 % a year) and the rubric's allocation score, `_per_share`, TTM market cap and Altman Z (when `market.shares` is absent), the statements' `per_share` transform on the TTM column, and `valuation.py:214`, which divides the DCF value per share by about 4. | `steward/company/series.py:99-158`, `analytics/ratio_set.py:136,157-168`, `company/statements.py:601`, `company/valuation.py:214` | open · re-checked |
| D18 | medium | `compare()` uses `subject_pairs[-1]`, the TTM point when it is newer, for the company's cells, percentile and peer-staleness anchor, while peers use their latest fiscal year. R17 fixed this in `ratios.py`; this path was missed. `subject_values` also calls `r.fn()` without `evaluate()`'s try/except. | `steward/company/service.py:436-443,491` | open · re-checked |
| D20 | medium | `save_group` upper-cases peer tickers. Trading212 tickers are case-sensitive (`VUSAl_EQ`), so a London or Amsterdam peer is dropped as "not in the covered universe". Second instance of R7. | `steward/company/service.py:142-144` | open · re-checked |
| D21 | medium | Overview segments take every `statement='segment'` Revenues fact at the latest `period_end`, with no duration filter, axis separation or restatement de-duplication. `total` sums geography, product and segment axes plus YTD and restated copies, so shares and denominators are wrong. `segments.analysis` is the correct path. | `steward/company/evidence.py:180-212` | open · re-checked |
| D22 | medium | Surprise and forward P/E ignore currency. An estimate in USD for an EUR filer yields surprises of hundreds of percent, and forward P/E divides a listing-currency price by EPS in another currency. Every estimate vintage for a period gets a surprise, not only those known before the actual. | `steward/company/evidence.py:455-475,522-535` | open · re-checked |
| D23 | low | When cash is filed but no debt concept is mapped, net debt is set to minus cash, so debt held under an unmapped or extension concept inflates the per-share value. The reason string says so, but the plan does not list it as a limitation, and the net-debt date is not carried. | `steward/company/valuation.py:177-186` | open · re-checked |
| D24 | low | With no scenario selected, the grid and reverse DCF still use `results[0]`, the bear case, although the comment says there is no default selection. | `steward/company/valuation.py:264-284` | open |
| D25 | low | `yoy` runs after currency translation, so growth includes the FX move, contradicting the currency note. | `steward/company/statements.py:580-594` | open |
| D26 | low | Announced dividends are keyed by the calendar year of the ex-date but compared with fiscal-year cash paid. There is no currency or listing filter, so a dual listing can be double counted. | `steward/company/evidence.py:776-780` | open |
| D27 | low | The thesis draft truncates facts in the order research, figures, segments, conflicts, so the conflict warnings are dropped first. `resolve` does not check that numbers in the text exist in the facts. | `steward/thesis/draft.py:116` | open |
| D28 | low | Citation URLs get only a length check, so a `javascript:` URL can be stored. `POST /case` and `PUT /peer-groups` accept any ticker without validation. | `steward/company/evidence.py:1008` | open |
| D29 | low | Report and scenario version numbers are `max+1` without a lock, so two concurrent saves return a 500 on the unique key. | `steward/company/service.py:662-668`, `report.py:358-363` | open |
| D30 | low | "Cash returned … with both figures" counts years that have only one of dividends or buybacks, treating the other as 0. | `steward/company/autoevidence.py:157-169` | open |

## 4. Fund research (M3)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| F1 | medium | The securities-lending checklist scores every value except exactly "yes/true/lends/y" as "good", so "Yes (up to 10%)", "unknown" or "partial" passes. The fact is free text in the API. | `steward/fundresearch/analysis.py:1268-1274`, `api/fund_research_routes.py:202-231` | open · re-checked |
| F2 | medium | The recorded `benchmark_basis` and `benchmark_currency` facts are applied to whatever benchmark the owner picks with `?benchmark=`, so an unknown index is treated as total-return USD and a tracking difference is drawn. | `steward/fundresearch/service.py:222-239` | open · re-checked |
| F3 | medium | `benchmark_options` never sets currency, and with none recorded the difference is still computed ("assumes it matches"), contrary to "only on matched currency". | `steward/fundresearch/performance.py:342-346,401-419` | open · re-checked |
| F4 | medium | An accumulating fund is compared price against price with a price-return index, so the tracking difference includes the dividend yield and the checklist shows "concern". | `steward/fundresearch/performance.py:406-410` | open · re-checked |
| F5 | medium | No staleness check: if the listing's last bar is old, the 1Y and 3Y windows end then and are shown undated in compare. | `steward/fundresearch/performance.py:331,363` | open · re-checked |
| F6 | medium | `load_facts` ignores `as_of`, so an as-of checklist uses facts recorded later. | `steward/fundresearch/data.py:256-264`, `service.py:272` | open · re-checked |
| F7 | low | Two different funds on the same index (same issuers, weights within 0.5 %) are labelled share classes of one portfolio, and their overlap is skipped. | `steward/fundresearch/compare.py:163-185` | open |
| F8 | low | Issuer sync decides owner versus issuer by URL host. An owner value citing an ishares.com page is superseded silently by the next sync. This matches the plan's "from another source", but it clobbers an owner decision. The same applies to `plan_nav_write`. | `steward/fundresearch/issuer_sync.py:451-487` | open · re-checked |
| F9 | low | When the listing currency is None, the NAV currency check is skipped and a premium is still computed. | `steward/fundresearch/performance.py:165-169`, `service.py:262-264` | open |
| F10 | low | Fund volatility is computed on dates shared with the benchmark, so it changes with the benchmark chosen. | `steward/fundresearch/performance.py:439-446` | open |
| F11 | low | `.get` on non-dict payload nodes raises AttributeError, which is not caught, so one bad payload aborts the whole sync. `fund_index_coverage.checked_at` is rewritten on every run. | `steward/fundresearch/issuer_sync.py:159,218,667-673,706` | open |
| F12 | low | A ticker whose FMP compensation call is gated or over quota gets no `coverage_status` row, so its profile and estimates are re-fetched every run. | `steward/sources/provider_jobs.py:138` | open |

## 5. Performance and X-Ray (M4, M5)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| P1 | medium | A sleeve's return is annualised over the whole lookback span even when the sleeve held nothing for part of it. `twr` skips the zero-value intervals, but `span` counts every day. `too_early` uses the snapshot span, so a 3-month stock sleeve inside 400 days of history can raise a breach and a shift proposal in the monthly review. Related to R22, but a different defect. | `steward/performance/sleeves.py:100-128,188-189` | open · re-checked |
| P2 | low | `gross = abs(raw.amount)` drops the sign of dividends, deposits, withdrawals and fees, so a negative dividend correction is booked as income. The cash check then reports a false break that withholds TWR. (*verify* whether Trading212 sends negative rows.) | `steward/performance/ledger.py:115,133,137-139` | open · re-checked (overstated, moved to low) |
| P3 | low | Flows are dated by Prague calendar day, but snapshots are taken at 22:30 and a manual resync replaces the day. A deposit after the snapshot is counted in a day whose value does not contain it, which creates two cash breaks around it. | `steward/performance/service.py:157-177`, `ledger.py:94-98` | open |
| P4 | low | Configured `max_sector_weight` and `max_country_weight` limits are never evaluated when `owner_user_id` is unset, and no `unavailable` entry is written. Each evaluation also calls the uncached X-Ray `load`. | `steward/portfolio/risk_limits.py` (`gather_risk_inputs`) | open |
| P5 | low | Revenue, commodity and trade slices summing above 1 make a leaf count more than its weight, and the negative rest is ignored. | `steward/xray/views.py:387-394` | open |
| P6 | low | `reconciles` uses a 0.01 tolerance, so a dropped leaf of up to 1 % still reads "reconciles". | `steward/xray/leaves.py:210`, `views.py:536` | open |
| P7 | low | Attribution components and the residual are rounded separately, so the rounded sum can differ from the rounded gain by cents. | `steward/analytics/returns.py:362-363` | open |
| P8 | low | A window's opening day can be up to 7 days before its start (a 1Y window of 372 days) with no flag. `_missing_weight` uses the nominal start. | `steward/performance/windows.py:96-107`, `service.py:198-213` | open |
| P9 | low | The growth-of-100 benchmark rebases on the first close on or after the start; window figures use the close on or before. | `steward/performance/service.py:413` | open |
| P10 | low | Under average cost, the open-lot table still shows FIFO unit cost. | `steward/performance/lots.py:190-196` | open |
| P11 | low | Duplicate tickers in what-if `weights` resolve last-wins, while additions are summed. | `steward/xray/scenario.py:92-95` | open |
| P12 | low | Region splits are case-sensitive in SQL but lower-cased when read, so "Europe" and "europe" are both in force and the winner is arbitrary. | `steward/xray/owner_input.py:163-193` | open |

## 6. Risk, decisions, journal and release (M6, M7, M8)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D19 | high | In a `new_security` what-if with `fund_from="pro_rata"`, the trim loop overwrites the purchase when the ticker is already held: `after[ticker]` is set to `v+spend` and then reset to `v*factor`. The proposal shows a trim with no buy, totals stop summing, and pretrade gets a buy and a sell of the same ticker. | `steward/decision/whatif.py:162-177` | open · re-checked |
| M1 | medium | What-if `split` values are unbounded: `{"A": 2, "B": -1}` passes the sum check, then `TradeLeg(value_base<=0)` raises a ValidationError that the routes do not catch (500). A withdrawal with a negative share produces negative sales. | `steward/decision/whatif.py:49-53,201-205` | open · re-checked |
| M2 | medium | `_tax_estimate` divides base-currency value by account-currency proceeds and labels the result in base currency, so the switch hurdle is off by the FX rate unless the account and base currencies match. | `steward/decision/pretrade.py:762-774`, `service.py:788-803` | open · re-checked |
| M3 | medium | Shock `fx`, `sector` and `country` moves are unbounded (`USD: -1.5` gives a negative total while it still "reconciles"). Keys are not normalised, so `usd` or `us` silently matches nothing. | `steward/decision/shocks.py:53-62` | open · re-checked |
| M4 | medium | The event-alert dedupe key uses the cluster's representative story id. When a tier-1 article joins the cluster and becomes representative, the same event alerts again. Deal alerts key on every news item, so two sources for one deal each alert. | `steward/changes/alerts.py:65-81,149-175`, `events/feed.py:133-146` | open · re-checked |
| M5 | medium | The chat `theme` tool does not check the value against the portfolio's labels. `sector="Tech"` returns 0 % exposure with "high" confidence, which the model can state as fact. | `steward/chat/analytics_tools.py:79-119`, `decision/themes.py:253` | open · re-checked |
| M6 | medium | Risk contribution covers only the modelled holdings. With 2 of 10 modelled, a stock can "account for 90 % of volatility" and breach `risk_contribution_cap`. Unpriced names are not listed in `left_out`. | `steward/decision/covariance.py:82-99`, `pretrade.py` (`_risk_budget`) | open · re-checked |
| M7 | medium | The release gate passes on 3 self-entered matched facts across 2 subjects. `source_url` and `feature` are not validated, and `summarize().complete` is not part of `can_activate`. Activation is checked only at switch-on: if the gate later fails, the feature stays active. | `steward/release/readiness.py:202-215`, `activation.py:33-44` | open · re-checked |
| M8 | low | A position with `value_base` 0 raises DivisionByZero (500) in pretrade and rebalance. Duplicate sell legs overwrite `sale_impacts[ticker]`. | `steward/decision/service.py:519-520,788-791` | open |
| M9 | low | Pretrade compares the raw currency to base (`GBX != GBP` charges FX), while rebalance normalises, so the two tools disagree. | `steward/decision/pretrade.py:607-610` | open |
| M10 | low | The rebalance display shows `weight_now` on today's total while the band is judged after the deposit. | `steward/decision/rebalance.py:189,277` | open |
| M11 | low | Pilot `rated` counts feedback on any alert, so it can exceed `sent`, and the false-positive rate has a mismatched denominator. | `steward/release/pilot.py:147-161` | open |
| M12 | low | `thesis_gap` flags core ETFs, which pretrade exempts, so `affected_weight` is inflated. | `steward/quality/cockpit.py:287-315` | open |
| M13 | low | `load_profiles` does not filter `provider='fmp'`, so a newer country from another provider can flip `markets`. | `steward/sectors/map.py:257-262` | open |
| M14 | low | The journal export's `list_entries(limit=2000)` truncates silently, although the manifest says "all you have recorded". | `steward/journal/export.py`, `journal/service.py` | open |

## 7. Disclosure: ingest, statements and replay (P0–P2)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D31 | high | `new_filings` keeps only the newest 3 filings after the cursor, then `advance_cursor` moves past them. After an outage with 4 or more new filings, the oldest are never ingested. | `steward/disclosure/jobs.py:72-74,178-184` | open · re-checked |
| D32 | medium | `acceptanceDateTime` is parsed as true UTC (`Z`→`+00:00`), but the feed's `Z` times are not UTC. On Apple's live submissions feed (CIK 320193, 9 Oct 2026), the parsed times put about 860 of 1,000 filings between 22:00 and 01:00 New York time, after EDGAR's 22:00 close. The feed hours fit EDGAR's 06:00–22:00 window only if the true Eastern time is about 8 hours before the feed value: the FY2023 10-K reads `2023-11-03T02:08:27Z`, which is 18:08 ET on 2 Nov. If that holds, stored `known_at` is 3–4 hours **late**. That is the conservative direction (no look-ahead leak), but as-of views and event timing are off, and an after-hours filing can move to the next day. (*verify* the offset against one filing's `ACCEPTANCE-DATETIME` header, which needs the SEC User-Agent.) | `steward/disclosure/sec_discovery.py:73-80` | open · re-checked (feed), offset to verify |
| D33 | medium | Only `xbrldi:explicitMember` is read into context dimensions; `typedMember` is ignored. A typed-dimension fact is mirrored as consolidated, and the ON CONFLICT key makes the first one in the document win, so the real consolidated value can be dropped. | `steward/disclosure/parse.py:242-250`, `fact_evidence.py:137-178`, `ingest.py:238` | open · re-checked |
| D34 | medium | Several concepts map to one metric (`NetIncomeLoss` and `ProfitLoss`; three debt concepts; operating and finance lease). The observation stores no concept and the unique key excludes it, so the first in document order wins silently. A lease liability with one of the two is reported as the whole. | `steward/disclosure/fact_evidence.py:140`, `financials.py:45-185` | open · re-checked |
| D35 | medium | The statement-history query has no ORDER BY and sorts on `known_at` only. Two concepts for one metric and period make "latest" arbitrary and flag a false restatement. | `steward/disclosure/statement_read.py:136-173`, `financials.py:709-733` | open · re-checked |
| D36 | medium | Amendment links are incomplete. `amends_version_id` is set only if the original is already stored; the route ingests `[-limit:]` and never back-fills. `superseded_by_id` is set only within one accession, so after a full 10-K/A the original still reads current in `filing_timeline`. Filing observations never set `supersedes_id`. | `steward/disclosure/ingest.py:101-111`, `api/disclosure_jobs_routes.py:90-92`, `asof.py:218` | open · re-checked |
| D37 | low | A 403 (SEC fair-access block) is recorded as `ACCESS_UNVERIFIED` with no latch or `retry_at`, and the loop keeps requesting. There is no 5xx backoff, and pacing is per-minute only, with submissions and archive budgeted separately. | `steward/disclosure/fetch.py:188,225-246`, `ingest_job.py:190-199` | open |
| D38 | low | Only `FetchFailure`, `HTTPError` and `ValueError` are caught per issuer. A cooling-down `RuntimeError`, `HardBlocked`, `QuotaExceeded` or a database error aborts the run for every remaining issuer and loses the step totals. | `steward/disclosure/jobs.py:172` | open · re-checked |
| D38a | low | Replay's `as_of.date()` uses the caller's offset. `drop_future` keeps items with `known_at` None, and deals, ownership and insiders all pass None, so its leak guard does nothing for them. | `steward/disclosure/replay.py:60-78,127-176` | open |
| D38b | low | `observations_as_of` returns dimensional rows beside consolidated ones, and `answer()` passes them to the evidence picker, so a segment revenue can be quoted as "Revenue". | `steward/disclosure/asof.py:100-125`, `answer.py:261` | open |
| D38c | low | `metric_for` strips only the `us-gaap:` prefix, so `dei:EntityCommonStockSharesOutstanding` never maps. | `steward/disclosure/financials.py:190-192` | open |
| D38d | low | The note-store idempotency key includes the character offset, so a parser-version bump re-inserts the same buckets and `load_note_rows` keeps both. Two debt tables in one filing merge last-wins. | `steward/disclosure/note_store.py:148-155` | open |
| D38e | low | Confirming an LEI or FIGI suggestion marks it confirmed even when the issuer already holds a different identifier (the `where lei is null` update is a no-op), so the decision disagrees with the stored identifier. | `api/disclosure_jobs_routes.py:276-296` | open |
| D38f | low | The SEC User-Agent (with a contact e-mail) is sent to every third party probed (GLEIF, TED, EDINET, SEDAR, IR sites). `sub.json()` is unguarded. | `steward/disclosure/probe.py:90,394` | open |
| D38g | low | `link_amendments` matches form plus report date, so two same-day 8-Ks can mislink an 8-K/A. EDINET's parent document id is not used. | `steward/disclosure/sec_discovery.py:138-162` | open |
| D38h | low | `str(int(cik))` raises on a non-numeric CIK. | `steward/reference/disclosure_identity.py:108` | open |

## 8. Disclosure: ownership, insiders and dependencies (P3–P5)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D39 | high | When the only filing for a manager and period is a 13F-HR/A with no amendment type, `accessions` is empty and `rep.accessions[0]` raises IndexError, so `/holdings/{t}/ownership` returns 500. The "amendment type missing" flag is never shown. | `steward/disclosure/ownership_read.py:85`, `ownership.py:70-86` | open · re-checked |
| D40 | medium | The 13F trend uses the newest period any manager has filed, so early Q4 filers are compared with a full Q3, which shows a mass exit. | `steward/disclosure/ownership_read.py:66,122-144` | open · re-checked |
| D41 | medium | The 13D/G lane sums the latest filing per filer CIK, so a parent and subsidiary (or group members) filing for one stake are double counted. Old 13Gs stay current, because lag is filed-minus-event, not age at `as_of`. | `steward/disclosure/ownership_read.py:157-187` | open · re-checked |
| D42 | medium | Dependency upserts ignore the denominator, so "Customer X 23 % of revenue" and "18 % of receivables" in one note overwrite each other's share. | `steward/graph/relationships.py:86-88`, `disclosure/concentration_store.py:32` | open · re-checked |
| D43 | medium | Note table rows are always typed `customer`, take the whole note's denominator, and segment or product rows ("Automotive 30 %") become named customer drafts. They stay suggested, so a reviewer must catch them. | `steward/disclosure/concentration_extract.py:54,229-269` | open · re-checked |
| D46 | medium | Group claims ("three customers 45 %") are treated as the largest single customer in `largest_customer_weighted`, `concentrated_customer` and the screen. Both queries use the current `status='confirmed'` rather than the status as of the date (contrary to P5.6). In the screen query, the rank is computed before the denominator filter. | `steward/disclosure/look_through.py:70-89`, `screening.py:374-387` | open · re-checked |
| D47 | medium | `portfolio_exposure` does not apply `one_per_counterparty`, so a 10-K and a 10-K/A (or sentence and table) double the flow. `max()` compares ISO dates with "FY2025" as text. The same flaw appears in `latest_edges` and `latest_relationships`. | `steward/disclosure/dependencies.py:389,555-573`, `look_through.py:84` | open · re-checked |
| D52 | medium | The insider screen SQL bypasses the insider logic: supersession as stored now, no owner overrides, and `rule_10b5_1_plan is false`, which excludes unknown-plan buys that `net_by_role` counts. | `steward/disclosure/screening.py:350-361` | open · re-checked |
| D53 | medium | The stake screen takes the latest control-stake filing with no CUSIP-match or same-class check, so a class change shows a spurious stake change. | `steward/disclosure/screening.py:363-372` | open · re-checked |
| D54 | medium | PDMR `dedupe` collapses only identical rows, so a corrected notice with a different price or volume is counted alongside the original. | `steward/disclosure/pdmr_activity.py:50-61` | open · re-checked |
| D55 | medium | `accept_unflagged` stores model-agrees-with-extractor items as person labels, which inflates the gate's precision. `adjudication_label` takes `machine_value`, `item_ref` and `sample_key` from the client. | `steward/disclosure/adjudication_assist.py:166-193`, `adjudication.py:36-38`, `api/disclosure_routes.py:1425` | open · re-checked (partial: the gate counts `accepted_suggestion` on purpose) |
| D56 | low | Same-day 13F-HR and RESTATEMENT are ordered by accession when `known_at` ties (end of day), so the HR can reset over the restatement. | `steward/disclosure/ownership.py:65` | open |
| D57 | low | `percent_of_outstanding` divides split-restated shares by an un-restated share count. `position_change` ignores the owner's class override. | `steward/disclosure/insider_analysis.py:196`, `insiders.py:379` | open |
| D58 | low | `insider_classification_override` cascades on delete, and the collector deletes and reinserts a filing's rows, so re-ingesting a filing deletes the owner's overrides. | `supabase/migrations/20261007120000_insider_and_dependency_shapes.sql:39`, `ownership/insider_collect.py:121` | open |
| D59 | low | A ceased PSC with no `reported.as_of` is treated as current. | `steward/disclosure/control_corroboration.py:185` | open |
| D60 | low | Shock coverage sums reported and owner-assigned edges for the same pair. | `steward/disclosure/dependencies.py:457` | open |

## 9. Disclosure: deals, estimates and alerts (P6–P8)

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| D44 | high | `parse_number` misreads two number forms. `$(1.20)` is not negative (the leading `$` defeats the bracket test). A footnote marker `1.20 (1)` becomes 1.201. Neither raises an issue, so revision and surprise signs flip. | `steward/disclosure/estimate_extract.py:150-167` | open · re-checked |
| D45 | high | `\bGAAP\b` matches inside "non-GAAP", so an adjusted-EPS table is stored as GAAP and compared with GAAP actuals. | `steward/disclosure/estimate_extract.py:313-316,451` | open · re-checked |
| D48 | medium | Surprise scoring blocks a *differing* basis but scores an actual with no basis, with only a warning. SEC-mirrored actuals carry `as_filed`, which never equals GAAP or adjusted, so those are never scored. Basis matching is therefore unreliable both ways. | `steward/disclosure/estimates.py:525-542`, `fact_evidence.py:158` | open · re-checked |
| D49 | medium | Deal term versions are numbered `max+1` regardless of `known_at`, and "latest" is the max `version_no`. A late-processed older filing (the target ingested after the acquirer, or a retry) displaces newer terms. | `steward/disclosure/deal_job.py:267-335`, `deals.py:350` | open · re-checked |
| D50 | medium | The job and the reader disagree on state order: `resolve_state` also rejects an earlier `effective_at`. A definitive agreement dated before the press-release announcement is stored but rejected, so the deal stays "announced". | `steward/disclosure/deals.py:277`, `deal_job.py:433-448` | open · re-checked |
| D51 | low | Estimate tables with the "(in millions)" caption outside the header get scale 0 with no issue, so revenue 1,250 is stored as base units (about a -100 % "miss"). | `steward/disclosure/estimate_extract.py:283-290` | open · re-checked (overstated, moved to low) |
| D61 | medium | Money scales lack "B", "mn" and "bln", so "$2.5B" is read as 2.5 and "£500mn" as 500. | `steward/disclosure/deal_extract.py:48-51` | open · re-checked |
| D62 | medium | `guess_type` returns the first hint in a fixed order across the whole document, so an M&A 8-K that mentions a credit facility or repurchase is typed debt or buyback. | `steward/disclosure/deal_extract.py:77-92,282-287` | open · re-checked |
| D63 | medium | `cash_amount` and `stock_amount` take any "$X in cash/stock", so "The Company had $1.2 billion in cash" becomes the consideration. | `steward/disclosure/deal_extract.py:226-227` | open · re-checked |
| D64 | medium | `collect_consensus` catches only `PostgresError`. A `ParseError` (a ValueError) from one empty IR page aborts the step. | `steward/disclosure/estimate_store.py:226-232` | open · re-checked |
| D65 | medium | The `_actuals` dedupe key omits basis, unit, currency and dimensions, so a later adjusted or dimensioned row overwrites the GAAP actual. With a NULL start, FY and Q4 collide. | `steward/disclosure/estimate_read.py:176-186` | open · re-checked |
| D66 | medium | `segment_bridge` keys estimates by segment name only (FY and Q4 share a period end), and never checks basis. | `steward/disclosure/estimates.py:628-640,687` | open · re-checked |
| D67 | medium | The debt-maturity alert silently drops "review" buckets and computes the near-term share from what remains, which can fire a false "due within a year". | `steward/disclosure/alerts.py:511-526` | open · re-checked |
| D68 | low | Deal and holder alerts window on SEC acceptance time against now−7d, so a filing ingested more than 7 days late never alerts. Throttled alerts are lost. | `steward/disclosure/alerts.py:192-193,594-605` | open |
| D69 | low | `dilution` applies the exchange ratio to all target shares and ignores `stock_pct`. | `steward/disclosure/deals.py:567-593` | open |
| D70 | low | `stake_pct` reads a tender's "minimum condition of 51 %" as the stake sought. A$, HK$ and NZ$ are labelled USD. | `steward/disclosure/deal_extract.py:34-36,257` | open |
| D71 | low | Closing 8-Ks usually lack the party sentence, so the closed state may never be recorded. Items 2.03 and 3.02 (financing) are not read. A repeat same-state filing counts as a rejected transition. | `steward/disclosure/deal_job.py:93-143,447` | open |
| D72 | low | `DealTransitionOut.document_version_id` is a required int, so a NULL makes the response validation fail with a 500. | `api/disclosure_deals_routes.py:46` | open |
| D73 | low | Coverage keys by `issuer_id`, so dual-class tickers overwrite each other. `HOLDINGS_SQL` inner-joins `instrument`, which drops holdings without one from the denominator. | `steward/disclosure/coverage_report.py:383,619` | open |
| D74 | low | Answer sentences omit period end, unit and segment, and `phrase` checks only numbers and dates, so qualitative words ("beat", "grew") can be added. | `steward/disclosure/answer.py:171-201` | open |
| D75 | low | Fiscal years are assumed to be named by their end year, which is off by one for retailers named by the start year (Home Depot, Target). | `steward/disclosure/estimate_extract.py:238` | open |

## 10. Web

Paths are under `apps/web/src/`. The web slice covered `lib/`, the X-Ray, performance, discover, risk and estimates views, and part of `components/disclosure`. It did not cover most of the deals, network and statements components, the journal, release, funds and companies views, saved reports, or a check of the typed client against the backend routes. The slice found no `dangerouslySetInnerHTML` on data, no unauthenticated server fetches and no UTC-versus-local date bugs.

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| W1 | medium | `growth()` compounds the backtest in the browser and stops at the first null period, while the backend skips such periods. The line ends early while the "After costs" headline covers every period, which breaks the backend-computes rule. | `app/(app)/discover/backtest/backtest-view.tsx:44-55` | open · re-checked |
| W2 | medium | A null `twr_annualized` always reads "under a year", but the backend also returns null for an unavailable TWR or a total loss, so a 5Y window with a missing TWR is mislabelled. | `app/(app)/performance/returns-view.tsx:~115` | open · re-checked |
| W3 | medium | When the server's `view.net` is empty, the insider net is recomputed in the browser with a client copy of the rules meant only for the sample. | `components/disclosure/insiders.tsx:114-116` | open · re-checked |
| W4 | low | When the fetcher changes (scorecard filter, X-Ray `as_of`), the old data stays on screen during the request and after a failure. The X-Ray coverage strip labels the old snapshot "against {new asOf}". | `lib/hooks/use-api-resource.ts`, `ResourceGate` | open · re-checked (keeping data after a failed refetch is the hook's documented design; the defect is only that a changed query shows the old query's data) |
| W5 | low | `unknown_fund_weight ?? 0` renders "0.0%" when `unknown` is null, instead of "—". | `app/(app)/xray/exposure-view.tsx:46` | open |
| W6 | low | A comma is read as a decimal point, so "buy VUAA 1,500" becomes 1.5 and a "1,000" bp rate becomes 1. | `lib/risk/risk.ts:20-45,110`, `lib/discover/discover.ts` (`buildFilters`) | open |
| W7 | low | `percentileLabel` hard-codes "th" ("21th", "2th"), and `gridMatrix` maps a null shift to a 0 row. | `lib/company/company.ts` | open |
| W8 | low | Pretrade and rebalance keep the previous result when a run fails, and a network error leaves `busy` stuck on. | `pretrade-view.tsx`, `rebalance-view.tsx` | open |
| W9 | low | `source_url` and `stable_url` are rendered as raw `href`s with no http(s) check, which allows a `javascript:` link. The backend side is D28. | `components/disclosure/*.tsx`, `SourceLink` | open |
| W10 | low | The estimates ticker regex allows "." and "..", so the request path can normalise to another route. The usage beacon skips any path with "." (BRK.B). | `app/(app)/disclosures/estimates/estimates-view.tsx`, `usage-beacon.tsx` | open |
| W11 | low | Composition-strip bars are normalised to the total but the legend shows raw weights, so the two disagree when the weights do not sum to 100 %. | `components/xray/composition-strip.tsx` | open |
| W12 | low | `bg-surface-sunken` is undefined but still used in 17 files, so those panels have no background: the company tabs, the discover backtest and provider panel, and the fund views. It was flagged in the plan and is still open. | `app/(app)/companies/[ticker]/*`, `discover/*`, `funds/*` | open |

### 10a. Second web pass (remaining views and API contract)

**Contract check.** A fresh `app.openapi()` export is identical to the committed `openapi.json`. `schema.d.ts` has the same 389 paths. Every route is in the spec except six `/disclosures/company/{ticker}/*` aliases, which are hidden on purpose. Every typed call that was read matches its route, and openapi-fetch encodes path parameters, so tickers containing "." and ISINs are safe. Model shapes in `schema.d.ts` were not compared field by field.

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| W13 | medium | Saved adjudication labels are not shown after a reload: sample items carry no `human_value` and `labels` starts empty. The page reads "Choose a label" for items the gate already counts, so they can be relabelled and double-counted. | `app/(app)/disclosures/adjudication/adjudication-view.tsx:35,216` | open · re-checked |
| W14 | medium | If `/release/activation` fails or is still loading, `on` is empty, so every feature shows "Off" with an enabled "Switch on" button and no error. | `app/(app)/release/readiness-view.tsx:29-30,57` | open · re-checked |
| W15 | medium | A feature that is switched on but now fails the gate shows "On: you switched this feature on" under "Stays off". The page never says it should be switched off (backend side: M7). | `app/(app)/release/readiness-view.tsx:57,119` | open · re-checked |
| W16 | medium | Fund facts are saved one PUT at a time. If entry 2 fails, entry 1 is already stored, `onSaved` is not called and the form keeps every value, so a retry duplicates entry 1 in the append-only history. | `app/(app)/funds/[isin]/record-facts-form.tsx:53-66` | open · re-checked |
| W17 | low | The checklist shows "Nothing recorded yet" while the facts history is loading or has failed, which presents unknown as empty. `FactForm` has no busy guard, so a double click appends two facts. | `app/(app)/funds/[isin]/checklist-view.tsx:127,153` | open |
| W18 | low | "Open the full view" on an alert links to the bare page with no ticker. | `lib/disclosure/alerts.ts:24`, `disclosures/alerts/alerts-view.tsx:215` | open |
| W19 | low | "Show N read alerts" counts only read alerts inside the recent-limit window, although the panel says nothing is deleted. | `disclosures/alerts/alerts-view.tsx:45,193` | open |
| W20 | low | The journal sends no limit, so `GET /journal` returns the default 200. Older entries vanish, the heading says "200 decisions", and older reviewed entries show as unreviewed. Second instance of M14. | `app/(app)/journal/journal-view.tsx:28,174` | open |
| W21 | low | Fund compare fetches even with a single ISIN, and the 4xx response is never shown. | `app/(app)/funds/compare/compare-view.tsx:42` | open |
| W22 | low | Peer scope "group" with no group chosen calls `/peers` without `group_id`, instead of prompting the owner to choose a group. | `companies/[ticker]/peers/peers-view.tsx:209-221` | open |
| W23 | low | `Math.max(...[])` gives "No update for Infinity days" for an active deal with no transitions or terms. | `components/disclosure/deals.tsx:141` | open |
| W24 | low | The case-item archive DELETE ignores errors, so a failed archive looks like success. | `companies/[ticker]/case/case-view.tsx:89` | open |
| W25 | low | Usage beacon paths containing "." (BRK.B) are not counted, so pilot page use is undercounted. Part of W10. | `components/release/usage-beacon.tsx:13` | open |
| W26 | low | `String(Number(percent)/100)` can send "0.14100000000000001", and the `as_of` default is the UTC date, not the local one. | `components/disclosure/network.tsx:417,430` | open |

Not covered in the second pass: the remaining `companies/[ticker]` views (overview, evidence, ratios, segments, valuation, quality); `components/disclosure` ownership, statements, panels and identifiers; and the disclosures index, deals and network pages.

## 11. Second backend pass (skipped modules and schema)

**Checked and clean:**
- The `ratio_set.py` ratios outside D3/D4: n/m handling, `abs()` on capex, D&A and interest, and opening-plus-closing averaging.
- The `risk.py` formulas and minimum-sample gates.
- Every `ON CONFLICT` target in `apps/backend/src`, which matches a unique index, primary key or inline unique.
- The CHECK lists on `quality_review`, `disclosure_alert`, adjudication datasets, `uk_tr1_notice`, form 13F and `price_bar`, which accept every value the code writes.
- The 13F dollars-versus-thousands cutoff and the put/call exclusion.

| Id | Severity | Finding | Evidence | Status |
| --- | --- | --- | --- | --- |
| B1 | low | `equity` maps both parent-only tags (`StockholdersEquity`, `EquityAttributableToOwnersOfParent`) and total-including-NCI tags (`…IncludingPortionAttributableToNoncontrollingInterest`, `ifrs-full:Equity`); `net_income` likewise includes `ProfitLoss`. For a filer that tags only the total, ROIC invested capital adds NCI twice, the `_liabilities` fallback subtracts it twice, and ROE and P/B mix scopes. Related to D34. | `steward/sources/xbrl_concepts.py:197-208`, `analytics/ratio_set.py:207-211`, `company/standard.py:81-86` | open · re-checked (the parent-only tags are listed first, so only filers without them are affected) |
| B2 | medium | Review `_apply` handles only `claim`, `observation` and `identity_mapping`. An `estimate_value` review (enqueued by alerts) or a `document_version` review closes with an audit event but changes nothing, although the docstring says the decision applies to the subject. `superseded` on a mapping has no branch either. `enqueue` then refuses to reopen a decided subject. | `steward/disclosure/review.py:108-175`, `alerts.py:416` | open · re-checked |
| B3 | medium | The rollback of `20261008200000_disclosure_audit_fixes` rebuilds the old, narrower unique indexes on `disclosure_observation` and `estimate_value`. Rows that the widened index allowed (YTD and quarter with the same `period_end`) make `create unique index` fail, so the rollback aborts. | `supabase/rollback/20261008200000_disclosure_audit_fixes.down.sql:104-108` | open · re-checked |
| B4 | low | `version_span` returns the span and context computed by a different parser version, only flagging `stale_parser` (`parser_version` None reads as not stale). It has no `known_at` guard. | `api/disclosure_routes.py:1502-1550` | open |
| B5 | low | `parse_exhibits` splits on every "Exhibit N" mention, so an "incorporated by reference to Exhibit 99.1" phrase makes a truncated row and a bogus duplicate, and lettered exhibits are missed. | `steward/sources/schedule13.py` (`parse_exhibits`) | open |
| B6 | low | The anonymous-label path returns a hard-coded `"status": "confirmed"`, so a rejected mapping reports confirmed on rescan. The upsert overwrites `candidate_ids` and `score` on decided rows. | `steward/reference/disclosure_identity.py:154-185` | open |
| B7 | low | The disclosure 13F holdings query has no `shares_type='SH'` filter (the other readers have it), and `effective_positions` sums `shares or ZERO`, publishing a partial total. | `steward/disclosure/ownership_read.py:72-77`, `ownership.py:177` | open |
| B8 | low | `var_es` returns a negative "loss" when all returns are positive, with no note. `diversification_benefit` accepts negative weights. An unknown frequency raises KeyError. | `steward/analytics/risk.py` | open |
| B9 | low | FMP compensation ignores component changes when the total is unchanged. `published_at` is midnight UTC of the filing date, which can precede the real filing. | `steward/sources/fmp.py` | open |
| B10 | low | The `defect_fixes` rollback does not reverse `alter function private.screen_version_immutable() set search_path`. | `supabase/rollback/20261008100000_defect_fixes.down.sql` | open |
| B11 | low | Retiring an owner dependency weight filters by ticker and id only (`_owner` unused). This matters only if the app becomes multi-user. | `api/disclosure_routes.py:1150-1162` | open |

Not covered in the second pass: the `tr1_uk` value regex; the edinet, audit and provider-jobs diffs; the series builder's concept priority (which decides B1's impact); and per-column NOT NULL checks outside the tables above.

## Suggested fix order

1. **D1, D3, D2, D39, D19:** these are cheap, contained and affect what the owner sees directly.
2. **D44, D45, D31, D32:** confirm D32 against one live filing first.
3. **Point-in-time cluster:** D7/D52, D46, D38a, F6. These all read current status or supersession instead of as-of status, and share one fix pattern (`graph_status_log` and `known_at`-bounded supersession).
4. **Currency and basis cluster:** D22, D48, D65, M2, M9, F2, F3.
5. The rest, by area.
