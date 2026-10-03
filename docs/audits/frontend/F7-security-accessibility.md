# F7 - security, accessibility, responsive audit (apps/web since 0b61f78)

Scope: every non-test .ts/.tsx changed in apps/web since 0b61f78 (list in scratchpad/f7files.txt). Read-only; no repo file edited; temporary vitest probes were deleted.

## Findings

### [F7-1] Financials probe panel: armed "Confirm" survives a ticker change and spends free quota on the new ticker
Severity: medium
Status: CBR
Where: apps/web/src/app/(app)/financials/financials-view.tsx:73,83 ; apps/web/src/components/financials/probe-panel.tsx:44-58,74-86
What: The data sections are wrapped in `<div key={`${ticker}:${refresh}`}>` but `<ProbeSection ticker=... />` sits outside that div and is not keyed. `ProbePanel` keeps `pending`, `busy` and `results` in local state, so changing the holding selector while "Spend one free request?" is showing leaves the Confirm button armed, and its closure now runs `probe.run(ticker)` with the NEW ticker. The confirmation text never names the ticker (only the intro paragraph does). Results from the previous ticker ("Done. ...") also stay visible under the new ticker.
Failure scenario: Owner clicks "Run..." on Alpha Vantage income statement for AAPL (armed), switches the selector to MSFT, clicks Confirm -> POST /holdings/MSFT/financial-history/alpha-vantage/INCOME_STATEMENT, spending 1 of ~25 daily requests on a ticker they never confirmed. The plan's rule is an explicit confirm per spend.
Evidence: financials-view.tsx:73 `<div key={`${ticker}:${refresh}`}` ... :83 `{ticker ? <ProbeSection ticker={ticker} onDone=... /> : null}` (no key). probe-panel.tsx:44 `const [pending, setPending] = useState<string | null>(null);` and `run(p)` calls `probe.run(ticker)` using the prop at click time.
Suggested fix: `<ProbeSection key={ticker} ...>`; also put the ticker in the confirm line ("Spend one free request for {ticker}?").

### [F7-2] "Collect macro data" queues the macro job on a single click (no confirm), unlike every other job trigger
Severity: medium
Status: CBR
Where: apps/web/src/app/(app)/macro/macro-view.tsx:76-90,94-99 ; apps/web/src/lib/macro/api.ts:72
What: The button calls `runMacroCollect()` -> POST /jobs/macro-collect directly. All other new owner jobs (xray ConfirmAction, price-history "Yes, run job", provider refreshes, probes) arm first, then confirm. macro_collect fetches the free-tier macro/trade providers (World Bank/FRED/Comtrade etc., quota-limited per the plan) and raises macro-surprise alerts. A mis-click or double-click spends quota and can emit alerts. `disabled={starting}` stops only re-entrancy during the request, not a second click after it completes.
Failure scenario: Owner opens /macro, clicks the header button once -> job queued immediately, nothing asked.
Evidence: macro-view.tsx:96 `onClick={() => void collect()}`; collect() calls `runMacroCollect()`; backend news_routes.py:256 `Collect macro series now and raise any macro surprise.`
Suggested fix: reuse `ConfirmAction` (components/xray/confirm-action.tsx) with copy naming the providers/quota.

### [F7-3] Small-text colour tokens fail WCAG AA contrast in light theme: text-text-faint (2.66-2.89:1) and text-warning-signal (3.62-3.86:1) carry real messages
Severity: medium
Status: CBE (ratios computed from src/app/tokens.css)
Where: text-text-faint: components/data-state/state-placeholder.tsx:34 (nextStep guidance), components/coverage/atlas-matrix.tsx:26 ("No ledger entry (unknown)", 11px), components/coverage/scorecard.tsx:48, components/markets/session-strip.tsx:34-35,118 (10px "upcoming"/"before history" cells and week labels), research-report-view.tsx:435, components/xray/lookthrough-sankey.tsx column headings (11px). text-warning-signal: components/xray/exposure-section.tsx:182,187,236,283; funds-section.tsx:102,115,150,206,227,325; ownership-section.tsx:141,244,327; insider-section.tsx:189; coverage-section.tsx:128 (all 10-12px).
What: Computed WCAG ratios (python, sRGB relative luminance):
 light: text-faint #8a919c on surface #f3f4f6 = 2.89; on surface-muted #e9ebef = 2.66; on surface-raised = 3.07. warning-signal #a86f00 on surface = 3.86; on warning-signal-subtle #f7ecd2 = 3.62. loss #d4411f on surface = 4.17 (session-strip "missing bar" glyph, 10px bold). text-muted #5a6270 on surface 5.59, on surface-muted 5.15, on warning-subtle 5.24 (pass). accent-ink on surface-muted 5.56 (pass).
 dark: text-faint #6b7483 on surface = 4.10 (on surface-muted 3.70, raised 3.88); warning-signal on surface 8.91 (pass); all other pairs tested >= 5.9.
 Also non-text: the "no data" hatch line (stroke-text-faint on fill-surface-muted) = 2.66 light (<3:1).
Failure scenario: Stale/unreconciled warnings ("different dates", "superseded by", "field names unverified", "share class not reconciled") and the "No ledger entry (unknown)" / next-step lines are rendered at 10-12px in a colour below 4.5:1 (below 3:1 for faint); low-vision users cannot read exactly the caveats the design relies on. Several of those warnings also have no icon, so colour is the only emphasis.
Evidence: script scratchpad/c.py output above; usage via grep.
Suggested fix: use text-text-muted for informative text (reserve text-faint for decorative); for warning text use text-text with a warning-signal border/icon, or darken --warning-signal in light (needs about #8a5a00 for 4.5:1 on surface-muted tints); darken the hatch stroke.

### [F7-4] Choropleth tooltip is positioned with -translate-x-1/2 at the country anchor and can overflow the viewport at 390px, creating horizontal page scroll
Severity: low-medium
Status: CBR (geometry verified, scroll not rendered)
Where: apps/web/src/components/charts/choropleth-map.tsx:150-166
What: Tooltip is `absolute max-w-48 -translate-x-1/2` at `left = anchor.x / 960 * 100%`. With the map ~358px wide at 390px viewport, 31 shapes have anchors where +-96px leaves the container (computed: FJ 98.8%, NZ 92.6%, PG 90%, NC 94.7%, VU 95.5%, SB 93.7%, TL, AU 85%, JP 85%, ... and left side US/MX/GT etc.). Neither the SVG (`overflow-visible`) nor the app shell (no overflow-x clip in (app)/layout.tsx) clips it, so the page's scrollable width grows while a right-edge country is hovered/focused, and the tooltip is cut off the left edge for western anchors. On touch there is no hover at all; focus is the only trigger.
Failure scenario: Keyboard-focus or tap Fiji/New Zealand on a phone -> tooltip extends ~75px past the viewport; page can scroll sideways.
Evidence: vitest probe listing anchored shapes (n=176, W=960) - see list above.
Suggested fix: clamp the tooltip left to [halfWidth, containerWidth-halfWidth] or put `overflow-x-clip` on the map wrapper.

### [F7-5] Useful state is stale while a refetch is in flight: changing as-of / reconciled-only / window keeps the old payload on screen under the new label (no loading state)
Severity: medium
Status: CBR
Where: apps/web/src/lib/hooks/use-api-resource.ts:49-62 (shared hook) combined with financials-view.tsx:73 (key lacks asOf and reconciledOnly), components/financials/statement-explorer.tsx:55, estimates-fan.tsx:83 ; apps/web/src/app/(app)/xray/xray-view.tsx (asOf is state, sections not keyed)
What: When `fetcher` changes (asOf, reconciledOnly), `useApiResource` keeps `state.kind === "ok"` with the previous data until the new request settles; it never goes back to "loading". The label is computed from the prop immediately ("... as known on {asOf}" in StatementExplorer/EstimatesFan units), so for the duration of the request the old-vintage figures are shown under the new as-of date. On a failed request the "refresh failed, showing the last loaded data" banner appears (OK), but on a slow request there is no signal at all. The feature's whole point is point-in-time honesty.
Failure scenario: Owner sets "As known on 2024-03-01" while the latest view is displayed; for the request duration the statement table (latest vintage, restated figures) is captioned "as known on 1 Mar 2024".
Evidence: use-api-resource.ts `setState(prev => next.kind === "error" && prev.kind === "ok" ? ... : next)` only runs on settle; no reset effect on fetcher change.
Suggested fix: include asOf/reconciledOnly in the section key (`key={`${ticker}:${asOf}:${reconciledOnly}:${refresh}`}`) or add `aria-busy`/dimming while a newer request is in flight.

### [F7-6] Backend 422 `detail` arrays are rendered as "[object Object]" in several action error paths
Severity: low
Status: CBR
Where: components/financials/probe-panel.tsx:51 (`String(r.body.detail)`), components/markets/action-ledger.tsx:68 (`String(apiError.detail)`), lib/source-coverage/hooks.ts:149 (`String(r.error.detail)`), lib/hooks/use-api-resource.ts:19 (shared)
What: FastAPI validation errors return `detail` as an array of objects; `String([{...}])` is "[object Object]". lib/network/actions.ts and macro use `fieldErrors/flattenErrors` correctly, these do not. peer-set-panel.tsx:75 JSON.stringifies (raw but readable).
Failure scenario: probe with a ticker the backend path-regex rejects (422) -> "Not run. HTTP 422 · [object Object]".
Suggested fix: reuse lib/api/validation-errors.ts helpers.

### [F7-7] Focus is lost after Confirm / Arm swaps the control, and the Segmented control's focus ring is clipped
Severity: low
Status: CBR (code reading)
Where: components/xray/confirm-action.tsx:98-104 (setOpen(false) unmounts the focused Confirm button), components/financials/probe-panel.tsx:77-96, components/network/refresh-actions.tsx:53-66, components/markets/portfolio-coverage.tsx:53-66 (trigger button replaced by Confirm button, focus falls to body); components/data-state/segmented.tsx:20-36 (`overflow-x-auto` container + `outline-2` with no offset: the outline is painted outside the button box, so top/bottom are clipped by the scroll container).
Suggested fix: move focus to the Confirm button when armed and back to the trigger afterwards; use `focus-visible:-outline-offset-2` (inset) in Segmented.

### [F7-8] Nested treemap: every leaf tile is a tab stop, with no visible readout on focus
Severity: low-medium
Status: CBR
Where: components/xray/nested-treemap.tsx:125
What: `<g tabIndex={0} role="img" aria-label=...>` for each leaf (issuers across all funds: tens to hundreds) inside an outer `<svg role="img">`; there is already an exact SrTable. Focus only restyles the stroke; the full label/value is a `<title>` (mouse hover only), and small tiles draw no text (`roomy` threshold), so a sighted keyboard user sees a highlighted rectangle with no name or value. Also the choropleth makes every country with data a tab stop (up to ~176) with a 1.5px stroke focus cue on tiny polygons (choropleth-map.tsx:129-136).
Suggested fix: make tiles non-focusable and rely on the table, or add a focus-driven readout (like the choropleth tooltip) and a roving tabindex; widen the focus stroke.

### [F7-9] State pattern fills can be missing or drawn in the wrong colour; summary-strip segments can be invisible
Severity: low
Status: SUSP (needs a browser render to confirm)
Where: components/coverage/state-glyph.tsx:48-76, components/coverage/atlas-matrix.tsx:68, components/coverage/summary-strip.tsx:18-35
What: (1) `PatternDefs` (the `<pattern id="atlas-pat-*">` definitions) is rendered only inside `AtlasMatrix`. `SummaryStrip` and the `GlyphLegend` swatches reference `url(#atlas-pat-...)` and depend on the atlas matrix having rendered (ledger resource loaded). If the ledger request fails while the summary succeeds, the summary bars render without patterns (icon + tone remain). (2) Pattern strokes use `currentColor`, which resolves in the context of the `<pattern>` (hidden defs svg), not the referencing `text-text-faint` svg, so the hatch is drawn in the page text colour at 60% opacity directly behind the 12px state label in atlas cells (the count line has a bg, the label does not), which may reduce label legibility. (3) In `Bar`, a state with 1 of 60 entries is about 2px wide with `overflow-hidden`, so its icon is clipped: that segment is colour-only (aria-label still lists it).
Confirm by: render atlas with the ledger request failing; screenshot a "paid_only" (cross-hatch) cell.

### [F7-10] Source links are not scheme-checked in most new components (defence in depth); only some use the http(s) guard
Severity: low
Status: CBR (no exploitable path found: backend gives https-only/SEC URLs)
Where: unguarded `<a href={api string}>`: components/financials/ir-panel.tsx:10,42,72; components/macro/context-cards.tsx:51; components/macro/indicator-card.tsx:96 (also renders an anchor with no href if sourceUrl is null); components/markets/action-ledger.tsx:98; components/xray/coverage-section.tsx:116; insider-section.tsx:193,274; ownership-section.tsx:314; components/data-state/provenance-footer.tsx:25. Guarded: network/badges.tsx SourceLink, holding/shared.tsx SourceLink, decision-trail.ts (`^https?://`), holding-view via isExternalUrl.
What: A `javascript:`/`data:` value in any of those API fields would render as a clickable link. All carry `rel="noreferrer noopener"` and `target="_blank"`. The IR source URL is validated `^https://` by the backend (free_sources_routes.py IssuerIRSourceInput) and SEC/provider URLs are server-generated, so this is not exploitable today; coverage-ledger `source_url` is owner-typed free text and unvalidated in the UI (coverage-section.tsx:229 `source.trim() || null`).
Suggested fix: route all of them through `isExternalUrl` from lib/sources/sources.ts (one shared SourceLink).

## Security checks that found no issue (see also "Examined and found sound")
- No dangerouslySetInnerHTML / innerHTML / eval / new Function in any changed file. No localStorage/sessionStorage use; the only JSON.parse calls (markets/parse.ts:57, network/parse.ts:82,95) parse API strings through type guards.
- Path/query parameters: all fetches go through openapi-fetch typed clients with `params.path`/`params.query`, which percent-encode path segments and serialise query values; no hand-built fetch URLs (grep `fetch(` found none). Tickers with `/`, `?` or `&` cannot escape their segment. Internal links use encodeURIComponent (ownership-summary-card.tsx:15, financial-history-card.tsx:28, network-patents-card.tsx:28, decision-trail-panel.tsx:192). URL-state readers whitelist values (xray TAB_IDS, coverage url.ts enums, ISO-date regex) or only pass them as encoded query values.
- No window.open / location assignment / router.push from API data; router.replace in coverage/use-query-state.ts uses pathname + URLSearchParams-built query.
- All write calls use getBrowserApiClient() (Supabase bearer middleware, no cookie auth, so no CSRF surface). No process.env or secret/token strings in changed client files; no console logging.

## Write/spend call-site inventory and gates
| Call | Site | Gate |
|---|---|---|
| POST /jobs/ownership-13f, nport-holdings, etf-look-through, insider-trades, control-stakes | xray/confirm-action.tsx:100-108 | two-step ConfirmAction (OK) |
| POST /funds/coverage-ledger/refresh, PUT /funds/coverage-ledger, POST /funds/snapshots/pin | xray/coverage-section.tsx:67,221,288 | ConfirmAction (OK) |
| POST /jobs/price-history | markets/portfolio-coverage.tsx:29 | arm + "Yes, run job" (OK) |
| POST .../alpha-vantage/{statement}, finnhub-research | financials/probe-panel.tsx | arm + Confirm (BUT see F7-1) |
| PUT /holdings/{t}/peer-set/{id}/status | financials/peer-set-panel.tsx:76 | select peer + reason + explicit status buttons (OK; evidence id validated integer) |
| POST company-research/diffbot, pdl, patent epo-ops refresh | network/refresh-actions.tsx | arm + Confirm (OK) |
| POST edge/event/disclosure decisions, contradiction resolve, relationship confirm/reject | lib/network/actions.ts via decision-control.tsx / review-queue.tsx | reason >= 10 chars required client side, explicit buttons (OK) |
| POST /relationship-review/hits/{id}/review ("Read passages") | review-queue.tsx:122 | single click, no confirm; fetches a filing and stores suggestions (reads only, no key quota, low risk; note) |
| POST/DELETE /macro/exposures | macro/exposure-editor.tsx | form submit; delete has Confirm (OK) |
| POST /jobs/macro-collect | macro-view.tsx:79 | NONE (F7-2) |
| POST corporate-action decision | markets/action-ledger.tsx:63 | form with reason >= 10 and evidence (OK) |
| POST reset-block | activity/source-coverage-panel.tsx via coverage-matrix.tsx:95-128 | idle -> confirm -> busy (OK) |
| PUT /config/event-rules (+validate/preview) | event-rules-editor.tsx | existing SaveBar/replay gate (OK) |
| POST /jobs/research, earnings-check, thesis-verdicts | research-report-view.tsx:586, holding-view.tsx:411 | single click; pre-existing (earnings-check diff is empty in this range), not new |

Read fan-out / loops: no setInterval/refetch loop in new views (only holding-view.tsx:107, pre-existing job polling). `useApiResource` refetches only when the fetcher identity changes or a sync completes (`lastSuccessJobId`). `useCorrelationRows` issues one GET /holdings/{t}/prices?days=365 per holding (read-only DB route, reference_routes.py:272; N requests on every sync success; no external quota) - perf only. `useValuations` is capped at 4 concurrent GETs. No GET in the changed views triggers an external provider call.

## Examined and found sound
- Table semantics: every new data table has `<caption>` (sr-only or visible), `<th scope="col">`, and row headers where the first column is an identifier (checked cash-waterfall, debt-maturity, ir-panel, peer-set, peer-table, atlas-matrix, ledger-table, global-reach, plan-gaps, summary-strip, funds-section, xray facts, ChartFrame TableView, exposure-editor, holding-macro-panel). Wide tables sit in `overflow-x-auto`/`overflow-auto` wrappers with `min-w-*`, so they scroll inside their card at 390px instead of the page (ledger-table, global-reach, results.tsx tables are in `overflow-auto` containers).
- ARIA: role="meter" (provenance-footer.tsx, scorecard.tsx) has aria-label and min/max/now; role="img" charts all have a computed aria-label and a table equivalent (ChartFrame TableView or SrTable). Choropleth countries with data are `tabindex=0 role=img aria-label`, non-data shapes `aria-hidden`; Escape clears the tooltip. Segmented uses role=group + aria-pressed. details/summary are native (keyboard operable).
- Colour-only meaning: data states use icon + label + fill pattern (state-glyph); candle up/down differ by fill vs outline; session-strip cells carry glyph + label; treemap remainder/cash groups are dashed/grey; confirmed vs suggested corporate actions are solid vs dashed. Choropleth legend lists value ranges, zero and no-data have distinct non-colour fills (outline/hatch).
- Reduced motion: globals.css:154 disables animation/transition durations globally (spinners only).
- Heading order: PanelHeading renders h2 (h3 optional) beneath the page h1; no skipped levels found in the new views; h4 only inside h3 sections (network).
- Touch targets: Button sm = h-8 (32px) and native small buttons are about 24-26px, above the WCAG 2.2 minimum (24px); inline links are exempt.
- Other contrast pairs tested pass AA: text-muted on surface/muted/raised (5.15-5.94 light, 6.86-7.59 dark), accent-ink on surface-muted 5.56, dark theme gain/loss/unconfirmed/disputed/corroborated on their subtle fills (5.9-7.3).
- Double-submit: ConfirmAction/RefreshActions/DecisionControl/peer-set disable buttons while pending; stale-response race is handled by the sequence counter in useApiResource.

## Not examined
- No browser rendering/screenshots at 390px (overflow findings are from class/geometry reading); no axe/Lighthouse run; no screen-reader testing.
- Backend correctness of data (other auditors); the pre-existing holding-view and research-report-view write buttons beyond noting they were unchanged.
- Dark-theme contrast for heat/ramp fills of the choropleth and treemap (adjacent ramp steps are intentionally close; legend labels give the ranges).
