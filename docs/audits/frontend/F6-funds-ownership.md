# Frontend audit F6: X-Ray / Funds and ownership (apps/web)

Scope: apps/web/src/lib/xray, src/components/xray, src/app/(app)/xray, read against apps/backend/src/steward/{funds,ownership,lookthrough}/*.py and api/*routes.py. `npx tsc --noEmit` is clean. Repro tests were throwaway and are deleted.

Counts: high 1, medium 8, low 5 (14 findings; 1 of them SUSP).

### [F6-01] Fetch hook keeps the previous key's data on screen when as_of / holding / fund / filter changes
Severity: high
Status: CBR
Where: apps/web/src/lib/hooks/use-api-resource.ts:50-66 (shared hook); consumers apps/web/src/components/xray/ownership-section.tsx:46-60,198,225, insider-section.tsx:37-45,262, funds-section.tsx:172-176,250-254, exposure-section.tsx:29-45, app/(app)/xray/xray-view.tsx:89-96 (no `key`)
What: `useApiResource` never goes back to `loading` when `fetcher` changes (state stays `ok` with the old key's data); on a failure of the new request the old key's data becomes `previous` and is shown by `ResourceShell` as "Showing the last value that loaded". None of the XrayView children is `key`ed by ticker, and no panel prints the ticker or as-of it is showing.
Failure scenario: Owner picks holding B on the Ownership tab: for the duration of the request (13F, N-PORT, 13D/G, Form 4 and PDMR are all separate requests) holding A's holders, ranks, stakes and Form 4 rows are displayed with a normal "Verified" footer under the select showing B. If B's request fails (503, timeout) A's data stays as the "last value", unlabelled as another company. The same applies to `as_of` (Exposure, Funds), the Factsheet fund selector, and the audit "alerts only" toggle. In ExposureSection the Sankey also silently mixes an `overlap` from the old as_of (`overlapData` falls back to `previous` on error and to the old `ok` data while loading) with the new date's aggregate, so `cash_derivative_other_weight` from date X is applied to fund weights of date Y, with no note (the "Fund overlap data did not load" note only fires when `overlap` is null).
Evidence: `useApiResource` only calls `setState(prev => next.kind==="error" && prev.kind==="ok" ? {...next, previous: prev.data} : next)`; `reload` is the only place that resets to loading, and it is not called on key change. `exposure-section.tsx:48-50`: `overlap.state.kind === "ok" ? data : kind === "error" ? previous : null`.
Suggested fix: key every keyed block (`<InstitutionalBlock key={ticker}>`, `<AggregateBody key={asOf}>` etc.) or make the hook reset to `loading` (and drop `previous`) when `fetcher` identity changes; print the requested ticker/as-of in each panel header; do not use a stale-date overlap for the Sankey.

### [F6-02] Treemap puts a whole issuer into one bucket although the bucket weights are per row
Severity: medium
Status: CBR
Where: apps/web/src/lib/xray/exposure.ts:117-160 (`issuerKey`, `rest`); backend apps/backend/src/steward/funds/aggregate.py:371-381,397-400,465-470 (`acc.sector or sector` first-seen, buckets added per row)
What: backend buckets sum each row's weight under that row's own sector/country/currency, but `IssuerExposure.sector/country/currency` is the first value seen for the merged issuer. The frontend assigns the issuer's full `weight` to that single key. An issuer held directly (config sector "Technology") and through funds (fund CSV "Information Technology"), or an ADR + ordinary merged under one issuer with different currency/country, is split across buckets in the backend but drawn in one.
Failure scenario (vitest repro): buckets `Technology 0.10 (direct 0.10)`, `Information Technology 0.20`; issuer I weight 0.30, sector "Technology". Output: `Technology` group (0.10) holds leaf `I = 0.30` and is flagged overflow; `Information Technology` (0.20) shows only an "Other issuers" remainder 0.20. The leaf is drawn 3x the size of its group share and the fund-held 20% sits under the wrong sector. Only a generic "named issuers add up to more than the bucket" line appears. Currency and country dimensions are hit by every ADR/ordinary merge.
Evidence: repro output `[["Information Technology",0.2,false,[["Other issuers ...",0.2]]],["Technology",0.1,true,[["I",0.3]]]]`.
Suggested fix: do not nest issuers by the issuer-level key. Either have the backend expose per-bucket issuer contributions (weight per issuer per dimension key), or cap each leaf at the bucket weight and show the rest in the right bucket as "other"; at minimum, scale leaves to the group weight instead of normalising to the group area.

### [F6-03] Negative fund cash/derivative bucket silently removed from the treemap; area claim becomes false
Severity: medium
Status: CBR
Where: apps/web/src/lib/xray/exposure.ts:126-131,168-173; apps/web/src/components/xray/nested-treemap.tsx:62; exposure-section.tsx:186-197 (`dropped` never read); backend aggregate.py:453,482,621
What: `FUND_CASH` bucket weight is `w*remainder` and is negative for swap-based/derivative-offset funds (equity rows > 100%). `buildExposureTreemap` drops buckets with weight <= EPS and counts them in `dropped`, but no component reads `dropped`. `reconciles` (backend) sums the signed buckets so it is still true, so the "do not add up" warning is also silent. `squarify` normalises to the sum of drawn groups.
Failure scenario: classified groups 1.02 + fund cash -0.02: repro prints `dropped 1 drawn 1.02 reconciles true`. The treemap fills the full canvas with groups that total 102% of portfolio value while the legend/units say "% of total portfolio value" and area is "proportional to the share of TOTAL portfolio value". The 2% negative offset is not mentioned anywhere (the Sankey does mention it; the treemap does not).
Suggested fix: surface `treemap.dropped` and the drawn total next to the chart ("-2.0% fund derivatives offset not drawn; drawn groups sum to 102%"), and use `drawn !== 1` as the trigger, not only `reconciles`.

### [F6-04] Sankey loses flow when two positions share one fund ISIN
Severity: medium
Status: CBR
Where: apps/web/src/lib/xray/exposure.ts:247-263 (`shownByFund` keyed by ISIN, subtracted once per `report.funds` row), 264-300
What: backend `FundAsOf` has one row per position ticker (`load_portfolio` selects per `position_snapshot.ticker`), and the same fund held on two listings (e.g. VUSAl_EQ and VUSA_EQ, same ISIN) yields two rows with the same `isin`. Nodes `pos:/src:` are deduped by ISIN and the links merged, but the loop over `report.funds` subtracts the issuer weight already shown for that ISIN (summed over both listings) from each row's remainder.
Failure scenario (repro): funds A 0.30 and B 0.20, same ISIN, issuer I via both channels 0.24+0.16. Output links: `pos>src 0.500`, `src>iss 0.400`; remainders (rest = 0.30-0.40, 0.20-0.40) are negative and dropped, so the fund node takes in 0.50 and sends out 0.40 (0.10 vanishes; node drawn at max(in,out) hides it). `drawnTotal` only counts pos links, so no gap note fires.
Suggested fix: aggregate `report.funds` by ISIN (sum weight, unknown_weight) before building flows, or compute the remainder per ISIN once.

### [F6-05] PDMR panel presents channel-wide counts and state as if they were about this holding
Severity: medium
Status: CBE
Where: apps/web/src/components/xray/insider-section.tsx:246-304, apps/web/src/lib/xray/insider.ts:74-91; backend apps/backend/src/steward/ownership/pdmr_read.py:121-134
What: `status.parsed`, `status.attempted`, `status.by_status` and `status.state` are computed from `select status, count(*) from public.pdmr_notification group by status` with no ticker filter. The panel shows "N parsed of M attempted", uses them as the provenance coverage denominator, and maps `available` to a Verified badge; the panel is rendered for every holding, including US stocks.
Failure scenario: after any UK announcement is parsed, a US holding with zero UK notifications shows "No PDMR announcement is stored for this holding." together with a Verified state badge, "47 parsed of 52 attempted" and `Parsed 47, Could not be read 5`, i.e. figures that belong to other issuers. Conversely a UK holding whose own announcements all failed shows Verified/Partial from global counts.
Suggested fix: only show the status block as "channel status (all issuers)" and derive the per-holding state from `notifications` (zero rows => not_covered/none stored; any non-parsed row => partial); hide the UK block, or mark it "not applicable", for non-GB listings.

### [F6-06] SUSP: backend upper-cases tickers for PDMR and insider lookups while T212 tickers carry a lowercase suffix
Severity: medium
Status: SUSP
Where: apps/backend/src/api/pdmr_routes.py:28, apps/backend/src/api/insider_activity_routes.py:(call `ticker.upper()`), insider_routes.py:77; frontend passes the raw holding ticker (apps/web/src/lib/xray/view.ts, xray-view.tsx)
What: `/holdings/{ticker}/pdmr-notifications` and `/insider-activity` call `ticker.upper()` and query `where ticker = $1`. UK/EU T212 tickers are stored mixed case (`VODl_EQ`, tests use it) and `pdmr_notification.ticker` comes from `news_item.affected_ticker`, which is one of the candidate (T212) tickers.
Failure scenario: choose VODl_EQ on the Insiders tab: request `VODL_EQ`, no rows, "No PDMR announcement is stored" for a holding that has them.
Confirm with: `select ticker from pdmr_notification limit 5` on real data; or call `GET /holdings/VODl_EQ/pdmr-notifications` against a row stored as `VODl_EQ`. Fix: do not upper-case, or match case-insensitively.

### [F6-07] 13D/G group percent mislabelled
Severity: medium
Status: CBE
Where: apps/web/src/components/xray/ownership-section.tsx:318-324 and `isGroup` in lib/xray/ownership.ts:218-221; backend control_stakes_read.py:26-30 (LIMITATIONS)
What: UI says "Group of N; percent is the group's, not per person." The backend states "The filing-level stake is the largest single reporting-person figure. Group members report overlapping beneficial ownership, so members are never summed; for a group it is a lower bound." Also `isGroup` is true for any filing with >1 reporting person, not only a group.
Failure scenario: a filing by two reporting persons (a fund and its parent, 6.1% and 9.8% of the class) is shown as "% of class (as filed) 9.80%, Group of 2; percent is the group's" - it is one person's figure and the group's aggregate is not known.
Suggested fix: change the note to "largest single reporting-person figure; the group total is at least this" and show per-holder `percent_of_class` (field exists on `holders[]`).

### [F6-08] Insider class table labels whole open-market classes "counts toward net" and double-counts option legs
Severity: medium
Status: CBE
Where: apps/web/src/lib/xray/insider.ts:46-66, components/xray/insider-section.tsx:85-90,119-137; backend insider_read.py:verdict() (lines ~190-205)
What: the class table groups rows by `economic_class` and tags `open_market_buy/sell` as "Counts toward net discretionary activity: yes". The backend only counts a row when it is non-derivative, has a share count, is not superseded and is not under a Rule 10b5-1 plan (`rule_10b5_1_plan is True` -> excluded `pre_planned_10b5_1`). The row-level `counts_toward_net_discretionary` / `exclusion_reason` fields are available but not used for the totals. Also code M/X/O produce both a derivative row and a stock row, both `option_exercise`, and `classTotals` sums both legs' shares.
Failure scenario: CEO sells 50,000 under a 10b5-1 plan and 2,000 outside one: headline "Sold (open market) 2,000" but the class list shows "Open-market sell 52,000, counts toward net: yes". An exercise of 10,000 options shows 20,000 shares under "Option exercise".
Suggested fix: split totals by `counts_toward_net_discretionary` (and show 10b5-1/derivative exclusions as their own lines), and count only non-derivative rows for share totals of exercise/conversion classes.

### [F6-09] Aggregate provenance ignores over-reported funds; "stale" hides unknown weight
Severity: medium
Status: CBR
Where: apps/web/src/lib/xray/exposure.ts:55-76
What: state is derived from `stale_funds`, coverage count and `unknown_fund_weight` only. A fund whose rows sum above 100% has `reconciles=false`, `unknown_weight=0` and contributes `over_reported_weight` (buckets then total >100%); neither `FundAsOf.reconciles` nor `unknown.over_reported_weight` is considered. And because `stale` is checked before `partial`, a stale fund hides that e.g. 30% is not looked through (the reason text only lists stale names).
Failure scenario (repro): fund rows_total 1.10, `reconciles:false`, over_reported 0.05, no unknown -> `aggregateProvenance(...).state === "verified"`. With one stale fund and one missing fund the badge reads Stale and the unknown 30% appears only in the Facts panel.
Suggested fix: add `reconciles === false` / `over_reported_weight > tol` -> partial (or disputed) with reason; combine reasons instead of early-returning.

### [F6-10] Manual ledger row silently overrides a derived row; confirm text understates it
Severity: medium
Status: CBR
Where: apps/web/src/components/xray/coverage-section.tsx:195-216 (ManualEntry), backend coverage.py:save_entry (conflict update where origin='derived' or excluded.origin='manual')
What: PUT upserts on (subject_kind, subject_key). For an existing derived fund row (tier issuer_downloadable, `latest_holdings_as_of` set) a manual save replaces tier/reason/flags and writes `latest_holdings_as_of = NULL` (the form cannot send it), and the refresh then never updates that row. The confirm body only says it "replaces an earlier manual row". The form defaults the tier to "Official holdings", `subject_key` is not trimmed to ISIN format or upper-cased for funds (a lower-case or mistyped key creates a second row), and there is no way to delete or revert a manual row.
Failure scenario: owner records a fund with the key typed in lower case: duplicate row, derived row stays "unavailable" and the typo row says "official, owner-verified". Owner re-saves an existing fund: its "Holdings as of" becomes "—" permanently.
Suggested fix: pre-select the existing row's values and say "this overrides the derived row for <key> and freezes it against refresh"; validate fund keys against the ISIN regex and upper-case; default tier to a neutral choice; offer revert.

### [F6-11] Insider panel: as-of is the window end (today) and state is "verified" with no freshness test
Severity: low
Status: CBE
Where: apps/web/src/components/xray/insider-section.tsx:57-75, 91-99
What: `asOf: nd.until` is the default window end (today). A holding whose newest filing is months old reads "As of <today> · known since <old date>" with state Verified. Also `nd.net_value === null` is explained as "some counted rows have no price", but it is also null when no row counted (all rows excluded), and `value_complete` is true then ("every counted row has a price").
Suggested fix: use the newest `filed` as asOf (or flag stale beyond the Form 4 cadence), and distinguish "no counted rows".

### [F6-12] as_of URL value not validated; error detail rendered as "[object Object]"; as_of silently applies to the Funds tab
Severity: low
Status: CBR
Where: apps/web/src/app/(app)/xray/xray-view.tsx:42-45 (`useState(params.get("as_of") ?? "")`), lib/hooks/use-api-resource.ts:13-19, funds-section.tsx:19-23
What: `?as_of=garbage` (or a future date) goes straight to `/funds/aggregate`; FastAPI returns 422 with an array `detail`, and `describeError` does `String(error.detail)` -> "[object Object]". The date input shows blank. The as_of picker exists only on the Exposure tab, but the Funds tab's overlap request also uses it with no visible indication of which date was requested.
Suggested fix: validate `/^\d{4}-\d{2}-\d{2}$/` and not-in-future on read; show the active as_of on the Funds tab; join array details in `describeError`.

### [F6-13] Audit verdict details not surfaced: methodology change has no diff, backwards/restated alerts have no reason
Severity: low
Status: CBE
Where: apps/web/src/lib/xray/parse.ts:96-124, components/xray/funds-section.tsx:309-317; backend funds/drift.py:81-130
What: only `detail.changes` and `detail.note` are read. `methodology_change` carries `previous_rules`/`rules` (frontend shows just the current rules), and `value_change` from `as_of_moved_backwards` / `restated_for_same_as_of` may have an empty `changes` map, so an alert row shows no explanation.
Suggested fix: render previous vs current rules and the two flags.

### [F6-14] 13F lag and missing-quarter wording
Severity: low
Status: CBE
Where: apps/web/src/lib/xray/ownership.ts:52-56,82-102; components/xray/ownership-section.tsx:84-88
What: `latest_filed` is `max(filed)` over all effective filings of the period including late NEW HOLDINGS/restatement amendments, but the takeaway says "The filing came N days after quarter end" and the table "Filed (lag)". `quartersSincePrevious` is null for the oldest returned row although the backend computed its change against a fifth, possibly non-adjacent quarter, so the "span more than one quarter" warning never covers it. Lag arithmetic itself is exact (2026-06-30 -> 2026-08-14 = 45 d, 2026-12-31 -> 2027-02-14 = 45 d, UTC-parsed).
Suggested fix: word it "latest filing in the quarter was filed N days after"; ask the backend for the previous period date per row.

### [F6-15] Minor
Severity: low
Status: CBE
Where: funds-section.tsx:169-177 (FactsheetBlock): with no funds it still fires `GET /funds//factsheet` because the hook runs before the early return. funds-section.tsx:235: `total_assets/1e9` prints "0.00 bn" for funds under 5M and ignores `total_assets_as_of`. nport positions limit 50 with no "more rows exist" flag (ownership-section.tsx:198). `useApiResource` replaces `previous` with null after a second consecutive failure (last-good lost).

## Examined and found sound
- Treemap/squarify: zero, negative, NaN weights filtered (`finitePositive`, squarify filter), special buckets (cash, fund cash, not-looked-through, unknown, unclassified) ordered last, never merged into issuers, remainder only for classified buckets; `Other issuers` computed as bucket - named.
- Sankey: unknown/not-looked-through/fund-cash are separate nodes; `rest = w - unknown - max(cashDeriv,0) - shown` conserves flow for single-listing funds; negative derivative offsets become a note, not a negative link; node ids unique by construction so label collisions only cost label overlap (`spreadLabels`); links merged by endpoints.
- Backend/frontend key names for aggregate, overlap, coverage ledger, ownership, N-PORT, 13D/G, insider, PDMR, factsheet (`field_names_verified` semantics inverted correctly) and audit rows match; Decimal-as-string/float handled by `toNum`; unit handling (fractions via `formatPct`, 13D/G and N-PORT `pct_of_fund` in percent points) is correct.
- Channel separation: no code sums 13F, N-PORT, 13D/G and fund channels; 13D/G percent shown as filed with the cross-check label beside it.
- Rank flow: ranks relative to the latest top holders, tie-break by shares then CIK matches backend ordering, `move` sign right, new managers get no line, disclosed in units.
- 13F breadth: oldest change is null (not 0) and the DivergingBar renders null as a dash; quarter labels correct; stale cutoff 140 d is consistent with 45-day filing lag.
- Confirm gates: every job trigger, ledger refresh, manual PUT and snapshot pin goes through `ConfirmAction` (nothing fires on first click or on page load); `busy` disables the confirm button (no practical double submit); correct methods/bodies (`POST /funds/coverage-ledger/refresh`, `PUT /funds/coverage-ledger`, `POST /funds/snapshots/pin` with exactly one of snapshot_id/as_of).
- Insider classes and Form 4 code mapping keys match `insider_classify.CLASSES`; superseded rows excluded; 200-row limit flagged as partial; PDMR state mapping covers the backend AccessState values.
- Tab/ticker URL state uses `replaceState` with try/catch; page wrapped in Suspense.

## Not examined
- Real-data behaviour (nothing run against a backend), CSS/visual layout and mobile widths, label overlap in the Sankey beyond reading `spreadLabels`.
- `lib/api/client`, `lib/charts/*` internals beyond sankey/squarify, `components/data-state/*` beyond `ChartFrame`/`ProvenanceFooter`, SrTable a11y.
- Backend classification of fund rows (`lookthrough/read.py`) and N-PORT/13F loaders; whether `instrument_security_link` makes F6-06 moot.
- External link scheme safety (`source_url` rendered as href without a scheme check; React 19 blocks `javascript:` hrefs).
