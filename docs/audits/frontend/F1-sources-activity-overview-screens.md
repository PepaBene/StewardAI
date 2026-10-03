# F1 audit: source coverage, data-state, overview data context, activity trail, coverage atlas, screens

Scope read: lib/{source-coverage,data-state,overview-data,activity/decision-trail,coverage,screens}, components/{sources,data-state,overview-data,coverage,screens}, app/(app)/activity/{source-coverage-panel,decision-trail-panel}, use-api-resource usage. Compared against api/source_routes.py, gate_routes.py, graph_routes.py (reset-block), steward/verify_sources/zero_cost.py + signal_audit.py, steward/valuation/read.py, steward/portfolio/read.py, steward/data_context/portfolio.py, steward/free_source_alerts.py. `npx tsc --noEmit` is clean. Repro tests were temporary and deleted.

Counts: high 2, medium 7, low 5, suspected 2 (listed inside those).

### [F1-01] Empty endpoint manifest makes the zero-cost gate read "passed" and the scorecard "Target met"
Severity: high
Status: CBE
Where: apps/web/src/lib/source-coverage/parse.ts:162-195 (parseZeroCostGate), apps/web/src/lib/coverage/logic.ts:costScore, apps/web/src/app/(app)/activity/source-coverage-panel.tsx:GateSummary
What: Backend `ZeroCostReport.passed` is `not violations`; with zero manifest rows `checked=0`, no findings, so `passed=true`. The parser only validates shape, so a vacuous audit is shown as a clean one. The scorecard turns it into "Target met" for "Actual spend remains $0".
Failure scenario: provider_endpoint_manifest empty (migration not applied/seeded, or table wiped) -> GET /verification/zero-cost-gate returns `{passed:true, checked:0, ... findings:[]}` -> Activity shows "Zero-cost gate: passed 0 manifest endpoints checked" and Coverage shows "Cost: Target met".
Evidence: repro `parseZeroCostGate({passed:true,checked:0,...,findings:[]})` -> `RESULT empty=true/met`. zero_cost.py:128 `passed = not self.violations`.
Suggested fix: in parseZeroCostGate (or costScore + GateSummary) treat `checked === 0` as unavailable/"nothing audited", never passed. Optionally also require violationCount to equal the number of violation findings (see F1-02).

### [F1-02] Gate body with `passed:true` and `violation_count>0` is accepted; the two views disagree
Severity: medium
Status: CBE
Where: apps/web/src/lib/source-coverage/parse.ts:186 (consistency check only looks at findings, not violationCount); source-coverage-panel.tsx GateSummary uses `gate.passed`; coverage/logic.ts:costScore uses `passed && violationCount===0`
What: The "passed while violating" guard compares `passed` to the findings list only. A body with `passed:true, violation_count:3, findings:[]` (truncated findings, a future backend that caps findings, or any proxy mangling) parses fine.
Failure scenario: Activity panel prints "Zero-cost gate: passed" (no violation list, because findings is empty) while the Coverage scorecard prints "Target not met" for the same body.
Evidence: repro `RESULT mismatch=true/3/not_met`.
Suggested fix: reject (return null) when `passed && violationCount>0`, `!passed && violationCount===0`, or violationCount !== count of violation findings.

### [F1-03] Monthly request-budget windows lose their reset time and history after day ~15 of the month
Severity: medium
Status: CBR
Where: apps/web/src/lib/source-coverage/hooks.ts:useSourceUsage (days=15) + logic.ts:buildBudgets (`current = windows.find(w.windowStart === a.quotaWindowStart)`)
What: Usage is fetched with `since = now - 15 days` and the backend filters `window_start >= since` (source_routes.py get_source_usage). A monthly window starts on the first of the period, so from ~day 16 it is outside the fetch, `current` is undefined, `resetsAt` becomes null ("reset time not reported") and the sparkline/history for month-capped endpoints is empty or lacks the current window. Used/remaining are unaffected (they come from /sources/access).
Failure scenario: Alpha Vantage / month-capped endpoint on 20 Oct: window_start 2026-10-01 < 2026-10-05 cutoff -> reset line says "reset time not reported", although the API knows the window.
Evidence: `windows` is built only from `usage` rows; `.filter(... u.windowKind === a.quotaPeriod)`. Backend: `where ($1 is null or window_start >= $1)`.
Suggested fix: fetch since = start of the current month minus the history needed, or per-period (day: 15 days, month: 14 months), or derive `resetsAt` from access.quota_window_start + period instead of requiring the usage row. Also note minute windows over 15 days are unbounded in row count.

### [F1-04] Decision-trail "Older decisions" is not guarded against filter changes; failed paging shows "End of the trail."
Severity: medium
Status: CBR
Where: apps/web/src/app/(app)/activity/decision-trail-panel.tsx:more(), and render branch `load.kind === "ok" && load.next !== null ? <Button> : <p>End of the trail.</p>`
What: (a) `more()` has no cancellation/sequence check and closes over the filter at click time. If the user changes rule/outcome/ticker while a page is in flight, the late response calls `setLoad({kind:"ok", rows: appendPage(oldRows, oldPage)})`, replacing the new filter's first page with old-filter rows under the new filter's controls. (b) When `more()` fails, state becomes `kind:"error"` with `next` kept but the button is gated on `kind==="ok"`, so the UI shows the error and the text "End of the trail." while older rows exist and cannot be retried without changing a filter.
Failure scenario: click "Older decisions", immediately pick outcome "raised": result list shows the unfiltered older rows labelled for outcome raised (counts line "N loaded: ..." also wrong). Or a 500 on page 2 -> "End of the trail."
Evidence: code above; backend paging itself (`id < before_id order by id desc`, `next_before_id`) is correct.
Suggested fix: keep a request token (ref incremented in the effect and in change()); drop stale responses; render the button for `kind==="error" && next!==null` (retry) and show "End of the trail" only when `kind==="ok" && next===null`.

### [F1-05] useApiResource keeps the previous filter's data as "ok"/"previous" when the fetcher changes (atlas filters)
Severity: medium
Status: CBR
Where: apps/web/src/lib/hooks/use-api-resource.ts (state is not reset when `fetcher` changes); consumers: components/coverage/atlas.tsx (useDataCoverage + useCoverageSummary with filters), lib/coverage/hooks.ts
What: On a filter change the hook refetches but leaves `state` at the old `ok` data (no loading state), and if the new request fails, `previous` is the old filter's data. ResourceGate then renders it under "refresh failed, showing the last loaded data" beside the new filter selection. The entity-key field updates the URL per keystroke so this window is hit constantly.
Failure scenario: filter provider=sec then provider=fred; request for fred returns 503 -> matrix, ledger rows and summary still show sec rows with the fred filter selected. Also during any slow response the matrix silently shows the previous filter's cells.
Evidence: `setState(prev => next.kind==="error" && prev.kind==="ok" ? {...next, previous: prev.data} : next)`; no effect resets state on fetcher identity change.
Suggested fix: key the resource by the fetcher (reset to loading, previous=null when the query changes) or let callers pass a `resetKey`; keep last-good only across reloads of the same query.

### [F1-06] Atlas "Verified" provenance and silent 500-row cap on the ledger table
Severity: medium
Status: CBR
Where: components/coverage/atlas.tsx (ChartFrame provenance.state: `... total === 0 ? "not_covered" : "verified"`), lib/coverage/logic.ts:gapsFirst(cap=500) used as `LedgerTable entries={drilled ?? gapsFirst(rows)}`
What: (a) The atlas footer badge is "Verified" whenever any ledger rows exist, even if every cell is unavailable/paid_only (it describes the fetch, not the data). (b) `gapsFirst` slices to 500 with no notice; the table caption/summary says "every row matching the filters is listed". Rows beyond 500 (all verified ones, sorted last) vanish silently. `drillEntries` is uncapped, so the two views disagree.
Failure scenario: 1,200 ledger rows -> table shows 500, matrix header says "1200 ledger entries ... match", no truncation message.
Suggested fix: compute the footer state from entryStateCounts (cellState over all entries) and show "Showing 500 of N rows" when truncated.

### [F1-07] Held "Reset block" is only offered for hard_blocked rows, but the confirm text/behaviour covers cooldowns the UI never offers; (reset-block safety otherwise OK)
Severity: low
Status: CBR
Where: components/sources/coverage-matrix.tsx ResetBlock; backend steward/graph/provider_gate.py reset_block
What: Safety is good (two-step confirm, path-segment regex mirrors the backend `^[a-z0-9_]{2,40}$`, POST, no body, errors surfaced, reload after success). Note the backend also clears `retry_at`/failure counters and flips state to `available` if `last_success_at` is set, so for a `paid_only` endpoint that once succeeded the row jumps to "available" immediately; the UI warning says "will most likely be denied again" which is right, but the optimistic state is "Verified" until the next job. Low: no defect, flagged for awareness.
Suggested fix: none required; optionally show the returned `state` in the "Block lifted" message.

### [F1-08] Screens: fund P/E, P/B, yield and book-value-based ratios are never checked for staleness
Severity: medium
Status: CBR
Where: apps/web/src/lib/screens/metrics.ts (staleWhen), lib/screens/parse.ts:adaptValuation, lib/screens/logic.ts:readMetric
What: Staleness is read only from `price.stale` and `eps_ttm.stale`. (a) For ETFs (`kind:"fund"`), pe/pb/dividend_yield come from `fund` (`fund.stale` = as_of older than 45 days, `fund.as_of`), `eps_ttm` is null, and `fund.stale` is ignored, so 45+ day old iShares/Vanguard figures pass criteria as current values. (b) P/B is gated on price only (staleWhen ["price"]) although it is price over book value per share; `book_value_per_share.stale` / `dividend_per_share.stale` (for yield) are not read. The screen's own help text promises "A stale ... figure never passes".
Failure scenario: ETF whose fund_valuation row is 90 days old with pe 14 -> screen "P/E <= 15" -> Matched, no stale flag.
Evidence: valuation/read.py PerShareFigure.stale, FundValuation.stale; adaptValuation reads only v.price.stale and v.eps_ttm.stale.
Suggested fix: carry `fund.stale`, `book_value_per_share.stale`, `dividend_per_share.stale` into ValuationInput and add "fund"/"bvps"/"dps" to staleWhen for pe/pb/yield accordingly.

### [F1-09] Screens: coverage-ledger reasons are looked up by ticker only, so price-symbol-keyed ledger gaps never match
Severity: medium
Status: CBR (SUSP on how often)
Where: lib/screens/parse.ts:ledgerGaps (key = entityKey upper) and lib/screens/logic.ts:readMetric (`inputs.ledger.get(key)` where key = entity ticker)
What: Ledger entity keys for securities are the Trading212 ticker OR the price symbol (data_context/holding.py uses keys [ticker, price_symbol]; entity_kind_sql matches either). The source-coverage view handles both (held set includes price_symbol); the screen builder only looks up the ticker. Entries stored under the price symbol (e.g. a Stooq-style symbol) are ignored, so a paid_only/not_covered ledger gap is reported as "missing" or generic "not_covered".
Failure scenario: coverage_status(provider=x, entity_key="aapl.us", state=paid_only) -> screen shows "Missing metric: The API returned no value and gave no reason" for AAPL_US_EQ instead of "Paid only".
Suggested fix: HeldInput needs price_symbol (Holding has none in /portfolio/holdings; /activity/coverage has it), build the ledger map for both keys.

### [F1-10] Screens: "paid_only" is inferred from any source's error text, mislabelling unrelated metrics
Severity: low
Status: CBR
Where: lib/screens/logic.ts:readMetric (`v.errors.find(e => PAID.test(e))` -> reason "paid_only")
What: `errors` is the last refresh error per source (e.g. "finnhub: 403 premium endpoint"). Any metric with no value on that ticker that is not explained by `ratio.reason` gets classified paid_only if ANY source error matches /paid|premium|upgrade|subscription|plan does not/, before the ratio's own reason is consulted (fig.reason is only the fallback detail).
Failure scenario: P/E missing due to "no EPS in the figures" plus a Finnhub premium error -> shown as "Paid only".
Suggested fix: prefer `fig.reason` when present and only fall back to the error scan when the ratio has no reason; match the error's source to `ratio.source_label`.

### [F1-11] Screens: percent threshold converted with n/100 fails exact-boundary comparisons
Severity: low
Status: CBE
Where: lib/screens/logic.ts:thresholdValue
What: `0.7/100 = 0.006999999999999999`, so an API value of exactly 0.007 fails "at most 0.7%" (and 1.1/100 = 0.011000000000000001 passes "at least"? no, fails `>=` for 0.011). 261 of 1000 one-decimal thresholds differ from the decimal fraction.
Evidence: repro dividend_yield value 0.007, criterion lte 0.7 -> `float=failed`.
Suggested fix: compare in percent space with rounding (`Math.round(v*1e6)/1e6`) or use `Number((n/100).toPrecision(12))`.

### [F1-12] Screens: failed valuation refetches are swallowed and keep the old figures as current
Severity: low
Status: CBR
Where: lib/screens/hooks.ts:useValuations (`if (next.kind==="error" && old?.kind==="ok") return prev`)
What: "Refresh data" re-requests every ticker, but a failure leaves the previous ok value with no marker, no error count, and outcomes continue to say "Matched". The page's `failedSources` banner does not cover valuation failures.
Suggested fix: store `refreshError` beside the kept value and surface "N valuations could not be refreshed".

### [F1-13] Identity scorecard reads "Target not met" when the unresolved list is merely loading/failed, and misdescribes the share
Severity: low
Status: CBE
Where: lib/coverage/logic.ts:identityScore
What: `status: ratio >= 1 && unresolvedHoldings === 0 ? "met" : "not_met"` - with `unresolvedHoldings === null` (loading or /reference/gaps failed) a 100% share becomes "Target not met" instead of unavailable. The detail also says "Counts positions with a confirmed issuer link" but backend `identity_coverage` is value-weighted (sum of position value with issuer_id over total value, portfolio.py:433); the measured text is right ("of total portfolio value"), the detail line is wrong.
Suggested fix: return "unavailable" when unresolvedHoldings is null; reword detail to "value of positions with a confirmed issuer link".

### [F1-14] Atlas summary ignores the entity-kind filter while the matrix honours it
Severity: low
Status: CBE
Where: lib/coverage/hooks.ts:coverageQuery / useCoverageSummary; components/coverage/atlas.tsx
What: `/data/coverage/summary` has no entity_kind parameter and coverageQuery does not send one, but the page copy says every filter is applied by the backend. With "Entity kind = Securities" the matrix and ledger are securities only; the summary strip still counts all kinds.
Suggested fix: note it in the UI or compute the summary client-side from the filtered rows.

### [F1-15] Source matrix: provider with no rows in the chosen scope claims "no coverage-ledger entries for this provider yet"
Severity: low
Status: CBE
Where: lib/source-coverage/logic.ts:filterGroups + components/sources/coverage-matrix.tsx (`g.rows.length === 0` message)
What: With Universe = Held/Watchlist/Other and State = all, `filterGroups` empties `rows` for providers that only have entries in other scopes but keeps the group; the table then says the provider has no ledger entries at all and its datasets are "unknown, not covered and not zero".
Suggested fix: distinguish "no rows in this scope" from "no ledger rows".

### [F1-16] Coverage entries never derive `stale` from age; ledger rows can read "Verified" with very old data
Severity: medium
Status: SUSP
Where: lib/source-coverage/parse.ts:adaptCoverage (state = stateWithCoverage(toDataState(raw), coverage); ageDays only displayed)
What: states.ts documents `stale` as a derived state "when a view derives them", but the coverage adapter derives only `partial`. A ledger row `available` whose `latest_period` is years old (or whose provider stopped) is "Verified" in the matrix and atlas; only the text "up to N d old" hints at it. There is no per-dataset refresh interval in the API, so a threshold would be a client guess.
What would confirm: confirm with the owner whether the coverage view should flag age; if yes needs a backend refresh interval per dataset.

### [F1-17] Overview data-context: panel state is the global context state, and an empty panel under "verified" renders a "Verified" placeholder
Severity: low
Status: SUSP
Where: components/overview-data/portfolio-data-context.tsx (every ChartFrame gets the same `state`/`reason`), data-state/states.ts displayDecision
What: `contextState` returns "partial" with all gap reasons for the whole section if any gap exists, so e.g. the Fund overlap panel is labelled "Partial coverage" because exposure mapping is missing. Conversely `displayDecision("verified", hasValue=false)` yields a placeholder whose badge reads "Verified" (state placeholder shows `state` verbatim) - reachable when a panel has no rows but ctx has no gap for that domain.
Suggested fix: per-panel state (not_covered when its list is empty).

### [F1-18] Minor: fund-overlap takeaway for one fund; duplicate React keys for equal labels
Severity: low
Status: CBR
Where: portfolio-data-context.tsx OverlapPanel (`${s.funds} funds with holdings; no pair of them shares an issuer.` shown for funds===1) and pair `key={`${p.a}|${p.b}`}` (labels, not ISINs).
Suggested fix: reword for funds<2; key by ISIN pair.

## Examined and found sound
- toDataState: unknown/missing strings map to "unavailable" (never verified/zero); backend AccessState values (available, not_covered, paid_only, access_unverified, quota_exhausted, temporarily_failed, disputed) and freshness values all covered. accessState correctly downgrades available+stale / never_succeeded.
- Decimal handling: toNumber/dec reject non-finite and empty; backend sends Decimals as strings (quota, request_weight, credits, coverage_ratio) and these are parsed. quota_used/remaining/headroom semantics match backend (used=0 when no ledger row; null when untracked); budgetTone/usedFraction/overAllowance math correct; headroom is a 0..1 fraction used as such.
- adaptCoverage partial derivation: ratio-based, clamped, requires expected>0; unknown entity_kind falls to "other".
- Held/watchlist classification keys: backend writes entity_key as ticker or price_symbol; held set includes both, case-insensitive; with either list failed nothing is guessed (all "other") and the UI says so.
- parseZeroCostGate: rejects non-boolean passed, non-integer counts, malformed findings, unknown severity, passed-with-violation-findings; backend to_json key names match (see F1-01/02 for the two holes).
- reset-block: POST, confirm step, endpoint/provider regex equals backend ProviderPath, errors surfaced; button only for hard_blocked.
- Decision trail: outcome/rule vocab matches the backend Literal enums; paging contract (before_id, next_before_id, newest-first) matches read_trail; appendPage dedupes by id; evidence links only http(s); ticker validated against the backend pattern. (Race and error branch: F1-04.)
- Screens: missing/unknown never passes (judge returns unknown, evaluateEntity excludes or lists unknown), definite fail outranks unknown, pending handled, held-only metrics unknown for watchlist-only entities; units: valuation dividend_yield/margins/ROE/ROIC, holdings weight/ppl_pct and day_change_pct are all backend fractions, and the pct<->fraction conversion is applied once (only the boundary float issue F1-11). Alpha Vantage fills (ProfitMargin, ReturnOnEquityTTM, DividendYield) are also fractions. URL state round-trips and drops unknown metrics/ops; value is length-capped and cleaned; MAX_CRITERIA enforced.
- 4-at-a-time valuation loader: run counter cancels superseded runs, results are keyed by upper-cased ticker, no cross-run write after cleanup.
- Scorecard: price/provenance/global reach are labelled not_measured/reported rather than invented; share meters are clamped; coverage sums in backend summary (available over entries with expected) match the "over N of M entries with an expected count" wording.
- Overview data context: Share ratios come with their denominator, null ratio shown as "n/a", units (fractions) match backend; sort and unmapped handling match backend concentration().
- tsc --noEmit clean.

## Not examined
- Backend seeds of provider_endpoint_manifest/provider_access (cannot confirm F1-01's empty-manifest precondition occurs in practice), real endpoint names beyond the regex check.
- components/coverage/state-glyph.tsx visuals, a11y of glyph patterns, other activity panels (job-log, alerts, trust, eval, coverage-panel, overview-panels) which predate this branch.
- fund overlap weight units beyond the `overlap`/`unknown_fund_weight` field descriptions; lib/macro/api.ts (fetchPortfolioMacroCoverage).
- Runtime behaviour in a browser (no dev server started).
