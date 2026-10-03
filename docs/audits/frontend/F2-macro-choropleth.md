# F2 audit: macro views, geo/choropleth, exposure editor (apps/web)

Counts: high 1, medium 4, low 5. Lint, tsc and the existing 144 tests in these areas pass.

### [F2-1] Holding switch shows the previous holding's macro data under the new ticker, and prefills the previous issuer id into the write form
Severity: high
Status: CBR
Where: apps/web/src/lib/hooks/use-api-resource.ts:62-74 (no reset when `fetcher` changes); apps/web/src/app/(app)/macro/macro-view.tsx:48-66,150-165
What: `useApiResource` keeps `state.kind === "ok"` with the old `data` while the new fetch is in flight (only `reload()` resets, and only if not ok). `macro-view` swaps `ticker`/`asOfIso` (new fetcher) and passes the OLD `ctx` to `<HoldingMacroPanel ctx ticker={ticker}>`, which prints `Mapped exposure of {ticker}` and `{ticker} - as of ...` from the NEW prop. `targets`, `issuers` and `defaultIssuerId` are derived from the same stale `contextData`, so `ContextCards` and the `ExposureEditor` form also follow the old holding. If the new ticker's request fails, `ResourceBoundary` renders `state.previous` (the old holding's data) next to the alert.
Failure scenario: pick ASML then change the select to SAP on a slow or failing backend: for the duration of the request (or permanently on error) the screen says "Mapped exposure of SAP" with ASML's countries, weights and indicators. The editor's `key` only changes once the new data arrives; until then, "Add mapping" with the prefilled `issuerId` writes the mapping to ASML's issuer while the page says SAP. Same hook issue in TradePanel: after submitting a new reporter/HS, `ProductRows hsCode={query.hsCode}` and `PartnerSankey selectedHs` label the previous query's rows with the new HS code.
Evidence: `fetchAndApply` only calls `setState(prev => next.kind==="error" && prev.kind==="ok" ? {...next, previous: prev.data} : next)`; nothing sets `loading` when `fetcher` identity changes. macro-view line 155: `{(ctx) => <HoldingMacroPanel ctx={ctx} ticker={ticker} />}`.
Suggested fix: key the section on the request (`<ResourceBoundary key={ticker+asOfIso}>` plus a fetcher-identity reset in the hook: `useEffect(() => setState({kind:"loading"}), [fetcher])`), or have the parsed context carry its ticker and render only when `ctx.ticker === ticker`. Disable the editor's submit until the context matches the selected ticker.

### [F2-2] Countries with a value but no polygon in the 110m topology appear twice in the ranked list
Severity: medium
Status: CBR
Where: apps/web/src/components/charts/choropleth-map.tsx:67-72 and 190-206
What: `ranked` is built from ALL `data` rows with a finite value; `offMap` is the subset whose code has no shape. The list renders `ranked` and then `offMap`, so every off-map country with a value is listed twice (once as a ranked row with a bar, once as "not drawn on the map"). Off-map countries with `value: null` appear only in `offMap`. 110m has no polygon for SG, HK, MT, BH, MU, LU-sized microstates and many islands (177 features; the nine unmatched ones are W. Sahara, Fr. S. Antarctic Lands, N. Cyprus, Somaliland, Kosovo).
Failure scenario: an owner-assigned 4% Singapore mapping shows "Singapore 4.0%" in the ranking and again "Singapore / not drawn on the map / 4.0%". Anyone adding the column up counts it twice; `top` for bar scaling is unaffected, but the visual total is inflated.
Evidence: `const ranked = [...data].filter(finite)` (no `drawn.has` test) and `const offMap = data.filter(d => !drawn.has(d.code))`.
Suggested fix: `ranked = data.filter(finite && drawn.has(code))`, or drop the value from the offMap rows and keep them only for the "not drawn" marker; keep the bar for both.

### [F2-3] Trade query is sent to /macro/trade with the reporter exactly as typed; the backend matches case-sensitively and accepts only alpha-2 or M49
Severity: medium
Status: CBR
Where: apps/web/src/components/macro/trade-panel.tsx:176-181,191-195; apps/backend/src/api/macro_routes.py:280-291
What: the form accepts any 2-5 letters/digits ("de", "DEU", "Germany" is rejected but "deu" passes) and `tradeFetcher` passes `query.reporter` unchanged to `/macro/trade/{reporter}/{hs}`, whose SQL is `reporter=$1 or reporter_iso=$1` (exact, case-sensitive; no alpha-3, no alias expansion such as 840 -> 842). The country-context call right below uppercases (`query.reporter.toUpperCase()`) and the backend's `reporter_codes` expands aliases and alpha-3, so the same query succeeds in one panel and 404s in the other.
Failure scenario: owner types "de" or "DEU" or "840" (ISO number of the US; Comtrade stores 842): trade summary says "No trade observations for this reporter and product" while the Sankey below shows Germany/US rows for the same input. Reads as "no data" when data exists.
Evidence: `fetchTradeContext(query.reporter, query.hsCode)` vs `fetchCountryContext(query.reporter.toUpperCase())`; backend `where (reporter=$1 or reporter_iso=$1)`.
Suggested fix: normalise once in the form with `resolveGeography` (send alpha-2/"EU"/the M49 the backend stores, uppercase) and tell the user when the code is not recognised; ideally make the backend route use `reporter_codes`.

### [F2-4] Trade summary labels reporter-wide numbers as the selected HS product's
Severity: medium
Status: CBR
Where: apps/web/src/components/macro/trade-panel.tsx:200-228 and lib/macro/logic.ts TRADE_METRIC_LABELS; apps/backend/src/api/macro_routes.py:293-327
What: the route computes `periods`, `hs_revision` (becomes "mixed" if the reporter has several), `source_url`, `known_at` and ALL `metrics` (growth, partner HHI, export share, product-mix change) from `points` = every stored product of the reporter; only `latest_value_by_flow` is product-specific (`product`). The panel prints "{reporter}, HS {code} ({revision}): {periods} periods stored; latest {date}" and tiles "Export growth", "Export partner concentration" with notes that never say "all products stored for this reporter". Growth uses `flow_totals`, which sums World rows over whichever products happen to be stored, so it moves when another product is collected.
Failure scenario: for HS 8486 with only 2 periods stored, the line can read "12 periods stored" and "Export growth +18.0%" is the growth of the sum of all stored products, presented under the HS 8486 heading with the 8486 export total above it. `hsRevision` "mixed" is printed in parentheses as if it were a revision.
Suggested fix: label the metric block "Reporter-wide, all stored products" and the period count likewise; or have the backend scope metrics/periods to `product`.

### [F2-5] Owner must type a raw issuer id with no check against the ticker; a holding with no mapping yet has no way to discover its id
Severity: medium
Status: CBR
Where: apps/web/src/components/macro/exposure-editor.tsx:196-199; app/(app)/macro/macro-view.tsx:68 (`defaultIssuerId = contextData?.effects[0]?.issuerId`)
What: the prefill exists only when the selected holding already has an effect row. For a holding with `exposureUnknown` (exactly the case the panel tells the owner to fix: "Add a mapping below") `defaultIssuerId` is null, the holdings payload carries no issuer id, and the hint just says "The id of the issuer this segment belongs to". Nothing shows which ticker the typed id belongs to before the POST (the success notice shows segment/target/weight only). The POST is not confirmed, and a wrong integer silently attaches the weight to another issuer and consumes that issuer's 100% cap.
Failure scenario: owner guesses issuer 12 for a holding, saves "Germany 40% owner-assigned"; it lands on a different company, which now shows 40% Germany; the intended holding still shows "Exposure unknown".
Suggested fix: have the backend return `issuer_id` per holding (or a lookup endpoint), render a select of tickers instead of a number, and show "Issuer N = TICKER" in the submit notice.

### [F2-6] The editor accepts any 2-5 character geography; common wrong codes are stored as dead mappings
Severity: low
Status: CBE
Where: apps/web/src/lib/macro/logic.ts:validateExposureForm (`/^[A-Z0-9]{2,5}$/`); components/macro/exposure-editor.tsx hint "ISO alpha-2, e.g. DE (or EU)"
What: the rule mirrors the backend pattern exactly, but the hint promises ISO alpha-2. `resolveGeography("UK")`, "XK" (Kosovo), "EH", "SGP", "XX1" are all `unknown` (verified), so the row is accepted, counted as mapped value by the backend, never matches a macro series, and shows up as "Unrecognised code UK" on the map.
Failure scenario: owner enters UK for a UK-heavy issuer: 100% mapped, no indicators, no country on the map.
Suggested fix: validate with `resolveGeography(...).kind === "country"|"aggregate"` and offer GB for UK; block or warn on unknown codes.

### [F2-7] Basis filter leaves the "unknown" remainder, takeaway and table columns on the all-bases basis
Severity: low
Status: CBR
Where: apps/web/src/components/macro/portfolio-exposure-map.tsx (extraRows "unknown", takeaway, table), lib/macro/logic.ts:countryExposureView
What: with "Reported" selected, country values become reported-only shares, but "Unknown (unmapped)", "Mapped x%/unknown y%" in the denominator line, the takeaway and the table's "Exposed value"/"Holdings" columns stay all-bases. The shown countries plus the Unknown row no longer add to 100% (the mapped-on-another-basis share is not shown anywhere). The table's "Share of portfolio" column is filter-dependent while the next column is not.
Suggested fix: when filtered, add an explicit "mapped on other bases" remainder, and use filtered figures for the table row or label its columns.

### [F2-8] Macro parser has its own `toDataState` that diverges from the shared adapter
Severity: low
Status: CBE
Where: apps/web/src/lib/macro/parse.ts:67-73 vs lib/data-state/states.ts BACKEND_STATE_MAP
What: `vintage_unavailable` is `partial` here but `not_covered` in the shared map (documented as the single adapter); `temporarily_failed`, a legitimate provider_access state, is reported as "Unrecognised source state" with state `unavailable` (state right, reason wrong, and "unrecognised" is also used for a coverage ledger value the backend does emit). Coverage/macro views will disagree on the same ledger row.
Suggested fix: use the shared `parseDataState` and add the vintage reason on top.

### [F2-9] Alias handling is inconsistent after merging DE/DEU (and similar)
Severity: low
Status: CBR
Where: lib/macro/logic.ts:countryExposureView (`holdings: Math.max(...)`), components/macro/portfolio-exposure-map.tsx:`seriesByGeo.get(d.code)`; macro/holding-macro-panel.tsx (`groupByGeography` by raw string)
What: rows DE and DEU merge into one datum but `holdings` becomes the max, not the distinct union (backend only gives counts), so it can undercount; `seriesByGeo` is keyed by the raw `exposureKey`, so a DEU mapping shows "not reported" series for Germany; indicators stored as DE and DEU render as two "Germany" sections.
Suggested fix: key by `resolveGeography(...).code` in those maps; label merged holdings as "at least N".

### [F2-10] Small edge cases (low)
Severity: low
Status: CBR
- choropleth: a negative finite value has no ramp class and is not zero, so it is drawn hatched ("no data") while its aria-label/tooltip print the number (choropleth-map.tsx fill logic; choroplethBreaks ignores values <= 0).
- `pct()` prints a positive share below 0.0005% as "0.00%" and legend edges at 2 digits round (0.025% -> "0.03%").
- validateExposureForm: `Number()` accepts "0x10" (=16%) and "1e1"; a weight below 0.00005% rounds to 0 and is POSTed (backend allows 0).
- "Add a mapping below" CTA appears under a past "Known as of" date, but a new mapping has `created_at` = now, so it can never appear at that as-of (backend filters `created_at <= as_of`).
- ExposureEditor remounts (key `${ticker}-${defaultIssuerId}`) when the first mapping arrives, wiping the "Mapping saved" notice and any half-typed next row; TradePanel remounts on the same trigger and drops a user-entered reporter/HS.
- legend classes are (lo, hi] but labelled "5% to 10%" / "up to 5%": a value of exactly 5.0% reads as belonging to the second.

## Examined and found sound
- Code tables: frontend NUMERIC_TO_ALPHA2 equals backend ISO2_TO_NUMERIC (228 entries, diffed by script); COMTRADE_ALIASES and ALPHA2_TO_ALPHA3 match; M49 0 -> World, 97 -> EU, 490 -> TW, 842 -> US resolve as in `steward.geo_codes.to_iso`. Unknown codes are returned as `unknown` and listed ("Unrecognised code"), not dropped.
- Topology: of 177 features, 172 resolve to an alpha-2 (Taiwan 158 -> TW, France 250, Norway 578 present); only W. Sahara, Fr. S. Antarctic Lands and the id-less N. Cyprus, Somaliland, Kosovo are hatched "no data". Duplicate codes: none. Antarctica is excluded by design.
- Projection: Natural Earth I coefficients match d3-geo-projection; no ring in the 110m topology produces a horizontal jump > 150 px after splitting (Russia 14 sub-paths, Fiji 3 pieces, both sides of the antimeridian); South Africa's Lesotho hole has opposite winding, so non-zero fill leaves it open; latitude range -85..84 vs clip -58..84 only loses Antarctica.
- Legend classes: `choroplethBreaks`/`rampIndex` verified for 0 (no class, drawn as "Zero"), null (hatched), a value above the last edge (strongest class), values just above an edge, all-zero data (breaks null, only Zero/No data legend), tiny shares (edges scale with power of ten).
- Shares: weights are fractions end to end (Decimal strings parsed by `num`, `formatPct` multiplies by 100); the editor converts percent to fraction with 6-digit rounding (12.5 -> 0.125); backend `ExposureInput` bounds (0..1), geography pattern, hs_code pattern, `valid_to >= valid_from` and the hs_revision requirement are mirrored; hs_revision is sent raw and normalised server-side ("H6" -> HS2022). The frontend is stricter than the backend (one dimension per row).
- Remainder math: unmapped share = unmapped_value / total (falls back to 1 - mapped.ratio only when needed); backend scaling for over-allocation (1/weight_sum) flows through `by_basis`; a country with none on the chosen basis is a true 0 ("Zero (mapped, none)"), a null ratio is "no data".
- Write path: create is POST /macro/exposures with the documented body, 422 pydantic list and string `detail` are rendered, network errors caught, submit disabled while saving; delete is DELETE /macro/exposures/{id} behind an explicit Confirm/Cancel step, disabled while in flight, 404/errors shown on the row. The macro-collect POST queues a job only (free sources), no spend.
- Trade claim parsing: `_slug` in research.py yields exactly 7 colon-separated parts (`trade:reporter:partner:flow:revision:hs:period`, parts reduced to [A-Za-z0-9.%+-_], period an ISO date); anything else is returned as `unparsed` and counted visibly instead of guessed. The parser breaks only if the id format changes; World partner rows are excluded from the Sankey and listed apart.
- Indicator card: change = value - previous (unit kept), range position clamps and an out-of-range flag exists; 5/10-year ranges, `previous_*`, release/known dates map to the backend `ContextPoint` fields; null values stay "n/a", never 0. `as_of` is end-of-day UTC (`T23:59:59Z`) and consistent with the backend's `created_at <= as_of` and `valid_from <= as_of::date` rules.
- Trade metrics units: growth and HHI are fractions in the backend and are rendered as % and x/10,000.

## Not examined
- components/charts Sankey/HBar/treemap internals (outside the assigned files); data-state/ChartFrame components.
- Real-payload behaviour (no live backend): possible duplicate observation rows from the backend's provider_access left join (`endpoint in ('indicators','sdmx')`), which would repeat IndicatorCards with identical React keys, is SUSP and unverified; confirm with `select provider, count(*) from provider_access where endpoint in ('indicators','sdmx') group by 1 having count(*)>1`.
- Visual/a11y rendering in a browser (tooltip placement, focus order, dark-mode contrast of ramp-6 vs zero fill).
