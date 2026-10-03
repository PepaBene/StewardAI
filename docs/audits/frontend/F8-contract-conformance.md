# F8 audit: contract conformance of frontend parsers and untyped-endpoint consumers

Baseline audited: commit bd5f1fd (checked out via `git archive` into the scratchpad). The working tree was edited concurrently by someone else while I worked; each finding says whether the working tree already fixes it.

Method
- Real backend payloads: I ran the backend tests with an ASGI capture plugin, and my own seeded scenario (macro, trade, graph, deals, concentration, patents, funds, sources) through `create_app()` against a migrated Postgres. About 40 endpoints and 43 parser or adapter entry points were fed the captured JSON. Each parser ran behind a Proxy that records keys read and keys read-but-absent.
- Result: 0 parser failures or nulls on real payloads, and no key-name mismatch between any parser and what the backend returns. The only absent keys were optional ones. `openapi.json` matches the live app, and `schema.d.ts` matches `openapi.json`.
- The remaining defects are semantic: units, enums, formulas.

### [F8-1] Portfolio price coverage table shows day change 100x too small
Severity: high
Status: CBE (fixed in the working tree, uncommitted)
Where: apps/web/src/components/markets/portfolio-coverage.tsx:87 (bd5f1fd); backend apps/backend/src/steward/portfolio/quotes.py:42,93,210
What: `HoldingPrices.day_change_pct` is a fraction (`CHANGE_HELP`: "0.012 = +1.2 %"; `_change` returns latest/previous-1). The table divides it by 100 again.
Failure scenario: a holding up 1.2% (`day_change_pct: 0.012`) shows `formatPct(0.012/100, 2, true)` = "+0.01%". A 5% move shows "+0.05%".
Evidence: `h.day_change_pct === null ? "n/a" : formatPct(h.day_change_pct / 100, 2, true)`. `screens/logic.ts` and `market-strip.tsx` correctly treat the same field as a fraction.
Suggested fix: `formatPct(h.day_change_pct, 2, true)`. The working tree now has exactly this.

### [F8-2] Earnings surprise sign inverted when the consensus is negative; backend `surprise_percent` ignored
Severity: medium
Status: CBE (reproduced in vitest; fixed in the working tree, uncommitted)
Where: apps/web/src/lib/financials/logic.ts:764-771 (`surprise()`), apps/web/src/components/financials/earnings-card.tsx:95 (method text); backend apps/backend/src/steward/fundamentals/ir.py:825-832
What: the backend computes `surprise = actual - expected` and `surprise_percent = surprise / abs(expected)`, a fraction. The frontend parses `surprise_percent` into `surprisePct` but never uses it. It shows `actual / consensus - 1`, and the footer states that formula.
Failure scenario: EPS consensus -0.50, actual -0.30 (a beat). Backend gives +0.40. Frontend gives (-0.30 / -0.50) - 1 = -0.40, so it shows "-40.0%" as a miss. Repro output: `backend surprise_percent 0.4 frontend pct shown -0.4`. Loss-making companies get the wrong sign.
Suggested fix: use `avc.surprisePct`, or divide by `Math.abs(consensus)`. Fix the method text. The working tree now prints 0.4.

### [F8-3] Evidence ledger prints a fraction with a "%" sign
Severity: medium
Status: CBE (reproduced; fixed in the working tree, uncommitted)
Where: apps/web/src/lib/holding-data/evidence-items.ts:60 (13F holder row), :85 (dependency `reported_share`); backend apps/backend/src/steward/ownership/read.py:59-62 and :113-116 (`pct_of_shares_outstanding` is "shares x ads / outstanding, as a fraction"), supabase migration `reported_share between 0 and 1`
What: the 13F holder detail renders `${h.pct_of_shares_outstanding}% of outstanding` from a fraction. The dependency detail renders `reported_share` as a bare, unitless fraction.
Failure scenario: a holder with 1.23% of outstanding (backend 0.0123) shows "0.0123% of outstanding". Repro: `"1000 shares · 0.0123% of outstanding · a"`. The xray page uses `formatOutstandingPct` correctly, so the two views disagree.
Suggested fix: format both as percent (x100). The working tree now prints "1.23%".

### [F8-4] Macro view maps the valid backend state `temporarily_failed` to "unrecognised"; a second state map disagrees with the shared one
Severity: low
Status: CBE (repro; fixed in the working tree, uncommitted)
Where: apps/web/src/lib/macro/parse.ts:64-73 (own `toDataState`); backend apps/backend/src/steward/macro/read.py (`coalesce(pa.state,'available') access_state`; coverage `c.state`); shared map apps/web/src/lib/data-state/states.ts:121-136
What: `ContextPoint.access_state` is `provider_access.state` and `MacroCoverage.state` is `coverage_status.state`. Both can be `temporarily_failed`. The macro-local mapper only knows `isDataState` strings plus `available`, `ok`, `verified` and `vintage_unavailable`.
Failure scenario: an observation whose provider last failed gets state "unavailable" with tooltip `Unrecognised source state "temporarily_failed".` Repro confirmed. The macro page also maps `vintage_unavailable` to "partial", while the shared `BACKEND_STATE_MAP` and the Coverage atlas map it to "not_covered", so the same ledger row shows two states.
Suggested fix: use `parseDataState` from lib/data-state. The working tree now does this.

### [F8-5] Deal terms: `ownership_pct` and `interest_rate_pct` shown without a percent sign
Severity: low
Status: CBE by reading (fixed in the working tree, uncommitted)
Where: apps/web/src/lib/network/logic.ts:537 (`PCT_KEYS = ["cash_pct","stock_pct"]`); backend apps/backend/src/steward/graph/deals.py:125-142 (`_DECIMAL_KEYS` includes `ownership_pct`, `interest_rate_pct`, 0-100 values)
What: `termRows` promises "units explicit (percent keys are already 0-100)" but two of the four percent keys fall through to a bare number.
Failure scenario: confirmed term `ownership_pct: "12.5"` renders "12.5" beside the 0.125 fractions used elsewhere in the same panel.
Suggested fix: add both keys to `PCT_KEYS`. The working tree now has them.

### [F8-6] Evidence tier `unverified` collapses to "unknown"
Severity: low
Status: CBE by reading (fixed in the working tree, uncommitted)
Where: apps/web/src/lib/network/parse.ts:107 (`TIERS`); backend apps/backend/src/steward/graph/decisions.py `tier_value()` returns "unverified" for an unclassifiable source and stores it in `evidence_tier`
What: the frontend list lacks `unverified`, so such edges and steps get tier "unknown".
Suggested fix: add `unverified`. The working tree has it.

### [F8-7] Trade context footer always says "Verified" regardless of data age
Severity: low
Status: SUSP (not fixed)
Where: apps/web/src/components/macro/trade-panel.tsx:237 (`state: t.latestPeriod ? "verified" : "not_covered"`)
What: `/macro/trade` carries no freshness, so any stored period reads "Verified". My capture showed `latest_period: 2025-01-01` on a response generated 2026-10, still shown "Verified". The period date is printed beside it, so the age is visible.
Confirm with: decide whether trade data should go "stale" past a threshold. A product call, not a contract bug.

## Examined and found sound
All checked against real or captured payloads unless noted.
- Parsers and adapters with no key or shape mismatch: macro (context, coverage/portfolio, exposures, trade, research-context), markets (corporate actions, returns, listing, sessions, expected sessions), financials (facts, forward/consensus, actual-vs-consensus, IR releases and sources, peer-set, peer-metrics), network (business-network, deals, concentration, patent trends and overlap, kpi links, observations, reconciliations, review queue, knock-on), holding-data network and patent summaries, screens (`adaptHeld`, `adaptPrice`, `adaptValuation`), xray (factsheet, audit), source-coverage (coverage, access, usage, status, zero-cost gate), coverage (summary, measured, data-context).
- Number representation: typed (response_model) endpoints send Decimal as a JSON string; `response_model=None` routes send a JSON number (jsonable_encoder). Every parser accepts both and never maps null or "" to 0. For typed `number|string` fields I found no arithmetic on an unconverted string; `tsc` would reject it.
- Units: valuation margins, ROE, ROIC and dividend yield, `ppl_pct`, `weight`, factsheet `expense_ratio` and weights, HHI (fraction, x10000 for points), pro-forma `share_count_increase` and `new_holders_ownership`, 13F `shares_change` and `pct_of_shares_outstanding` in xray, `reported_share`, `overlap_share`, `jaccard`, `ownership_confidence`, N-PORT `pct_of_fund` (percent, shown as percent), `stake_percent` (percent), plausibility `difference_pct` (percent, divided by 100 correctly).
- Enums: edge, event and disclosure statuses and the decision transition matrix (`decision-control` matches `TRANSITIONS`), deal states, LEDGER tiers, nport coverage states, trail rules and outcomes, `action_type`, forward kinds, peer statuses, insider economic classes, audit verdicts, `coverage_status.state` (7 values plus `vintage_unavailable`). A legacy `entity_relationship` edge (`id: "legacy:N"`) is handled.
- Dates: ISO date vs datetime handled by day-slicing; `asOfParam` is guarded against a future `as_of` in network/holding-data, and the financials variant has no 422 risk because those routes do not reject future cutoffs. Query parameter names and bodies for the owner POST and PUT calls match the typed schema.

## Not examined
- Frontend components' DOM and a11y, and fetch-hook race conditions (other auditors).
- `/funds/aggregate`, `/funds/overlap` and `/funds/coverage-ledger` as live payloads: typed, so only schema-checked, and `test_fund_lookthrough_phase6` was still running when I stopped.
- Real-provider data (EPO, Finnhub, PDL, Diffbot, ESEF) beyond the test fixtures.
- A cosmetic residue: `value_text`-only facts (CIK, document type) are not shown in the statements view by design (`value` is null).

## Note on the environment
I started the local Postgres cluster 16/main and stopped it again, and dropped my test databases. All throwaway vitest files (`__audit_tmp__*`) are deleted. No repository file was edited and nothing was committed. An untracked `apps/web/src/app/(app)/macro/macro-view.test.ts` is not mine.
