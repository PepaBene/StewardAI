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

### Needs a backend change (frontend mitigates honestly, cannot fully fix)

- `/macro/trade/{reporter}/{hs}`: `periods`, `hs_revision`, `source_url`, `known_at` and all `metrics` are reporter-wide while only `latest_value_by_flow` is product-specific (F2-4). The UI labels the two apart.
- `/data/coverage/summary` has no `entity_kind` filter (F1-14); no per-dataset refresh interval, so `stale` cannot be derived from age (F1-16).
- Concentration payload gives one missing-share count for both sides (F5-04); forward patent citations are 0 instead of null when the source cannot supply them (F5-06).
- Per-bucket issuer weights in the fund aggregate (F6-02); no delete/revert for a manual coverage-ledger row (F6-10); no previous-period date per 13F row (F6-14); PDMR and insider routes upper-case the ticker while T212 UK tickers are stored with a lowercase suffix (F6-06); no N-PORT "more rows exist" flag.
- Reconciliation flag collapses "no check ran" and "a check failed" (F4-12); `vintage` is free text (F4-10, handled defensively).
- Backend returns HTTP 500 for an invalid corporate-action evidence id (F3-7).
- Quote unit is absent from `HoldingsPrices`, so "Last close" uses the instrument currency (F3-9).
- A quirk seen in the macro context query: providers with no `indicators`/`sdmx` access row read as `available` through `coalesce(pa.state, 'available')`, so an unprobed provider shows as verified.

### Not verified

No audit step ran the new views against a live backend with real data, in a screen reader, or with axe. Responsive findings in F7 came from code and geometry; the browser pass (`docs/data/frontend-acceptance.md`) covered error and empty states only. Focus-management fixes (focus moving to Confirm and back) are not covered by tests because the test setup has no DOM.

## Method

Each auditor read the backend route and function behind every endpoint it reviewed, compared key names, nullability, units and enums to the frontend parser, and used throwaway repro tests (deleted afterwards) to confirm findings. The contract auditor additionally ran the backend app on a migrated Postgres, captured real JSON for about 40 endpoints and fed it to the parsers. Fixers worked per area from the findings files, each fix with a test that fails on the old behaviour by construction.
