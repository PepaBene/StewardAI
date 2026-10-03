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

First pass. Harness: temporary pages mounting each view with the API intercepted (not committed).
Scenarios: (a) every API call returns HTTP 503; (b) every call returns a schema-valid empty
body generated from `apps/backend/openapi.json`. Each page at 1280px light, 1280px dark and
390px light.

- No uncaught exceptions or console errors in either scenario on any page.
- No horizontal page scroll at 390px.
- Error and empty states show the shared state vocabulary with a reason; no zeros for missing data.
- Choropleth rendered with test values: sequential ramp, hatched "no data", outlined zero,
  ranked list beside it, light/dark/390px.
- Automated: typecheck, lint (incl. colour-token check), unit tests, production build.

## Live-backend pass (2026-10-03)

Harness (nothing of it is committed): a throwaway Postgres 16 with all migrations, the backend with an
owner token, the real collectors and jobs run through the proxy against free providers (ECB, Eurostat,
FRED, ČNB, BIS calendar, SEC EDGAR XBRL and 13F, Yahoo prices, OpenFIGI, GLEIF, ESMA, Frankfurter,
iShares), five synthetic positions priced at the real closes, owner-confirmed listings and owner-assigned
country mappings; the web app in `next dev` with a local auth bypass; Chromium (Playwright) at 1280 px
and 390 px in light and dark; axe-core 4.13.

- Pages: `/`, `/macro`, `/markets`, `/financials`, `/xray`, `/coverage`, `/screens`, `/holdings/[ticker]`,
  `/holdings/[ticker]/network`, `/activity`, `/holdings`, `/status`, the palette, the chat panel.
- Console: no uncaught exceptions, no hydration warnings and no duplicate keys in repeated runs after the
  fixes. Two expected-404 lines remain (no listing derived for SPY; no research-profiles config saved): the
  pages handle both.
- Network: no failed request; the only 4xx are those two 404s.
- Layout: no horizontal page scroll at 390 px on any page after the fix (the holding page was 917 px).
- Values compared with the API and with the filings: prices, weights, price coverage, next-release dates and
  basis, AAPL FY2023-25 revenue, net income, EPS, dividends, tax rate, 13F holders, the screen's P/E.
- Choropleth: sequential ramp, hatched "no data" countries, ranked list and table agree with the exposure
  rows (United States 19.9%, China 11.3% ...); the unmapped remainder is its own row.
- Macro next release: provider-calendar rows say "as published in ... calendar" with the date; the estimate row
  says "estimate, based on observed cadence, not a published date".
- `/coverage` scorecard: identity, provenance, price coverage, return windows, reach, cost, each with its
  source endpoint; figures equal the endpoints'.
- Axe (WCAG 2.0/2.1 A and AA, best-practice), 48 combinations: first run 787 violating nodes in 48 of 48 (778
  serious or critical); after the fixes 0 in 0 of 48. Extra states scanned with zero findings: palette with
  results, chat panel, armed confirmations, all disclosures open on `/markets`, stale / quota exhausted /
  disputed rows.
- Keyboard: skip link; Enter on a trigger moves focus to Confirm (to Cancel for the destructive "Reset block"),
  Tab reaches Cancel, Cancel and a finished action return focus to the trigger, palette and chat return focus to
  their opener on Escape. 90 tab stops per page counted for a visible focus ring (33 on `/coverage` had none
  before the fix, none after; SVG map shapes show a stroke change instead of an outline).
- Automated: typecheck, lint (incl. colour-token check), 133 test files / 1,357 tests (jsdom DOM tests for focus
  management, scroll regions, meters, keys), production build.

## Not checked

- Screen readers (none available in the sandbox): roles, names and axe's semantic rules were checked, not
  spoken output.
- Hover-only states and tooltips (axe cannot scan them); data that free providers do not give (per-claim
  research known-at, 13D/G stakes, PDMR notices, N-PORT funds, hard-blocked provider rows) rendered from real data.
- Behaviour with each real provider key removed.
- A production build in a browser (the dev server was used).

## Known backend limits surfaced in the UI

See `docs/audits/frontend_audit_2026-10-03.md` for what is still open. Resolved in the third wave:
provider-calendar next-release dates (estimate only as a labelled fallback), value-weighted price
coverage, exchange/country/asset-class reach, provenance completeness, identity review share,
`stale_basis`, and `known_at` on 13F, search, thesis and strategy sources (13F is date-only and is
shown as a date). The third-wave views were checked by unit and render tests and then against a live backend with
real data (see the live-backend pass above).
