# Free data sources: frontend acceptance record (Phase 8, frontend)

Scope: the frontend views built for phases 0-7 of
`docs/plans/free_data_sources_implementation_plan.md`. Every figure on these screens is read
from the backend API; there are no fixtures or sample figures in `apps/web/src`.

## Screens and the endpoints they depend on

| Screen | Route | Main endpoints | Degrades to |
| --- | --- | --- | --- |
| Source coverage and request budget | Activity `#source-coverage` | `/data/coverage`, `/sources/access`, `/sources/usage`, `/sources/status`, `/verification/zero-cost-gate` | Empty ledger = "unknown, not covered"; endpoint without a cap shows "No published cap" |
| Decision trail | Activity `#decision-trail` | `/alerts/free-source-trail` | Empty list; below-threshold outcomes are not stored and say so |
| Macro and trade, country choropleth | `/macro` | `/portfolio/data-context`, `/macro/context/{ticker}` (next release: calendar date, or a date per the provider's published schedule rule, each with source link, time and IANA zone, an assumed zone marked "assumed from the bank's location"; labelled estimate; or unknown; an unrecognised basis shows as unknown), `/macro/exposures`, `/macro/trade/...`, `/research-context/*` | "Not covered" per panel; country with no mapping is hatched, a real zero is outlined |
| Price coverage | `/markets` | `/holdings/{t}/prices`, `.../prices/diagnostics`, `.../corporate-actions`, `.../returns`, `/exchange-sessions/*`, `/calendar/exchanges` | Correlation matrix, raw bars and shared-observation counts are shown as unavailable (no endpoint) |
| Financials | `/financials` | `.../financial-history`, `.../segments`, `.../forward-data/consensus`, `.../actual-vs-consensus`, `.../peer-set`, `.../peer-metrics` | Cash walk and debt ladder draw only when they reconcile to filed totals; peers show the mean only (API returns no per-peer values) |
| Business network and innovation | `/holdings/{t}/network` | `.../business-network`, `.../deals`, `.../patent-*`, `/company-graph/*`, `/relationship-review/*` | Wrong-shaped body = "Temporarily unavailable", never an empty graph |
| Funds and ownership | `/xray` | `/funds/*`, `.../ownership`, `.../control-stakes`, `.../insider-activity`, `.../pdmr-notifications` | Cash, unlooked-through funds and unknown weight stay separate; channels are never summed |
| Coverage atlas | `/coverage` | `/data/coverage(/summary)` (incl. `stale_basis`; ledger rows of `macro_release_calendar` per fred/eurostat/ecb/bis), `/portfolio/price-coverage?max_stale_sessions=3`, `/portfolio/identity-review`, `/data/provenance-completeness`, `/data/reach`, `/portfolio/data-context`, `/verification/*` | A figure the backend could not give (no positions, empty table, failed request) is "unavailable", never zero |
| Screens | `/screens` | `/portfolio/holdings`, `/watchlist`, `.../valuation`, `/data/coverage` | Missing metric is excluded or shown as unknown, never pass or zero |
| Holding page sections | `/holdings/{t}` anchors `#data-context`... | `.../data-context`, `.../evidence-summary`, `/macro/context/{t}` | Gaps listed explicitly |
| Overview panels | `/` | `/portfolio/data-context`, `/macro/coverage/portfolio` | Zero denominator shows "n/a" |

A disabled or missing provider key surfaces as the backend's coverage state (`access_unverified`,
`paid_only`, `quota_exhausted`, `not_covered`); the views render that state and keep the last
verified value. Owner-triggered refreshes (Diffbot, PDL, EPO OPS, Alpha Vantage, Finnhub
probes, jobs) always require an explicit confirm step and never run on page load.

## What was checked

Harness: temporary pages mounting each view with the API intercepted (not committed).
Scenarios: (a) every API call returns HTTP 503; (b) every call returns a schema-valid empty
body generated from `apps/backend/openapi.json`. Each page at 1280px light, 1280px dark and
390px light.

- No uncaught exceptions or console errors in either scenario on any page.
- No horizontal page scroll at 390px.
- Error and empty states show the shared state vocabulary with a reason; no zeros for missing data.
- Choropleth rendered with test values: sequential ramp, hatched "no data", outlined zero,
  ranked list beside it, light/dark/390px.
- Automated: typecheck, lint (incl. colour-token check), unit tests, production build.

## Not checked (needs a live backend with data)

- Dense, populated screens against real data (chart legibility at real volumes, label collisions).
- Manual keyboard and screen-reader walkthroughs (semantic tables, roles and labels are in place and unit-smoke-tested).
- Chart aggregates against API totals.
- Behaviour with each real provider key removed.

## Known backend limits surfaced in the UI

See `docs/audits/frontend_audit_2026-10-03.md` for what is still open. Resolved in the third wave:
provider-calendar next-release dates (estimate only as a labelled fallback), value-weighted price
coverage, exchange/country/asset-class reach, provenance completeness, identity review share,
`stale_basis`, and `known_at` on 13F, search, thesis and strategy sources (13F is date-only and is
shown as a date). The third-wave views were checked by unit and render tests; not against a live
backend with data.
