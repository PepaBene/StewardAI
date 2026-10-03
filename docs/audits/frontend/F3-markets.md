# Frontend audit F3: markets / price coverage (apps/web)

Scope: lib/markets, components/markets, app/(app)/markets, lib/charts/candles.ts, components/charts/candle-chart.tsx (+ heat.tsx as used by the correlation panel). Compared against backend: api/free_sources_routes.py, api/reference_routes.py, steward/reference/{read,returns,price_diagnostics,calendar}.py, steward/free_sources.py, steward/portfolio/quotes.py.

### [F3-1] Portfolio coverage divides a fraction by 100: day change shown 100x too small
Severity: high
Status: CBE
Where: apps/web/src/components/markets/portfolio-coverage.tsx:87
What: `HoldingPrices.day_change_pct` is a FRACTION on the backend (`CHANGE_HELP = "Fraction (0.012 = +1.2 %)"`, `_change` returns `latest/previous - 1`, steward/portfolio/quotes.py:42-48,93). The table does `formatPct(h.day_change_pct / 100, 2, true)`, and formatPct itself multiplies by 100. (The sibling holdings-view uses the raw value; `difference_pct` in the plausibility check IS a percent, so that /100 is right, this one is not.)
Failure scenario: backend returns day_change_pct = 0.012 (+1.2%) -> table prints "+0.01%". A -8% day prints "-0.08%".
Evidence: throwaway vitest: `formatPct(0.012 / 100, 2, true)` -> "+0.01%".
Suggested fix: `formatPct(h.day_change_pct, 2, true)`; fix the test fixture if it uses percent units.

### [F3-2] Event-study gate depends on the selected Returns window, not on the span the study draws
Severity: medium
Status: CBR
Where: apps/web/src/app/(app)/markets/markets-view.tsx:116,150-154; lib/markets/logic.ts:203-214
What: `eventStudyGate` reads `missing_actions.price_adjusted` and `action_coverage` from the Returns response for the user-chosen window (30/91/182/365 days). Backend `missing_actions` flags (`load_confirmed_actions`, free_sources.py:563-567) only cover confirmed merger/spin-off/symbol-change/delisting inside [start,end]. The event study, however, draws every confirmed split/dividend in ~400 days of bars. Choosing "1M" in the Returns control therefore opens the gate (an unvalued merger 8 months ago is no longer flagged) and choosing "1Y" closes it. The gate is also changed by an unrelated control, and a suggested (unconfirmed) merger/spin-off never blocks it.
Failure scenario: holding with a confirmed spin-off 6 months ago: 1Y window -> "The backend flags the adjusted series", study withheld; click 1M -> study drawn across a price step the adjusted series does not reconcile.
Evidence: code lines above; `useHoldingReturns(ticker, addDays(today,-Number(windowId)), today)` feeds `returnsParsed` into the gate.
Suggested fix: fetch a dedicated returns window for the gate that spans the bars used (e.g. first bar -> end), independent of the segmented control; also block on any non-rejected unhandled-kind action in the span.

### [F3-3] Returns window ends at the viewer's local "today": lies about coverage and verified-through, and the as-of is the request date
Severity: medium
Status: CBR
Where: apps/web/src/lib/markets/logic.ts:31-35 (todayIso), markets-view.tsx:115-116, parse.ts:134 (known_as_of), returns-panel.tsx:77-86
What: three coupled problems.
1. `end = todayIso()` uses local getFullYear/Month/Date, backend dates are UTC/exchange dates. Verified: `todayIso(new Date("2026-10-03T23:30:00Z"))` under TZ=Pacific/Auckland -> "2026-10-04". For viewers ahead of UTC the window ends on a date that does not exist yet for the backend.
2. Backend `expected_sessions` includes `end` (today) if it is a weekday. Until the daily job stores today's bar, coverage = (N-1)/N < 1, so the Returns panel shows state "partial" ("N-1 of N expected sessions") on every trading day, and `actions_complete` demands the owner verified coverage `>= end` (today), so `action_coverage` is "incomplete" and total return withheld, again every day unless the owner re-verifies daily. The event-study gate (F3-2) inherits this: it is almost always closed.
3. `known_as_of` in the backend response is just the request `end`; ProvenanceFooter shows it as "as of", so a stale series (last bar a week old) is stamped "as of today".
Failure scenario: Prague owner opens the page 09:00 on a Tuesday: Returns shows 251 of 252 sessions, state partial, total return unavailable "corporate-action coverage is incomplete"; footer says as of 2026-10-06.
Suggested fix: end = last stored bar date (`prices.as_of`) or last expected session <= UTC today; use `prices.as_of` for the footer's as-of; compute today from UTC (`toISOString().slice(0,10)`) for any server-facing date.

### [F3-4] Correlation heatmap legend labelled in percent: "-100% ... +100%"
Severity: low
Status: CBE
Where: apps/web/src/components/charts/heat.tsx (Heatmap renders `<HeatScale cap={cap} className="self-end" />` without a formatter), used by components/markets/correlation-panel.tsx:86
What: HeatScale's default format is `formatPct(v,0,true)`; Heatmap does not pass `formatCell` through, so a correlation matrix (cap 1) shows a scale key "-100% .. +100%" while cells show 0.62.
Evidence: rendered markup contains `−100%` and `+100%`.
Suggested fix: pass a `formatScale` prop (or reuse formatCell) from Heatmap to HeatScale.

### [F3-5] Unexplained-jump warning ignores shifted ex-dates; marker and warning disagree
Severity: low
Status: CBE
Where: apps/web/src/lib/markets/logic.ts:141 vs 121-130
What: `placeActions` maps an ex-date that is not a bar to the next session (`shifted`), but `unexplainedJumps` only treats a jump as explained when `bar.date === exDate`. A split with a weekend/holiday ex-date, or a provider that dates the step one session off, draws its marker on the jump bar yet the panel still says "1 large move with no recorded corporate action".
Evidence: bars 2026-03-06 (100) / 2026-03-09 (40), confirmed split exDate 2026-03-08: marker index 1, `unexplainedJumps` -> [{date:"2026-03-09", change:-0.6}].
Suggested fix: build the explained set from `placeActions` results (bars[index].date) or allow +-1 session.

### [F3-6] Raw price return has no reason; "No reason given by the backend" shown for the expected case
Severity: low
Status: CBR
Where: parse.ts:136 (`figure(r.raw_price_return, null)`), returns-panel.tsx:18
What: the route does not return `price_raw_reason`, so a null raw return (no raw bars stored, the usual case since only adjusted bars are served) shows "No reason given by the backend." The reason exists in `missing_actions.price_raw` but is only listed in the merged warning box without basis attribution (the three lists are merged by `new Set`, so a reason that belongs to total return and one that belongs to raw return are indistinguishable).
Suggested fix: use `missing_actions.price_raw[0]` as the raw row reason; render the three lists per row.

### [F3-7] Decision flow: returns / event-study not refreshed after a decision; stale buttons; 422 detail rendered as "[object Object]"
Severity: low
Status: CBR
Where: markets-view.tsx:167 (`onChanged={actions.reload}`), action-ledger.tsx:67-70
What: (a) after Confirm/Reject only the actions resource reloads; Returns (total return, missing_actions, action_coverage inputs) keeps the old figures until the window is toggled, while the event study (which reads the new action list) updates, so two panels disagree. `reload` keeps the old `ok` state while refetching, so the row still shows "Suggested" with live Confirm/Reject buttons for a moment (a second confirm just rewrites decided_at). (b) FastAPI validation errors have `detail` as an array; `String(apiError.detail)` gives "[object Object]". (c) an invalid evidence id (FK violation in `source_evidence`) is a 500 from the backend and shows "HTTP 500"; the client accepts "1e3" / "0x10" as evidence ids (`Number.isInteger(Number("1e3"))`).
Suggested fix: reload returns too (or lift a reload key); format `detail` arrays (`detail.map(d => d.msg)`); validate evidence id with /^\d+$/.

### [F3-8] Per-holding correlation fan-out is unbounded
Severity: low
Status: SUSP
Where: lib/markets/use-markets.ts (useCorrelationRows)
What: N parallel `GET /holdings/{ticker}/prices?days=365`, each of which on the backend loads every other holding's series and computes N-1 correlations (read.py `price_history`), i.e. N^2 series loads in one burst, re-fired after every successful sync (`lastSuccessJobId`). Confirm by timing with a 30-holding portfolio / checking DB pool saturation. One 5xx merely shows the holding as `failed`, so it degrades gracefully.
Suggested fix: cap concurrency (e.g. 4) or add a backend matrix endpoint.

### [F3-9] Misc low items
Severity: low
Status: CBR
- portfolio-coverage.tsx:84: "Last close" column prints bare numbers across holdings quoted in GBX, USD, EUR (no quote unit; 1,240.50 GBX next to 55.10 USD).
- price-diagnostics.tsx:242,257: column titled "Bars" contains the bar type ("adjusted"); the bar count is not shown.
- session-strip: trailing sessions after the last stored bar (job lag) are labelled `missing_bar` ("genuine gap") while the backend's diagnostics `missing_days` only counts first..last bar and exposes the trailing case as `stale_sessions`; the two panels can disagree (strip "3 missing", diagnostics 100%).
- session-strip only renders Mon-Fri; a confirmed weekend session (backend `exchange_open` accepts a confirmed `open` row on a weekend) is in `expected.days` but never drawn or counted.
- markets-view.tsx:70-100: if the selected ticker disappears (position sold), `current` falls back to list[0] but the `<select>` keeps the stale value.
- MIC from an unconfirmed/rejected listing (`listing.status`) is used for the venue calendar when diagnostics have none (markets-view.tsx:124).
- Marker lane: two actions on the same bar overlap exactly; markers at index 0 clip at x<0; heatmap row-header column is `w-12`, so T212 tickers ("ASML_NL_EQ") are cut.

## Examined and found sound
- GBX/GBp/ZAc/ILa table in logic.ts matches backend `MINOR_QUOTE_UNITS` (0.01 divisor); `quoteOf` prefers `quote_unit` (canonical GBX) over `currency`; `describeQuote` prints both the stored and the major value. Plausibility `difference_pct` correctly divided by 100 (backend returns percent).
- Adjusted-vs-raw labelling: backend `load_bars`/`load_series` serve only `bar_type='adjusted'`; the UI states this, disables Raw with a reason, never splices bases. Caveat: backend `store_bars` defaults every bar to "adjusted" and Yahoo is split- but not dividend-adjusted (yahoo.py:10), so "adjusted" does not tell the owner which kind; the UI wording ("provider-adjusted closes as stored") is hedged adequately.
- Marker placement: price-chart-panel marker lane uses the same `candleBand(bars.length, 880)` (range [0, 880-52], same paddingInner) and same bars slice as CandleChart; `band.x(i)+bandwidth/2` equals the candle `cx(i)`. Rejected actions are excluded; ex-dates outside the window are omitted rather than clamped.
- Event study mechanics (indexing to the bar before the event, windows running off either end omitted, mean only over complete windows); returns parse (action_coverage enum, missing_actions keys, `results: []` + `reason`) matches the route; coverage/expected/observed are the backend's numbers, none recomputed.
- Correlation assembly: `assembleCorrelation` keys by the `Correlation.ticker` (T212 tickers, same domain as /portfolio/holdings), fills mirrored value only when the row's own is null and counts asymmetry; `clusterOrder` reorders `tickers` and `matrix` with the same index permutation in the panel and the pair table (`matrix[i][i+1+k]` is the pair of tickers[i], tickers[i+1+k]); a missing value counts as distance 1, never 0 and renders as an empty cell. Holding without series gets no row (listed in `withoutSeries`).
- Fetch races: useApiResource keeps a sequence counter (stale responses dropped); HoldingSection is keyed by ticker so all per-holding hooks remount on selector change; correlation `key` memo keeps the fetcher stable (no refetch loop).
- Listing 404: `describeError` carries `status`, `parsed()` forwards it, the view maps 404 to "no listing derived yet" instead of an error. Listing parser matches `listing_overview` keys.
- Decision form: two steps (pick, then reason + Save), reason trimmed and >=10 (backend `min_length=10`), evidence id required only when confirming without one, `evidence_id` omitted when already linked, submit button disabled while busy (implicit Enter submission is blocked by the disabled default button), method/path/body match `CorporateActionDecision`.
- Price-history job trigger: arm-then-confirm, POST /jobs/price-history exists (202), busy disables the buttons.
- Date handling: all display goes through `parseDay`/`formatDay` (string slicing), arithmetic uses UTC midnight; no viewer-timezone shift in formatting or in the 90-back/28-ahead window arithmetic (only the "today" anchor, see F3-3).
- Session classification logic: held-vs-expected denominator comes from backend `expected.days` (confirmed rows first, else weekdays less closures); national holiday never excuses a missing bar; today without a bar is `upcoming`; before-first-bar days excluded.

## Not examined
- Live-backend behaviour (no backend run); real payload shapes for `raw` JSON on corporate actions beyond the code path in `decode_structured_rows`.
- Intraday/other tabs of the holding page that reuse these components; ChartFrame/ProvenanceFooter internals; theming and responsive layout beyond a code read.
- Backend `_same_currency`/T212 unit handling for the plausibility check (backend scope).
- Existing unit tests (markets.test.ts, logic.test.ts) were not run or reviewed for fixture units.
