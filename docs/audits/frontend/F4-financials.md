# F4 audit: financials (lib/financials, components/financials, app/(app)/financials)

Throwaway repro tests were written and deleted (git status clean). Paths are relative to apps/web/src.

### [F4-01] Surprise % sign inverted for negative consensus (loss-making EPS)
Severity: high
Status: CBE
Where: lib/financials/logic.ts:768 (and components/financials/earnings-card.tsx method text)
What: `surprise()` recomputes `actual / c.value - 1` and ignores the backend's `surprise_percent`, which is `(actual - expected) / abs(expected)` (steward/fundamentals/ir.py, end of `actual_vs_consensus`). With a negative consensus the sign flips.
Failure scenario: EPS actual -0.40 vs consensus -0.50 (a beat, loss narrower): card shows "-20.0%" next to a "Within the analyst range" bar. A miss shows a positive %.
Evidence: repro: `surprise({actual:{value:-0.4}, consensus:{value:-0.5,low:-0.6,high:-0.4}}).pct` -> -0.19999999999999996.
Suggested fix: use `(actual - c.value) / Math.abs(c.value)`, or display `avc.surprisePct` from the API.

### [F4-02] Estimates fan mixes annual and quarterly actuals on one line / axis
Severity: high
Status: CBE
Where: lib/financials/logic.ts:651-669 (`actualsFor`), 737-738 (`lastActualPeriods`/`periods`); estimates-fan.tsx actual path
What: `actualsFor` keeps every fiscal period whose class is annual or quarter, keyed by `fiscal_period` ("FY2025", "FY2025Q1"...). `buildEstimates` takes `actuals.slice(-4)` of the alphabetically sorted list, so the axis becomes FY2025, FY2025Q1, Q2, Q3 and the solid "actual" line connects a 12-month value to 3-month values. Forward periods are also a mix, and the string sort puts "FY2026H1" before "FY2026Q1".
Failure scenario: revenue FY2025=400, Q1=95, Q2=100, Q3=105 -> line jumps 400 -> 95 -> 100 -> 105 (a spurious collapse). The 100x scale guard (line 731) cannot catch a 4x gap.
Evidence: repro `buildEstimates(...).actuals` -> ['FY2025=400','FY2025Q1=95','FY2025Q2=100','FY2025Q3=105'], periods the same.
Suggested fix: restrict actuals and the x-axis to the period granularity of the plotted consensus series (annual `^FY\d{4}$` vs quarterly `Q`), one granularity per chart; order periods chronologically (period_end), not by string.

### [F4-03] Cash walk / actuals pick facts across concepts of one line item, arbitrarily
Severity: medium
Status: CBE
Where: lib/financials/logic.ts:250-252 (`pick`), 271 (cfo), 654-665 (`actualsFor`), 399 (debt tranches)
What: backend maps several concepts to one `line_item` (net_income = NetIncomeLoss, ProfitLoss, NetIncomeLossAvailableToCommonStockholdersBasic, IFRS ProfitLoss...; capex = PaymentsToAcquirePropertyPlantAndEquipment AND PaymentsToAcquireProductiveAssets; D&A = DepreciationDepletionAndAmortization AND DepreciationAndAmortization; sources/xbrl_concepts.py). `pick()` filters by `lineItem` only and takes `newest()`; rows from one filing share `known_at` and `revision`, so the winner is the highest `id` (insertion order). `buildStatement` instead pins a single concept per line, so the explorer and the walk can show different net income for the same year.
Failure scenario: company with a noncontrolling interest (ProfitLoss 90, NetIncomeLoss 80), same filing: walk net income is 80 or 90 depending on row insertion order; the "gap" and the 0.5% reconcile test then move with it. Capex can be PP&E purchases or the larger "productive assets" figure.
Evidence: repro: order [ProfitLoss, NetIncomeLoss] -> netIncome 80; order [NetIncomeLoss, ProfitLoss] -> 90.
Suggested fix: define a preferred-concept order per walk input (NetIncomeLoss over ProfitLoss; PP&E payments over productive assets) and select deterministically; never pick across concepts.

### [F4-04] Statement line silently drops periods filed under a different tag; shown as "not filed"
Severity: medium
Status: CBE
Where: lib/financials/logic.ts:198-200 (`buildStatement` concept choice), table cell "not filed" in statement-explorer.tsx:92
What: each line item keeps only the one concept with most periods; periods filed under the other mapped concept are discarded, then rendered as empty slots / "not filed".
Failure scenario: revenue tagged SalesRevenueNet FY2021-23 and Revenues FY2019-20 + FY2024-26 (a normal ASC 606 tag switch). Result: FY2021, FY2022, FY2023 render "not filed" although revenue was filed.
Evidence: repro output FY2019:9, FY2020:9, FY2021:NOT FILED, FY2022:NOT FILED, FY2023:NOT FILED, FY2024:20...
Suggested fix: per period take the best-ranked concept (a fixed preference order), mark concept switches, and say "filed under a different tag" instead of "not filed".

### [F4-05] Quarterly statement view omits Q4 balance sheet and shows only Q1 cash flow
Severity: medium
Status: CBE
Where: lib/financials/logic.ts:59-65, 148-153, 165-168
What: period ends of a basis come only from flow facts of 80-100 days; instants join only if a quarter flow ends the same day, and 10-Q cash-flow facts are year-to-date (6/9 months -> class "other"). Q4 has no 3-month flow in a 10-K, so the FYE balance sheet is dropped; cash flow shows Q1 only. No message says why.
Failure scenario: Quarterly + Balance sheet for a Dec year-end: columns FY2025Q1..Q3 with no Q4/FYE balance (latest balance is nine months old, labelled as the latest period). Quarterly + Cash flow: only Q1 of each year.
Evidence: repro `buildStatement(..., "balance_sheet","quarter")` periods -> FY2025Q1, Q2, Q3 (FY2025 year-end cash fact present but dropped).
Suggested fix: let instants define their own quarter ends (also treat FYE as Q4), and for cash flow derive discrete quarters (YTD minus prior YTD) or label YTD explicitly.

### [F4-06] Stale data shown under the new as-of / reconciled-only setting while refetching (and after a failed refetch)
Severity: medium
Status: CBR
Where: lib/hooks/use-api-resource.ts:50-69 used by lib/financials/hooks.ts (all hooks); app/(app)/financials/financials-view.tsx:63 (only `ticker`+`refresh` key the sections)
What: changing `asOf` or `reconciledOnly` changes the fetcher but the resource state is not reset to loading; the previous payload stays "ok" and is rendered with the new label ("as known on <date>", "Reconciled facts only" checked). If the new request fails, `{...next, previous: prev.data}` keeps the old figures under the error. Same for the segment basis switch.
Failure scenario: tick "Reconciled facts only": for the latency of three requests, and indefinitely on a 500, the explorer, walk and ladder still show unreconciled facts. Pick a past date: restated figures filed later remain drawn with "as known on 2023-03-01".
Evidence: code (`fetchAndApply` never sets loading; `settle` error branch merges `prev.data`); the parent only remounts on ticker/refresh.
Suggested fix: reset to loading when the fetcher identity changes (or key the sections by `ticker:asOf:reconciledOnly`), and do not reuse `previous` across different query params.

### [F4-07] ProbePanel not keyed by ticker: pending confirm and results carry over to another holding
Severity: medium
Status: CBR
Where: app/(app)/financials/financials-view.tsx:70 (`ProbeSection` is outside the keyed `<div key={ticker:refresh}>`); components/financials/probe-panel.tsx:36-52
What: `pending` and `results` are component state; switching the holding in the selector keeps them. "Spend one free request? Confirm" armed for ticker A then fires against ticker B; the "Done. ..." result of A is displayed under B's heading. The confirm gate is meant to bind to the displayed ticker.
Failure scenario: click Run on Alpha Vantage balance sheet for AAPL, change selector to MSFT, click Confirm: one of the ~25 daily free calls is spent for MSFT with no prompt naming it. Also a probe still in flight when the ticker changes writes its result under the new ticker.
Suggested fix: `key={ticker}` on `ProbePanel` (or clear `pending`/`results` on ticker change) and name the ticker in the confirm text.

### [F4-08] Peer comparison counts duplicate peer rows; "partial" with an empty exclusion list
Severity: medium
Status: CBE (frontend) / backend premise by schema
Where: lib/financials/logic.ts:829-830 (`total = p.confirmedPeers.length`); parse.ts:360-363
What: `peer_set` is unique on (ticker, peer_ticker, basis, source) (migration 20261001103217), and `/peer-metrics` returns one `confirmed_peers` row per such row, while the metrics use the distinct tickers. A peer suggested by Finnhub and re-added by the owner appears twice.
Failure scenario: peers AAA (finnhub), AAA (owner), BBB, both comparable -> table says "2 of 3 confirmed peers comparable", state "partial: 1 of 3 excluded" and the "Excluded peers" list is empty (excluded is computed from tickers).
Evidence: repro `peerComparison` -> covered 2, total 3, excluded [].
Suggested fix: dedupe `confirmedPeers` by `peerTicker` in `parsePeerMetrics` (and show per-ticker status in the peer list).

### [F4-09] Segment profit matched by member local name only: axis ignored, collisions across axes
Severity: medium
Status: CBR
Where: lib/financials/logic.ts:483-502 (`localName`, `profitFor`)
What: the join key is the lower-cased local name; `members[0].axis` is never compared to the breakdown's axis, and prefix is dropped. Segment `operating_income` facts on any single-member axis (ProductOrServiceAxis, geography, a legacy axis) are candidates. A fact on a two-member context (ConsolidationItemsAxis + StatementBusinessSegmentsAxis, common after ASU 2023-07) is dropped by `members.length === 1`, leaving "No filed segment profit". The profit unit/currency is not checked against the revenue currency either.
Failure scenario: segment revenue "ServicesMember" (business-segment axis) gets the operating income of "ServicesMember" on the product axis; the margin colour is drawn as comparable.
Evidence: repro with two profit facts: Services -> 5 (from ProductOrServiceAxis), Devices -> 30 (business-segment axis); both attached.
Suggested fix: require the fact's axis to be the business-segment axis (and prefer the member with the full prefixed name), accept multi-member contexts that include it, check unit vs `data.currency`. Confirm against real filings that the segment `/segments` member names include axis info.

### [F4-10] Revision slope breaks on non-date `vintage` (backend allows free text up to 80 chars)
Severity: medium
Status: CBE (needs non-date vintages in data; `ForwardObservationInput.vintage: str`)
Where: lib/financials/logic.ts:602-603, 622-633; estimates-fan.tsx revision list; provenance asOf
What: `vintageDate = (vintage ?? knownAt).slice(0,10)`; `days()` uses `Date.parse`. A vintage like "Q1 2026 preview" gives NaN.
Failure scenario: row shows "NaN days", `perMonthPct` NaN, and `formatDate("Q1 2026 pr")` renders garbage/Invalid Date. Snapshot dedupe by "date" also merges unrelated snapshots sharing a 10-char prefix.
Evidence: repro `revisionSlope(...)` -> days NaN, perMonthPct NaN, from.vintage "Q1 2026 pr".
Suggested fix: use `known_at` for ordering and span; show `vintage` only as a label; validate `^\d{4}-\d{2}-\d{2}` before treating it as a date.

### [F4-11] Peer bars use |value|: negative metrics drawn as positive bars
Severity: low
Status: CBR
Where: components/financials/peer-table.tsx:33, 79
What: width = `abs(value)/max`. Negative operating margin, ROIC, FCF conversion or net debt/equity (net cash) is drawn the same length as a positive one; only the printed number carries the sign.
Failure scenario: holding -20% operating margin vs peers +20%: identical bars.
Suggested fix: bars around a zero baseline (negative to the left, distinct fill).

### [F4-12] Actual-vs-consensus ignores the page "as known on" date and its reconciliation state is collapsed
Severity: low
Status: CBR
Where: lib/financials/hooks.ts:82-89; sections.tsx:121-140; logic.ts:777
What: the AVC endpoint has no `as_of`; the periods dropdown comes from as-of-limited facts but the card shows the latest actual (possibly restated after the chosen date) and the latest consensus, under a page that says everything is point-in-time (the notice only lists segments and valuation). Backend `reconciled` is `coalesce(...,false)`, so a *failed* check shows "not covered by a reconciliation check" (partial) instead of disputed. No scale guard on AVC (the fan has the 100x guard): consensus in millions vs actual in units yields a ~1e6 "surprise".
Suggested fix: state that AVC is not as-of limited when `asOf` is set; surface the failed vs unchecked distinction from the backend; add the same magnitude guard.

### [F4-13] Segment profit facts honour `as_of` while segment revenue does not
Severity: low
Status: CBR
Where: sections.tsx:68-70 (`useSegmentProfitFacts(ticker, asOf)`) vs `useHoldingSegments(ticker, basis)`
What: with a past as-of date, revenue is the latest filing but profit facts are cut off, so the period.end of revenue has no profit and margin colour is disabled with "No filed segment profit", contradicting the notice that the date does not apply to segments.
Suggested fix: fetch profit facts without `as_of` (or disable both consistently).

### [F4-14] `asOfParam` end-of-day semantics
Severity: low
Status: CBR
Where: lib/financials/hooks.ts:18; financials-view.tsx:30 (`today` from `toISOString`)
What: `${date}T23:59:59Z` excludes facts known during the last second (23:59:59.001-.999) of the chosen UTC day; `max` uses the UTC date, so a viewer ahead of UTC cannot pick their local today. Peer window uses `$2::date`, consistent.
Suggested fix: send the next day's 00:00:00Z exclusive boundary (backend uses `<=`, so `T23:59:59.999999Z`), and document UTC.

### [F4-15] IR release links are rendered as hrefs without a scheme check
Severity: low
Status: SUSP
Where: components/financials/ir-panel.tsx:18,75
What: `POST /issuer-ir-releases` accepts `source_url` with only length checks (sources are https-only). A `javascript:` URL would be a clickable link. Owner-only data, so self-XSS at most. Confirm by posting such a release and clicking it. Same for forward-observation `source_url` in the provenance footer (not checked).
Suggested fix: render as link only for http(s).

## Examined and found sound
- Fact parsing (`parse.ts`): key names (`line_item`, `period_end`, `known_at`, `amends_*`, `dimensions.members` as [axis, member] pairs, `validation`, `reconciliation`) match `financial_history_rows` and `statements.py`; Decimal strings parsed by `num`; missing values are null, never 0; outer-shape failure returns null -> "Unexpected response shape".
- Sign handling: capex `Math.abs`, FCF = CFO - |capex|; XBRL payments are positive. Buybacks/debt-repaid are not consumed by this area.
- Cash walk is only drawn when NI + filed D&A/SBC is within 0.5% of CFO, otherwise a table; D&A and SBC are separate line items and are not double counted (one pick each). Gap is not shown as a bar.
- Debt ladder: same-unit filtering, total includes current portion (`LongTermDebt`), 2% tolerance symmetric, over-classified flagged "disputed", unclassified never spread over years; lease facts kept apart.
- Amendment lineage: versions grouped per accession, original = oldest `known_at` then revision, latest = newest; ties handled; restatement listing compares values (not just flags).
- Period class windows (330-380 / 80-100 days) cover 52/53-week years and 13-week quarters; fiscal labels come from the backend `fiscal_period` and fall back to the date.
- Estimates: one consensus series (provider|currency|basis) drawn, other series/currency listed as unplotted, revision slope uses one series only; GBX vs GBP are not converted (shown as mismatch); currency mismatch of actuals suppressed.
- Peer metrics parse: `covered_peer_metrics`, `included/excluded_reasons`, formulas match `peer_comparison`; unit "percent" values are fractions as formatted.
- Peer-set PUT: method/path/body match `PeerStatusInput`; reason < 3 chars disables buttons; evidence id validated as an integer; API errors shown; confirming without evidence is refused by the API and shown.
- Probes: nothing runs on load; Run -> Confirm/Cancel; one POST per confirmation; busy disables others; 409/422/429 shown verbatim (but see F4-07).
- Fetch races: `useApiResource` sequence guard drops out-of-order responses; ticker change remounts the data sections (key). Three-statement fetch fails whole if any one fails.

## Not examined
- `components/reference/segments-panel` / valuation panel internals, `components/charts/Treemap`, `ChartFrame`/`ProvenanceFooter` (href rendering), `lib/charts/format` rounding.
- Live payload shapes (Decimal serialisation as string vs number was assumed handled by `num`), real EDGAR tagging frequency for F4-03/04/09.
- Finnhub/Alpha Vantage backend behaviour beyond route signatures; `forward_payload` vintage ordering with identical `known_at`.
- a11y beyond obvious label/role checks; no browser run.
