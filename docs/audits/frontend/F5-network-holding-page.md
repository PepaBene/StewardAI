# Frontend audit F5: business network, holding data-context, evidence ledger

Area: apps/web/src/lib/network, components/network, app/(app)/holdings/[ticker]/network, lib/holding-data, components/holding, new sections of holding-view.tsx and the date labels of research-report-view.tsx.
Method: read every file in the area against the backend routes (free_sources_routes.py, graph_routes.py, free_source_reads.py, graph/{deals,patents,relationships,kpi,decisions}.py, data_context/*, reference/read.py). Throwaway vitest repro run and deleted. Existing tests (77) and `tsc --noEmit` pass; they do not cover any finding below.
Status keys: CBE = confirmed by execution/evidence in code on both sides, CBR = confirmed by reading, SUSP = plausible, needs live data.

Counts: high 1, medium 10, low 7 (18 findings).

---

### [F5-01] 13F "% of outstanding" printed 100x too small in the evidence ledger
Severity: high
Status: CBE
Where: apps/web/src/lib/holding-data/evidence-items.ts:60
What: `pct_of_shares_outstanding` of a 13F holder is a FRACTION (backend `ownership/read.py:59-63`: "shares x ads_ratio / shares outstanding, as a fraction"; `pct_of()` returns `shares*ratio/outstanding`). The ledger interpolates it as `${h.pct_of_shares_outstanding}% of outstanding`.
Failure scenario: a manager holding 4.12% of the issuer (`pct_of_shares_outstanding = 0.0412`) is listed as "13F · Vanguard — 412,345,000 shares · 0.0412% of outstanding". The same endpoint family is correctly formatted elsewhere (`reference/ownership-section.tsx` uses `formatOutstandingPct`), so the two screens disagree by 100x. The control-stake line right next to it (`stake_percent`) IS in percent units (control_stakes.py:344 computes `x*100`), so the unit handling is inconsistent inside one function.
Evidence: `h.pct_of_shares_outstanding != null ? \`${h.pct_of_shares_outstanding}% of outstanding\`` ; backend description string quoted above.
Suggested fix: format with `formatPct(value, 2)` (fraction in) for 13F; keep `${stake_percent}%` for 13D/G. Add a unit test with 0.0412.

### [F5-02] An as-of of "today" is sent as a future instant and the holding page's data-context and evidence requests return 422
Severity: medium
Status: CBR (backend guard read; not run live)
Where: apps/web/src/lib/network/logic.ts:29-31 (`asOfParam`), apps/web/src/lib/holding-data/logic.ts:19-27 (`asOfQuery`, `isFutureDay`), apps/web/src/app/(app)/holdings/[ticker]/holding-view.tsx (guard `isFutureDay(asOfDay, today) ? "" : asOfDay`), components/network/as-of-control.tsx (`max={today}`)
What: the picker allows `today` (max=today). `asOfParam("2026-10-03")` = `2026-10-03T23:59:59Z`. `GET /holdings/{ticker}/data-context` and `/evidence-summary` call `_reject_future` (data_context_routes.py:33-37): `stamp > now(UTC)` -> 422 "as_of cannot be in the future". `isFutureDay` only blocks `day > today`, so `day == today` passes. Also `today` on the holding page is the LOCAL date (`isoDay` uses getFullYear/getMonth/getDate) while the API compares UTC end-of-day.
Failure scenario: owner picks today's date on the holding page: both the data-context and the evidence sections turn into "API responded with HTTP 422" (or keep the previous "now" data beside the error). Same for a user east of UTC picking their local "today" while UTC is still the previous day (day > UTC today is not even caught).
Evidence: `_reject_future` code above; `isFutureDay` is `day > today`.
Suggested fix: for a day >= today (UTC) send no `as_of` (treat as "now") or clamp to `min(endOfDay, now)`; compute `today` in UTC in holding-view.

### [F5-03] Data from the previous as-of date (or the last good load) is rendered under the new as-of label
Severity: medium
Status: CBR
Where: apps/web/src/lib/hooks/use-api-resource.ts (state kept "ok" when the fetcher changes), apps/web/src/components/network/resource-boundary.tsx:33-44 (`children(previous.value, true)`; the `stale` argument is ignored by every caller in network-view.tsx)
What: when `asOf` changes, `fetchAndApply` runs but `state` stays `{kind:"ok", data: <old date>}` until the new response arrives, so panels draw the old rows with `asOfDate`/provenance of the new date and no loading indicator. If the new request fails (500, 422, network), `previous` is the data of a different as-of and is drawn again under the new date's label (the red alert above it is the only hint, and the alert is per panel).
Failure scenario: owner switches from "now" to 2025-06-30; for the second the edge ledger still lists today's edges while the info banner says "Viewing the network as known on 30 Jun 2025", and the provenance footer says `asOf 2025-06-30`. If that fetch then 500s, the same today-data stays under the 2025 label with only an alert above.
Evidence: `reload` only resets to loading when `prev.kind !== "ok"`; no reset in the `useEffect([fetchAndApply, lastSuccessJobId])` path.
Suggested fix: key the resource state by fetcher (reset to loading when `fetcher` identity changes), or pass `stale` and dim/label the panel "showing a different date".

### [F5-04] Concentration "at least" floor is applied to the wrong sum; backend never sends null sums
Severity: medium
Status: CBE (logic repro) + CBR (backend SQL)
Where: apps/web/src/lib/network/logic.ts:572-580 (`isFloor`), components/network/concentration-panel.tsx:50-56,86-92
What: `missing_share_count` counts named AND anonymous disclosures without a share, but `isFloor = missingShareCount > 0` is used to prefix only the NAMED sum with "at least". The backend also returns `coalesce(sum(...),0)` for every sum, so `named_share_sum`/`anonymous_share_sum` are `0`, never null (relationships.py:243-248). `hasShare` (computed, never used) would have caught that.
Failure scenario (reproduced): FY2025 customers: 2 named reporting 30% and 20%, 1 anonymous reporting nothing. Result: named "at least 50.0%" (it is exact), anonymous "0.0%" (it is unknown), floor flag set. Reverse case: 2 named, one without a share, no anonymous: named "at least 30%" correct, anonymous "0.0%" shown for zero anonymous customers. If anonymous disclosures are the ones missing a share the anonymous sum is shown as an exact total with no "at least", while the report says "disclosed without a share (not imputed)" only in a small line. Largest-share takeaway also prints "0.0%" when no row has a share (`Math.max(...rows.map(r => r.largestShare ?? 0))`), which is missing-as-zero.
Evidence: repro output `FLOOR true 0.5 0`; code lines above.
Suggested fix: ask the backend for per-side missing counts (or derive: floor for named iff some named share missing). Until then show "not reported" when `namedCount`/`anonymousCount` is 0 and when all shares of that side are missing, and compute the takeaway max only over non-null `largestShare`.

### [F5-05] "The API reports no gap" is shown when the data-context request failed or is still loading
Severity: medium
Status: CBR
Where: apps/web/src/components/holding/data-context-section.tsx:30-34,76 ; gaps-list.tsx:11-13
What: `gaps = mergeGaps(last(context)?.gaps, last(evidence)?.gaps)`; `last()` is `null` for loading and for an error with no previous value. The list is rendered as soon as EITHER request has left loading (`context.kind !== "loading" || evidence.kind !== "loading"`), and with an empty array it says "The API reports no gap for this holding at this cutoff."
Failure scenario: data-context returns 500/422 (see F5-02) while evidence-summary succeeds with `gaps: []`: the Gaps panel claims there is no gap, although the primary gap source (identity, financial history, exposure, network, patents, forward, coverage) was never read. Same for the first second of loading when evidence arrives first.
Suggested fix: only render the "no gap" sentence when both requests are ok; otherwise "Gaps could not be read (data-context unavailable)".

### [F5-06] Forward citations of EPO OPS families are 0, drawn as "no citations", not "unknown"
Severity: medium
Status: CBR (backend store + column default), not run against live data
Where: apps/web/src/lib/network/logic.ts:366-367, components/network/patent-matrix.tsx:33-37,196
What: the only automated patent source (EPO OPS, company_research.py:762) stores `forward_citations=0` with the note "not provided by OPS bibliographic data"; the column is `integer not null default 0` and the API returns `citations_forward: 0`. The frontend carries a careful "citations unknown" path (`citationsUnknown`, `citationLevel`) that keys on `null`, so it is dead for real data.
Failure scenario: every OPS family cell is coloured/labelled "no citations", the picked-cell list says "forward citations 0", the table column shows 0 and the units caption says "forward citations summed over the cell's families". A reader concludes the portfolio's patents are uncited, when the number was never measured.
Suggested fix: backend should expose null when the source cannot supply it; until then treat `citationsForward` as unknown when `source` is an OPS source (or when every family has 0 and source is not owner-entered), and say so in the legend.

### [F5-07] Holding-page network/patent card counts owner rows, rejected edges and unattributed families as confirmed-share denominators; "verified" with 0 attributed
Severity: medium
Status: CBE (parse repro) + CBR
Where: apps/web/src/lib/holding-data/parse.ts:26-47, components/holding/network-patents-card.tsx:47-57,76-79
What: (a) `business_network.patents` has one row PER OWNER ROW (join on `patent_family_owner`, PK family_id+ticker+applicant), so a family with two applicants is listed twice. `parsePatentSummary` counts `families.length`, `attributed` and `byLegalStatus` over rows without deduping by `family_key` (the network page's `buildPatentMatrix` does dedupe, so the two pages disagree). Repro: the same family twice -> `families: 2, attributed: 2, byLegalStatus {granted: 2}`. (b) `edges` includes rejected edges; `candidates = edges - confirmed` therefore includes rejected ("candidates, not facts") and the denominator "N of M edges confirmed" includes them, while the network page's `edgeCoverage` excludes rejected. Same graph, two different denominators. (c) provenance `state: patents.families === 0 ? "not_covered" : "verified"` is "verified" even when `attributed == 0` (only suggested ownership rows), contradicting its own takeaway "0 of 3 families are attributed".
Suggested fix: dedupe by `family_key` (any(attributed)), exclude rejected from candidates/denominator, state "partial"/"not_covered" when attributed is 0.

### [F5-08] Legal-structure panel is not as-of aware but is stamped with the selected as-of date
Severity: medium
Status: CBR
Where: apps/web/src/components/network/identity-panel.tsx (provenance `asOf: asOfDate`), lib/network/client.ts:92 (`fetchIdentity(ticker)` has no as_of), network-view.tsx info banner
What: `GET /holdings/{ticker}/identity` takes no as_of and returns today's confirmed parents/subsidiaries/owners (`reference/read.py:224-247`). Only the two history sub-lists get `as_of`. The banner on the page lists the patent timing table as the sole exception.
Failure scenario: viewing 2024-03-31: "Accounting consolidation and control" shows a parent that was added in 2026, and the footer says as-of 2024-03-31.
Suggested fix: when an as-of is set, hide/label the block "current structure, not as of <date>" and drop `asOf` from its provenance; add it to the banner's list of exceptions.

### [F5-09] A failed review-queue or reconciliation request is shown as an empty result
Severity: medium
Status: CBR
Where: apps/web/src/app/(app)/holdings/[ticker]/network/network-view.tsx:84-93,104-109 ; components/network/review-queue.tsx:41-52
What: `queue` is passed as `state.kind === "ok" && data.ok ? value : null`, `reconciliations` as `... : []`, `trendRows` likewise. On loading/error/rejected shape the ReviewPanel simply omits "Filings to read" and "Open contradictions" with no notice (headline drops the queue clause), and ResearchPanel prints "None open." for unexplained parent/successor changes.
Failure scenario: `/relationship-review/queue` returns 500: the owner sees "Passages awaiting a decision" with no filings and no contradictions and believes none are open, although the concentration panel above may show "N open contradiction(s)".
Suggested fix: render an "unavailable" notice for each of these three resources when not ok (they already have `ResourceBoundary`-style states available).

### [F5-10] One DecisionControl instance is reused across edges: typed reason and last result carry over to the next selected edge
Severity: medium
Status: CBR
Where: apps/web/src/components/network/edge-ledger.tsx:216-217 (`<EdgeDetail ... edge={chosen}/>` without `key`), decision-control.tsx:44-47 (state in the control)
What: selecting edge B while edge A's detail is open keeps the same `EdgeDetail`/`DecisionControl` fibre, so the textarea still holds the reason written for A and the green "Done. Edge confirmed." message of A is shown on B. Pressing "Reject" on B submits A's reason into B's audit trail.
Suggested fix: `<EdgeDetail key={chosen.id} .../>`.

### [F5-11] Decision buttons are derived from a status that may not be the row's current status
Severity: medium
Status: CBR (+ CBE for the table)
Where: apps/web/src/components/network/decision-control.tsx:19-23, edge-ledger.tsx:313-320, review-queue.tsx (disclosures), deals-timeline.tsx (steps)
What: (1) With an as-of date set the page shows historical statuses (`status_as_of_sql`) but still offers decisions, which apply NOW. A row that was "suggested" on 2025-06-30 and is "confirmed" today shows Confirm/Reject/Dispute; "Reject" is legal from confirmed (decisions.py TRANSITIONS), so the owner rejects a confirmed fact while reading a suggestion. The banner mentions it but the buttons stay live. (2) `unrecognised` falls into the default branch and offers "Confirm as fact" (repro: `['confirm','reject','dispute']`). (3) Legacy GLEIF/Wikidata rows with status confirmed offer "Reject", but `/relationships/{id}/reject` only accepts suggested/disputed (reference/read.py:283-286) -> always 409.
Suggested fix: disable the decision controls when `asOfDate` is set (or re-read the current status first), return `[]` for unrecognised, and for legacy edges offer reject/confirm only from suggested/disputed.

### [F5-12] Peer takeaway prints "0%" when no peer is ranked; peers with no OWN families are labelled "no patent data"
Severity: low
Status: CBE
Where: apps/web/src/components/network/peer-leadership.tsx:23-27,76-80
What: `formatPct(ranked[0]?.overlapShare ?? 0, 0)` -> "0 of 3 confirmed peers have patent data; the top peer shares 0% of this company's families" when `ranked[0]` is an unranked row (`overlapShare` null, repro `TOP null 0`). Backend sets `overlap_share=None` when the HOLDING has no attributed families (`mine` empty), even if the peer has plenty; the UI then says every peer has "no patent data".
Suggested fix: use the first row with `ranked === true`, otherwise omit the sentence; word the unranked reason as "no attributed families on either side".

### [F5-13] Deal stage track marks stages "confirmed" for events the state machine ignored
Severity: low
Status: SUSP (needs a deal with an out-of-order confirmed event)
Where: apps/web/src/lib/network/logic.ts:523-529
What: `dealStages` marks a stage confirmed for any confirmed timeline step. The backend `_walk` ignores events that are not a valid next step (e.g. a confirmed "approved" dated after "terminated") and reports them in `deal.ignored`; the confirmed headline state excludes them but the track does not consult `ignored`.
Suggested fix: exclude steps whose `eventId` is in `deal.ignored` from `reach` (or mark them "ignored by the state machine").

### [F5-14] Patent matrix year axis is capped at 60 columns from the EARLIEST year
Severity: low
Status: CBE
Where: apps/web/src/lib/network/logic.ts:400-404
What: `range()` stops after 60 entries counting up from the minimum year, silently dropping the latest years while the takeaway still counts the family as "placed". Repro: families with priority 1900 and 2020 -> years 1900..1959, the 2020 family has no cell, `placedFamilies = 2`.
Suggested fix: drop only empty years (or cap from the newest end) and report families outside the drawn range.

### [F5-15] Patent detail shows an arbitrary owner row (last wins) while the matrix uses the first
Severity: low
Status: CBR
Where: apps/web/src/components/network/patent-matrix.tsx:57 (`new Map(patents.map(...))`) vs logic.ts:338-339 (first row wins)
What: with several owner rows per family the picked-cell list can show an applicant/ownership basis "suggested" for a family counted as attributed through another row.
Suggested fix: use the same dedupe (prefer the attributed/dated owner row) for the detail list.

### [F5-16] Review-queue provenance says "verified" when every passage is rejected; unrecognised counted as suggested
Severity: low
Status: CBR
Where: apps/web/src/components/network/review-queue.tsx:46,87-89
What: `counts.confirmed === open.length ? "verified"` with `open = non-rejected`; if all disclosures are rejected, `0 === 0` -> verified. Headline adds `unrecognised` into the "suggested" count.
Suggested fix: `open.length === 0 ? "not_covered"`; show unrecognised separately.

### [F5-17] Smaller label and unit issues (grouped)
Severity: low
Status: CBR
- lib/network/logic.ts:537-538 `PCT_KEYS` has only `cash_pct`/`stock_pct`; backend terms also carry `ownership_pct` and `interest_rate_pct` (deals.py `_DECIMAL_KEYS`), rendered as a bare "5.5" labelled "Interest rate pct" (repro).
- lib/holding-data/evidence-items.ts:85 prints a dependency's `reported_share` as the raw fraction/Decimal string ("0.3500 issuer_revenue") with no unit; the network page formats it as a percent.
- components/network/badges.tsx / parse.ts:107: backend writes tier `unverified` (decisions.py `tier_value`); `toTier` maps it to "Tier not stated" ("The API did not state an evidence tier"), which is wrong wording.
- components/network/thesis-links-panel.tsx:15: "confirmed with primary evidence" but the server rule is `status == confirmed and evidence_id is not null` (kpi.py), which also admits owner_review tier. Also verdict observed/threshold are shown without the KPI operator or unit (both are in `kpi`).
- lib/network/logic.ts:240-254 + peer-leadership.tsx: leadership events get `to: null` and render "from <date> to present", which asserts the person still serves; an event row has no end date.
- components/holding/evidence-ledger.tsx:49: an empty-string passage yields "No passage: " with a blank reason (only `null` gets a reason).
- research-report-view.tsx:222: the "Evidence for TICKER" link targets `#evidence`, which only exists after the holding page's async evidence request finishes; the browser's one-shot hash scroll misses it.

### [F5-18] "Cited sources as of" in reports mixes period/event dates, not known-at dates
Severity: low
Status: CBR
Where: apps/web/src/lib/holding-data/logic.ts:claimKnownAsOf, components/holding/claim-known-as-of.tsx, research-report-view.tsx header
What: backend `Source.as_of` is a period end (`f["period"]`, 13F `p.period`), a snapshot date, a price-bar date or a publication date (research/gather.py), so the max of them is the newest period/event date, not when the claim became known; forward periods would show future dates. The wording ("Cited sources as of", tooltip "latest as-of date of the sources") is mostly honest, but the component/function names and the section heading in the header ("known-as-of-range") invite reading it as a known-at date, and showing only the latest hides older cited sources.
Suggested fix: keep the label but also show the oldest cited date, or rename to "Source data as of".

---

## Examined and found sound
- Status handling: `toStatus` maps anything outside confirmed/suggested/disputed/rejected to "unrecognised" (never confirmed); `isFact` is `confirmed` only; badges are dashed/outlined and labelled in text, not colour only; `edgeCoverage`/`rowsState` treat non-confirmed correctly; unknown API ids fail the whole payload and show "unavailable".
- Edge id kinds: numeric ids -> `/company-graph/edges/{id}/decision` with `{decision, reason}`; `legacy:N` -> `/relationships/{id}/confirm|reject` with no body; reason min 10 matches `DecisionBody`; event/disclosure endpoints, `reopen` semantics and contradiction resolve (min 5) match the backend.
- Provider refresh confirm gate: Refresh arms, a second explicit button spends, nothing on load; routes/keys (`suggested_edges`, `matched_records`+`skipped`, `patent_candidates`) match.
- Owner forms always post `status: "suggested"`; percent->fraction conversion bounds 0..100, the model bound is 0..1; deal event dates map to announced_at/effective_at correctly.
- Validity-axis math: open-ended `valid_to` extends to the axis end, `openStart` handled, zero-width domain guarded, min bar width, axis end is max(as-of, data); undated edges draw no bar. Note the backend already filters edges to those valid at as-of, so ended edges never reach the screen (not a UI defect).
- Parent/consolidation vs ownership: `GROUP_OF` keeps parent/ultimate_parent/subsidiary in "control" and owner/investor/investment in "ownership"; the identity panel separates the two channels.
- Peer rank ties share a rank (competition ranking) and unranked peers sort last; matrix bubble `bubbleSize` (sqrt, area ~ count) and `citationLevel` thresholds are consistent; family dedupe by `family_key` in the matrix; unattributed families excluded and counted.
- `whatChanged` bounds (`since < known_at <= until`) and its honest caveat about decisions on old rows; patents deduped.
- Concentration named/anonymous kept separate; `share_exceeds_total` and open contradictions surfaced; knock-on `source` keys are unique ("disclosure:id" / "edge:id").
- Deal stage derivation for the headline: `state` vs `confirmed_state` provisional flag matches backend `derive_state`; pro-forma ratios are fractions and formatted with `formatPct`, leverage as `x`.
- Links: all ticker hrefs use `encodeURIComponent`; path params go through openapi-fetch; `/xray?tab=ownership|insiders&ticker=` and `/financials?ticker=` match their pages' parsing; section anchors in the as-of bar all have matching ids.
- SSR/hydration: `HoldingContent` renders only after the client fetch (so `window.location`/`new Date()` initialisers are safe); `NetworkView` computes `today` in render but only an input `max` depends on it (mismatch only across UTC midnight).
- Parser guards for Decimal-as-string (`num`), jsonb-as-text (`optRecord`, `strList`), missing values stay `null`.

## Not examined
- Live behaviour with real payloads (no backend run); Next router handling of `history.replaceState(null, ...)` for `?asof=`.
- `components/financials/resource`, `PeerTable`, `PriceDiagnosticsPanel`, `useHoldingMacro` internals and the macro exposure units (`formatPct(e.weight)`), owned by other areas.
- Visual/a11y checks beyond reading semantics (contrast tokens such as `bg-heat-up-*`, `categorical-*` colours).
- Backend correctness of `status_as_of_sql`, `attributed_families` as-of filtering and the `$2::date` casts (covered by the earlier backend audit).
