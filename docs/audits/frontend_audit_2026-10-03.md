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

Every finding was re-verified by its fixer before changing code. Fixes carry regression tests (frontend now 121 test files, 1,267 tests; typecheck, lint, colour check and production build pass).

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

### Still open (genuinely not available)

- No machine-readable next-release date for macro series (only the provider's free-text schedule, shown as `release_calendar`).
- Research `known_at` stays null for sources with no stored ingestion time (13F filings, web search, thesis, strategy, report); a claim citing one shows "Known-at not recorded for N cited source(s)".
- `stale_after_hours` is null (age not judged) for providers with no refresh job or a disabled one, and it is twice the scheduled gap, not the cadence itself.
- 66 of 234 backend operations still have no structured response schema (listed in the backend audit); the frontend parses them with guards.
- Provider-payload assumptions remain unverified against live providers (see the backend audit).

### Not verified

No audit step ran the new views against a live backend with real data, in a screen reader, or with axe. Responsive findings in F7 came from code and geometry; the browser pass (`docs/data/frontend-acceptance.md`) covered error and empty states only. Focus-management fixes (focus moving to Confirm and back) are not covered by tests because the test setup has no DOM.

## Method

Each auditor read the backend route and function behind every endpoint it reviewed, compared key names, nullability, units and enums to the frontend parser, and used throwaway repro tests (deleted afterwards) to confirm findings. The contract auditor additionally ran the backend app on a migrated Postgres, captured real JSON for about 40 endpoints and fed it to the parsers. Fixers worked per area from the findings files, each fix with a test that fails on the old behaviour by construction.
