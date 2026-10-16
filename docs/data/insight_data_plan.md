# Insight data plan: when each feature has its data, and how it gets into the database

Written 2026-10-16 from the code (job registry, schedules in `steward/config/jobs.py`, each
feature's read path). The live state of the Railway database is measured by
[`insight_data_readiness.sql`](insight_data_readiness.sql) (read-only; tested against the
migrated schema in `tests/test_insight_readiness_sql.py`) and by **Data quality → Insight
coverage** in the app. Times are Europe/Prague.

## 1. Three kinds of data

| Kind | Meaning | Examples |
| --- | --- | --- |
| **Backfillable** | A first run loads years of history | prices (10 y), FX (20 y), Trading212 ledger (all), Form 4 (5 y), 13F (8 quarters), Yahoo dividends (10 y) |
| **Throttled** | History exists at the source but quotas spread the first fill over weeks | Twelve Data actions (6 holdings/run, weekly), Alpha Vantage statements (4/run, weekly), FMP (250 calls/day) |
| **Time-gated** | Only accumulates from the first run; cannot be backfilled | fair-value history, dividend-forecast snapshots, consensus snapshots, position snapshots |

Time-gated series are the ones to start **now**: every week they are not running is a week the
3/6/12-month features (forecast accuracy, consensus track record, fair-value history) slip.

## 2. Feature by feature

| Feature | Reads | Answers once | Filled by (schedule) | Gate |
| --- | --- | --- | --- | --- |
| Dividend history, schedule, calendar | `corporate_action`, `transaction` | a holding has ≥ 4 payments (`min_payments_for_estimate`); "ready" at confirmed events over 3 calendar years | `yahoo_dividends` Sat 11:20 (10 y, 15 holdings/run), `twelve_data_corporate_actions` Sat 10:50 (365 d, 6 holdings/run), `dividend_forecast` daily 08:30 confirms matched events | throttled: 2 Saturdays (Yahoo) to 5 Saturdays (Twelve Data) for 30 holdings |
| Dividend forecast accuracy | `dividend_forecast_snapshot` | 3 / 6 / 12 months after the first snapshot | `dividend_forecast` daily | **time-gated** |
| Checks: past, health, dividend axes | `financial_statement_fact` (FY, no dimensions), prices | 3 fiscal years for most checks; 5-year growth needs 6 FY; 10-year DPS growth needs 11 | `sec_xbrl` Tue 05:40 (latest 10-K/20-F + 10-Q only, so about 3 FY), `valuation_refresh` daily (ESEF), `alpha_vantage_statements` | **depth gap**: deeper history only from `sec_fsds_backfill` (see §4) |
| Checks: future axis, multiple path seed, forward metrics | `forward_observation` (`published_analyst_consensus`) | ≥ 2 future periods | `fmp_company` Sat 09:20 (20 holdings/run) | FMP plan: about 13 of 24 eligible holdings; 2 Saturdays to reach all 30 |
| Checks: value axis, valuation pictures, strips | peers (`peer_set` confirmed, `company_peer_group`, or the sector of the covered universe), sector from FMP profile | 5 peers (`min_peers_for_median`); histogram 20 universe members (`min_universe_for_percentile`) | `ai_approvals` daily 07:10 (Finnhub peers as *suggested*), `fmp_company` (sector) | **owner step**: suggested peers must be confirmed, or save a peer group |
| Price target, agreement, chart, track record | `consensus_snapshot`, `provider_access` | latest snapshot `complete`; chart 8 snapshots; track record 12 months | `insight_access_probe` Sun 11:00, then `consensus_snapshot` Sat 11:40 | **time-gated and plan-gated**: weekly, so 8 weeks to a chart; free plans may answer `paid_only` |
| Fair value (portfolio valuation, Check-In step line) | `fair_value_point` | a basis exists; history readable at 8 dated points | `fair_value_snapshot` daily 09:15 | **time-gated**; DCF basis needs statements; multiple/thesis bases need the owner's saved scenario or thesis |
| Scorecard, score marks | `company_check_snapshot` | a check snapshot per holding | `checks_refresh` daily 09:45 | follows statements and prices |
| Risk score, per-holding risk, correlation | `price_bar` (holdings and benchmark `^spx`), weights | 60 daily closes (volatility, beta, correlation), 250 (VaR / shortfall) | `price_history` weekdays 23:15 (10 y holdings, 20 y benchmarks) | backfillable: first run |
| Returns split, positions, closed positions | `transaction`, `position_snapshot`, `fx_rate` | first sync; annual rate after 30 days held | `portfolio_sync` daily 22:30 | backfillable (ledger); a position bought before the first stored fill carries a warning |
| Diversification: sankey, concentration | X-Ray look-through (`etf_holding`, `nport_holding`), sectors | first X-Ray build | `etf_look_through` Sat 06:40, `nport_holdings` Sat 05:15 | funds must be listed in `look_through.funds` |
| Diversification: geography | `company_segment_map` (geographic) from `segment_revenue` | coverage shown as "n % of value" | `sec_xbrl` (US/20-F filers), `watchdog` auto-fill hourly | non-SEC issuers need a manual segment map |
| Ownership mix | `institutional_holding` (13F), `insider_transaction`, `shares_outstanding` | a 13F period and shares outstanding | `ownership_13f` 5th of month (2 data sets/run, 8 wanted), `insider_trades` daily 06:30 (5 y first load), `sec_xbrl` (shares) | backfillable but slow on schedule: 4 months of monthly runs, or one manual backfill |
| CEO pay, tenure, board tenure | proxy pay facts (`financial_statement_fact`, form DEF 14A), FMP `executive_compensation`, Form 4 | any pay row; tenure from Form 4 | `sec_fsds_backfill` (**off, and cannot run on schedule**, §4), `fmp_company` | **gap** |
| Earnings summary | `evidence` + `fact`, storage, LLM key | a stored release with extracted figures | `earnings_check`, queued by `earnings_due` daily 08:00 | forward only: one per reported quarter |
| Updates feed | `inbox_item`, `story_cluster`/`news_item`, `corporate_action`, changes | anything in the last 14 days | `news_ingest` every 2 h, `inbox_scan` hourly | needs an LLM key for news triage |
| Insider monthly strip | `insider_filing`, `insider_transaction` | one filing | `insider_trades` | backfillable |
| Clocks retrospective | the three time-gated series above | 3 / 6 / 12 months after the **slowest** clock starts | as above | time-gated |

## 3. First fill on Railway, in order

The database is `postgres.railway.internal`, reachable only from inside Railway (no TCP proxy), so
`worker.backfill` cannot be run from a laptop against it. It does not need to be: the worker's
catch-up on start runs every data job that never succeeded or missed its slot, in this order:
`source_audit`, the core data jobs, the source jobs (incl. `yahoo_dividends`), `sec_fsds_backfill`,
`fact_reconcile`, then the insight snapshots (`insight_access_probe`, `consensus_snapshot`,
`dividend_forecast`, `fair_value_snapshot`, `checks_refresh`). Deploying the insight work is the
first fill. One data set by hand, from a shell inside Railway:
`uv run python -m worker.backfill sec_fsds_backfill --param quarter=2024q4`.

Owner steps the jobs cannot do:

1. **Confirm peers** (company page → Peers) or save a peer group, for every holding the value
   checks and pictures should cover.
2. **Save a valuation scenario or thesis target** per holding, so `fair_value_point` records the
   `multiple_base` / `thesis_target` bases from day one.
3. **Confirm suggested dividend events** in the Inbox queue (or keep `auto_confirm` on).
4. **Segment map** for non-SEC holdings that should count in the geography view.
5. List the funds to look through in **Settings → Jobs → look_through.funds**.

## 4. Gaps in how data reaches the database

Status 2026-10-16: G1, G2 and G4 are built (below); G3 is changed by the live finding that Twelve
Data's corporate actions are paid-only; G5 stays an owner step.

- **G1 built.** The data-set URL was wrong (`financial-statement-and-notes-data-sets`, a 404 for
  every set); it is `financial-statement-notes-data-sets/{2026_09|2025q3}_notes.zip`. The job runs
  daily: monthly sets for the last 12 months, quarterly before, back `fsds.history_months` (60),
  `fsds.datasets_per_run` (2) a run, newest first; what was read (and for which CIKs, so a new
  holding is back-filled too) is kept in `sync_state` key `fsds_datasets`; a 404 is rechecked
  after 3 days. Parsing runs off the event loop. First full fill: about 2 weeks.
- **G2 built.** The insight snapshots and the FSDS backfill are in `CATCH_UP_SEQUENCE`.
- **G3 revised.** Twelve Data `corporate_actions` answers 403 on the current plan and is latched
  off, so Yahoo is the only automatic dividend source; `yahoo_dividends` now runs daily (15
  holdings a run, each refreshed about weekly) and shipped ahead of the insight work (PR #35).
  Yahoo needs a confirmed listing: CUPd, D4Sd and NU have none yet (owner step).
- **G4 built.** `consensus_snapshot` runs daily.

| # | Gap | Effect | Change |
| --- | --- | --- | --- |
| G1 | `sec_fsds_backfill` needs a `quarter` parameter, but the schedule passes none, the backfill CLI cannot pass one and no API route exists | Proxy (DEF 14A) pay never lands; statement history stays at about 3 fiscal years, so 5- and 10-year checks, multiple history and DPS growth stay unknown | Without a parameter, take the newest published quarter not yet loaded and walk back to a configured `history_quarters` (one data set per run, as 13F does); add it to the backfill/catch-up sequence; allow `--param quarter=2025q1` on the CLI |
| G2 | Worker catch-up skips `insight_access_probe`, `consensus_snapshot`, `dividend_forecast`, `fair_value_snapshot`, `checks_refresh` | After a deploy the clocks wait for their cron slot (the probe for Sunday, consensus for Saturday) | Append them to `CATCH_UP_SEQUENCE` after the source jobs |
| G3 | Twelve Data covers 6 holdings per weekly run (2 credits each, `TWELVE_DATA_HOLDINGS_PER_RUN`), Yahoo 15, FMP 20 (`FMP_HOLDINGS_PER_RUN`) | Corroborated dividend history for 30 holdings takes 2 to 5 weeks; FMP estimates and sector 2 weeks | Run the Twelve Data job daily rotating the least-recently-checked holdings (12 of its 40 daily credits), and the Yahoo and FMP jobs until every holding has been covered once |
| G4 | `consensus_snapshot` is weekly | 8 snapshots for the chart need 8 weeks | Daily is affordable on Finnhub (200/day) for 30 holdings; the chart then appears after 8 days |
| G5 | Peers arrive only as *suggested* | Value checks and pictures stay unknown until the owner confirms | Keep the owner step (peers are a judgement), but surface a "confirm peers" queue item per holding |

## 5. When everything is there

Assuming the first fill (§3) runs today and G1–G4 are done:

| Data | Ready |
| --- | --- |
| Prices, FX, ledger, Form 4, 13F, Yahoo dividends, statements to the depth loaded, risk score, returns, ownership, insider strip | the day of the first fill |
| Corroborated dividend history for every holding | about one week (two weeks without G3) |
| Fair-value history (8 points), consensus chart (8 snapshots with G4) | about 8 days (8 weeks for the chart without G4) |
| Peer-based checks and pictures | as soon as peers are confirmed |
| Deep statement history and proxy pay (G1) | two FSDS data sets a day: the last year in 6 days, 5 years in about 2 weeks |
| Clocks 3-month checkpoint | 3 months after the slowest clock starts (mid-January 2027 if started now) |
| Clocks 6-month, consensus track record, forecast accuracy at 12 months | mid-April 2027 and mid-October 2027 |
| Estimates for the uncovered ~11 holdings | not on the current FMP plan; a paid plan or another estimates source |

## 6. What the live database had (2026-10-16, read before the move to Railway Postgres)

Read-only, from the Supabase copy the app used until 2026-10-15 20:23 (job ids continue on
Railway, so the data was carried over). Production runs `main`: none of the insight tables
exist until this work is deployed.

- 30 holdings; position snapshots for 12 days (since 2026-09-27). Benchmarks ^GSPC and ^STOXX and
  ECB FX: 20 years; CNB FX: 9 days.
- Prices: about 2,500 closes for each US holding; none for CUPd and D4Sd (listing only suggested),
  NU (no listing; bars stored as `NU`), SXRSd / SXRVd and BRNTl.
- Statements: FY2023-FY2025 only (the live XBRL job's reach) for every SEC filer; none for CSG,
  Chugai, Daiichi or the funds.
- Estimates for 17 holdings; confirmed peers (5 or more) only for AVGO, CELH, UNH and KO.
- Dividends: 4 suggested Twelve Data rows in `corporate_action`, 7 Trading212 dividend receipts.
- Form 4 and 13F rich; FMP pay for 13 holdings.
