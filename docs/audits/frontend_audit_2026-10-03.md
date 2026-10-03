# Free data sources frontend: audit and fix pass (apps/web, phases 0-8)

Audited at `bd5f1fd` on branch `claude/sharp-davinci-m2n5z8`. Eight independent read-only auditors reviewed one area each, in the same format and status keys as the backend audit (`free_data_sources_audit_2026-10-01.md`): **CBE** confirmed by execution, **CBR** confirmed by reading code (and the backend code behind the endpoint), **SUSP** suspected. The full findings, with failure scenarios and evidence, are in `docs/audits/frontend/`.

| Area | File | Findings |
| --- | --- | --- |
| F1 sources, activity, overview, coverage atlas, screens | `frontend/F1-sources-activity-overview-screens.md` | 18 |
| F2 macro and choropleth | `frontend/F2-macro-choropleth.md` | 10 |
| F3 price coverage | `frontend/F3-markets.md` | 9 |
| F4 financials | `frontend/F4-financials.md` | 15 |
| F5 business network and holding page | `frontend/F5-network-holding-page.md` | 18 |
| F6 funds and ownership | `frontend/F6-funds-ownership.md` | 15 |
| F7 security, accessibility, responsive (cross-cutting) | `frontend/F7-security-accessibility.md` | 10 |
| F8 contract conformance against the real backend | `frontend/F8-contract-conformance.md` | 7 |

Several findings overlap across auditors (the 100x day-change, surprise sign and 13F percent bugs were found twice).

## What the audit found, in short

- **Highs (each fixed):** day change shown 100x too small (F3-1/F8); earnings surprise sign flipped for a negative consensus (F4-01); estimates chart mixing annual and quarterly actuals (F4-02); 13F share shown 100x too small (F5-01); a zero-cost gate that read "passed" with no endpoint checked (F1-01); one holding's or date's data shown under another's label on macro, xray and holding pages (F2-1, F6-01).
- **Systemic cause fixed once:** `useApiResource` kept the previous query's data when its fetcher changed (stale data under a new holding, as-of date or filter, six findings). It now returns to loading when the fetcher changes and keeps last-good data only for the same query. A shared `errorMessage` helper fixes `[object Object]` for FastAPI 422 arrays.
- **Security:** no injection, secret exposure or unencoded fetch path in the changed files; every write/spend call site was listed and gated except two one-click actions (macro collect, relationship-review filing read), both now arm-then-confirm.
- **Accessibility:** two token pairings (`text-text-faint`, light-theme `text-warning-signal`) failed contrast for message text; replaced in the new views (tokens unchanged). Choropleth tooltip overflow at 390px, treemap tab stops, Segmented focus ring, lost focus after confirm: fixed.
- **Contract:** with real backend JSON (about 40 endpoints, 43 parser entry points) no parser mis-keyed or failed; `openapi.json` matches the live app and `schema.d.ts` matches `openapi.json`. The defects were semantic (units, sign, labelling).

## Resolution

Every finding was re-verified by its fixer before changing code. Fixes carry regression tests (frontend now 133 test files, 1,357 tests after the live pass; typecheck, lint, colour check and production build pass).

Fixed: all verified findings in F1, F2 (except F2-4 mitigation only), F3, F4 (F4-12 partly), F5 (F5-04/06 mitigated), F6 (F6-02/10/14 mitigated), F7 and F8 (F8-7 resolved with a 24-month staleness rule). Also fixed in the final pass: correlation heatmap row headers cut long tickers.

### Needs a backend change: resolved in the second pass (2026-10-03)

The items below were backend gaps that the frontend could only label honestly. A second backend pass (commit `401c1fb`, migrations `20261006130000` and `20261006140000`, each with a rollback, plus `supabase/manual/ALL_20261006130000_to_20261006140000.sql` for the live project) added the data, and the frontend adopted it and removed the workarounds. Each backend change has a regression test.

| Item | Backend change | Frontend adoption |
| --- | --- | --- |
| F2-4 trade metrics were reporter-wide | `/macro/trade` scoped to the HS product; `reporter_wide` block; `partners[]` with partner rows, unmapped remainder and a per-period HHI series | Product-scoped tiles, a collapsed reporter-wide block, partner Sankey with an explicit unmapped target, concentration trend |
| No vintage history | `history` on `/macro/context` (first release, latest, revised_at, vintage_unavailable); `access_unverified` for unprobed providers; duplicate-proof `provider_access` join | First-release/latest toggle with revision markers on a shared time axis |
| F1-14 summary ignored entity kind | `entity_kind` on `/data/coverage/summary` | Passed through |
| F1-16 staleness could not be derived | `stale` / `stale_after_hours` on coverage rows (from job cadence, grace x2; null = not judged) | Rows read Stale from the API |
| F5-04 concentration | Per-side missing counts, reported sums (null = none reported), at-least flags | Exact per-side labels |
| F5-06 forward citations 0 vs unknown | `citations_forward` nullable; EPO OPS zeros backfilled to NULL | Matrix shows "citation data for N of M families" |
| F5-18 per-claim known-at | `Source.known_at`, `CitedSentence.known_at_min/max` | "Known to the app: min to max", separate from source dates |
| F6-02 issuer split across buckets | `exposures[].issuer_buckets` | Treemap leaves per bucket |
| F6-10 manual ledger override | `DELETE /funds/coverage-ledger` restores the derived row | Arm-then-confirm "Remove manual override" |
| F6-14 13F previous period | `previous_period_end`, filing dates and lags | Real change span and lag |
| N-PORT truncation | `matching_positions`, `truncated` (not named `total`: a channel guard forbids total keys) | "Showing N of M" note |
| F6-05 / F6-06 PDMR | `ticker_status`; case-insensitive ticker lookups with expression indexes | Per-holding state, no case workaround |
| Per-peer values / duplicate peers | `peers[]` per metric, distinct-peer counts | Ranked per-peer bars, unranked null peers with reasons |
| F4-12 / F4-10 | `reconciliation_state`; `vintage_date` | Distinct unchecked / failed labels; slope day math from the date |
| Correlation matrix, raw bars, quote unit | `GET /portfolio/correlation` (matrix, counts, notes); `raw_bars` and `quote_currency` on prices; `quote_currency(_basis)` on holdings prices | One request, truthful Raw/Adjusted switch, units on Last close |
| F3-7 evidence id 500 | Validated: 422 | Digits-only check plus `errorMessage` |

### Resolved in the third backend wave (commit `b72e06b`)

| Was open | Now |
| --- | --- |
| No machine-readable next-release date for macro series | `next_release` (provider calendar: FRED, Eurostat, ECB, BIS), `next_release_time`, `next_release_source`, `next_release_basis`. The macro card shows the published date with its time (as the provider states it, no zone conversion) and a link to the calendar; when only `next_release_estimate` exists it reads "estimate, based on observed cadence, not a published date"; with neither it reads "unknown". A table view lists every series. The free-text `release_calendar` row is kept. |
| Scorecard approximations (held-position count for price coverage, `/reference/gaps` for identity, no provenance or reach figure) | `/coverage` reads `GET /portfolio/price-coverage?max_stale_sessions=3`, `/portfolio/identity-review`, `/data/provenance-completeness` and `/data/reach`. Price coverage is value-weighted with the unknown bucket; a missing figure (no positions, empty table, failed request) is "unavailable", never zero. Holding states go through `toDataState`. The Activity price and identity rings use the same endpoints; the old client-side counts (`useHeldPriceCount`, `useIdentityGaps`) are removed. |
| Stale threshold wording was fixed text | The ledger reason and the "stale after" line use the backend's `stale_basis`: "twice the scheduled refresh gap" for `job_schedule`, "twice the documented default refresh interval" for `documented_default`, no explanation when the basis is null. Providers with no job and no documented default stay "age not judged". |
| Research `known_at` null for 13F, search, thesis, strategy | Sources now carry `known_at`. Source cards in research reports and chat show "known <date>"; a 13F filing date is date-only and is shown as a date flagged "(date only)", never with an invented time. The report header no longer says the report stores no per-claim known-at: each claim shows the backend's `known_at_min`/`known_at_max` and the source data dates separately. A source still without `known_at` says "known-at not recorded". |
| Untyped responses | `GET /verification/coverage` is read through the typed client (the hand-written guard is removed). |

### Still open

- Guards are kept where the wire is quirky or the guard checks meaning, not shape: factsheet `charges` (a JSON string), identity-decision `score` (a decimal string), the business-network, financial-history and markets parsers (legacy zeros read as unknown, derived fields), and the zero-cost-gate parser (it rejects a "passed" gate that lists violations). These stay hand-written on purpose.
- `stale_after_hours` is null (age not judged) for providers with no refresh job and no documented default, and it is twice a refresh gap, not the cadence itself.
- `next_release` is a date, not a promise: the provider may reschedule. The ECB calendar states no time zone; none is shown.
- The release-calendar collector needs the `macro_release_calendar` job to have run; until then every series reads "unknown" or an estimate, and the `macro_release_calendar` rows in the coverage ledger show the per-provider state.
- Provider-payload assumptions are verified by the backend audit and, for the views, by the live pass below (the frontend parsers read real JSON from about 40 endpoints without a failure).

### Live-backend browser pass (2026-10-03, branch `claude/sharp-davinci-m2n5z8`)

The views were run for the first time against a real backend with real data, in a real browser, with axe and with DOM tests. Setup: a throwaway UTF-8 Postgres 16 with every repo migration applied; the backend (`uvicorn api.main:app`) with an HS256 owner token; the real jobs run through the proxy against free providers (`reference_data`, `price_history` via Yahoo because there is no Stooq key, `returns_refresh`, `macro_collect`, `macro_release_calendar`, `holiday_refresh`, `sec_xbrl`, `etf_look_through`, `exchange_probes`, `source_audit`, `fund_probes`, `zero_cost_gate`, `ownership_13f`, `control_stakes`, `nport_holdings`, `pdmr_uk`, `ir_releases`, `public_data_collect`); five synthetic positions (AAPL, MSFT, SPY, ASML, CEZ, real tickers and ISINs, quantities invented, priced at the real closes), owner-confirmed listings and owner-assigned country mappings. Next dev server with a local, uncommitted auth bypass; Chromium driven by Playwright; axe-core 4.13 (WCAG 2.0/2.1 A and AA plus best-practice) in light and dark, at 1280 px and 390 px. Real figures read: 3,515 price bars, 15,709 macro observations, 259 release-calendar rows, 1,560 statement facts, 57,785 13F rows, 224 graph edges.

Pages: `/`, `/macro` (every holding), `/markets`, `/financials` (SPY and AAPL), `/xray` (all tabs), `/coverage`, `/screens` (with criteria), `/holdings/[ticker]`, `/holdings/[ticker]/network`, `/activity`, `/holdings`, `/status`, the command palette, the chat panel, every arm-then-confirm control reachable on those pages. Values were compared with the API JSON (AAPL revenue, EPS and tax rate, prices, weights, price coverage, next-release dates and basis, P/E in the screen) and with what the company filed.

**Defects found in the frontend, all fixed, each with a regression test that fails on the old behaviour**

| # | Defect (seen live) | Fix |
| --- | --- | --- |
| L1 | `/macro` logged "two children with the same key (SPY_US_EQ)": `ContextCards` and `TradePanel` were siblings keyed by the bare ticker | Prefixed keys (`macro-view.test.tsx`) |
| L2 | `/holdings/[t]/network` logged duplicate keys: ESMA publishes several records for one venue and date (OCXE has four, two identical), the list keyed on mic/date/known-at | Position in the key, and a comment saying why (`identity-panel.test.tsx`) |
| L3 | Holding page was 917 px wide at 390 px: a long unbroken source label in the evidence ledger | `wrap-anywhere` on the row (`evidence-ledger.test.tsx`) |
| L4 | Overview drew a contribution chart of zeros ("CZK 1 ... CZK 0") when only one snapshot exists: the API returns 0.0 for every holding with `gap: null` | "Only one snapshot exists, so there is no period to attribute yet" (`contribution-card.test.tsx`) |
| L5 | "-0 EUR" for the price-coverage unknown bucket: the API sends -8.9e-14 | `formatMoney` rounds a residual to zero (`format.test.ts`) |
| L6 | AAPL effective tax rate 0.156 / 0.241 / 0.147 shown as 0.2 / 0.2 / 0.1: compact notation for unit `pure` | Three decimals for `pure` (`financials/format.test.ts`) |
| L7 | Keyboard focus was dropped to `<body>` after "Reset block" succeeded | The result line takes focus (`focus-management.test.tsx`) |
| L8 | Controls with `outline-hidden focus-visible:outline-2` (Segmented tabs, atlas matrix cells, table sort buttons) drew **no focus ring**: Tailwind v4's `outline-hidden` sets the outline style to none, so the 2 px outline had style none. This also means the earlier "Segmented focus ring" fix did not work in a real browser | Unlayered rule restoring `outline-style: solid` for those controls (`globals.test.ts`); 33 of 90 tab stops on `/coverage` had no ring, 0 after |
| L9 | Scrollable table wrappers were not keyboard-reachable (axe `scrollable-region-focusable`, 44 nodes) | `ScrollRegionFocus` gives `tabindex="0"` to exactly the wrappers that overflow now, after hydration has settled (`scroll-region-focus.test.tsx`) |
| L10 | `role="meter"` without `aria-valuenow` when there is no value (axe critical, `HBar`, `Ring`, `Gauge`) | A labelled `img` with no value attributes when the value is unknown (`meter-a11y.test.tsx`) |
| L11 | `dl > div > p` in the fund-view facts (axe serious); empty `th` in the screen-reader tables of charts; Activity coverage rings started at `h3` (heading order) | Note inside a `dd`; "Item"/"Value" for a blank header; `h2` |

**Axe, before and after (48 page x theme x viewport combinations)**

| | Combinations with a violation | Violating nodes | Serious or critical nodes |
| --- | --- | --- | --- |
| First run, unchanged code | 48 of 48 | 787 | 778 (679 colour contrast, 48 link-in-text-block, 44 scrollable-region-focusable, 4 definition-list, 3 aria-required-attr critical) |
| After the fixes (final run, same 48 combinations, plus the holding page re-run after the last landmark fix) | 0 of 48 | 0 | 0 |

Contrast fixes are tokens only (`src/app/tokens.css`; `npm run lint:colors` passes): light `--accent` #2f6bff to #2c66f5 (white on accent was 4.49:1) and dark #3d78ff to #2f66e8 (3.94:1), `--gain` light #0f8a63 to #0a7653 (3.94:1 on the page), `--text-faint` both themes, `--accent-strong` dark (hover state, 3.16:1), `--heat-up-strong`, and light `--loss`, `--warning-signal`, `--unconfirmed`, `--corroborated`, `--neutral-signal` which computed under 4.5:1 on the muted surface. A new `--warning-signal-foreground` for text on the amber badge (white on the dark amber was 2.16:1). Text links use `text-accent-ink` instead of the raw accent, are underlined in running text, and on-accent text no longer uses opacity.

**Focus management**: verified with the keyboard in the browser (Enter on the trigger moves focus to Confirm, Tab reaches Cancel, Cancel returns to the trigger, Confirm returns to the trigger and the result is announced) through to the result for the macro collect job and the price-history refresh (both free, queued in the throwaway queue), armed and cancelled only (no quota is spent) for the network provider refreshes (Diffbot, PDL, EPO) and the financials probes; the xray job triggers are the same `ConfirmAction` component as the macro one. Also for the command palette (focus to the input, Escape returns to the opener) and the chat panel (focus to Close, Escape returns). Covered by DOM tests (jsdom, `@testing-library/react`, `user-event`) in `focus-management.test.tsx` and `confirm-action.test.tsx`: `ConfirmAction`, the price-history refresh, `RefreshActions`, `ProbePanel`, `HitRow`, `ResetBlock`. Suite: 133 files, 1,357 tests.

**Data states**: six of the nine states appeared on live data (verified, partial coverage, not covered, paid only, access unverified, temporarily unavailable). Stale, quota exhausted and disputed were produced by changing rows in the throwaway database (an old check time, a provider's access state, three graph edges); they render with their label, icon and reason and pass axe in both themes. Disputed rows also showed that the claim and contradiction landmarks of several disputed relationships (and, by the same code pattern, disputed stories) shared one label (axe landmark-unique); each is now named after its relationship or story. Next release rows: `provider_calendar` rows read "as published in fred release calendar; the provider may still reschedule it" with the date, the observed-cadence row reads "an estimate based on observed cadence, not a published date", and the values equal the API's `next_release` and `next_release_estimate`.

**Backend findings from the live run** (not fixed here; see the report of this pass): `/macro/context/{ticker}` returned no effects for a mapping entered today because the query's `$2` parameter is inferred as `date` (`created_at <= $2` compares with midnight); `valuation_refresh` fails with `'float' object has no attribute 'get'` for AAPL; `/portfolio/contribution` returns zero rows for a single snapshot with `gap: null`; `unknown_value_base` is a float residual (-8.9e-14); the vintage source `alfred` appears as a second card for the same series with a conflicting next-release date; ESMA listing history has exact duplicate rows; `control_stakes` could not parse 6 of 6 13D/G candidates; `public_data_collect` skips World Bank, OECD and BIS although the probe says `available`.

### Not verified

- No screen reader (NVDA, VoiceOver, JAWS) was run: none is available in the sandbox. Roles, names and the semantics axe checks were verified; the spoken output was not.
- Axe checks the page states listed above; hover and focus-only states, and tooltips, are not scanned. Colour pairings in states that need data that does not exist for the free providers (research reports with per-claim known-at, 13D/G stakes, PDMR notices, N-PORT funds, a hard-blocked provider) were not rendered from real data. Source cards with `known_at` were not reached because research reports need an Anthropic key (unit and render tests only).
- The choropleth tooltip and the map's focus indication (a stroke change) were checked by eye and by axe, not with a screen reader.
- Behaviour with each provider key removed was not run.
- Hydration was clean in repeated runs of every page; the dev server was used, not a production build in a browser.
- `/holdings/[ticker]` and the network page print one console line each for expected 404s ("no listing derived yet", "no research profiles config saved yet"): the pages handle both; the browser logs the failed request itself.

## Method

Each auditor read the backend route and function behind every endpoint it reviewed, compared key names, nullability, units and enums to the frontend parser, and used throwaway repro tests (deleted afterwards) to confirm findings. The contract auditor additionally ran the backend app on a migrated Postgres, captured real JSON for about 40 endpoints and fed it to the parsers. Fixers worked per area from the findings files, each fix with a test that fails on the old behaviour by construction.
