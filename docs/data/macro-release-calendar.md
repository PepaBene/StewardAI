# Macro release calendar

Tested **2026-10-03** from the agent sandbox (outbound HTTPS through the egress proxy, TLS
verification on). Purpose: the macro context returns `next_release` (a date the provider itself
published) with `next_release_basis = provider_calendar`. The observed-cadence estimate
(`next_release_estimate`, basis `observed_cadence`) remains only a labelled fallback for series
with no calendar entry. Stored in `public.macro_release_calendar` by the job
`macro_release_calendar` (`POST /jobs/macro-release-calendar`, default daily 07:20 UTC).

Rules that apply to every source below: free only (a free FRED key, or keyless); nothing paid is
called, no payment method; one calendar read per provider per job run (at most one scheduled run
per day), each request reserved against the quota ledger first; a 402 is recorded as `paid_only`
and hard-blocks the endpoint, a 429 or a refused reservation as `quota_exhausted`. A payload
that is not in the verified shape is quarantined (kept in `macro_payload_archive`, dataset
`release_calendar:quarantine`) and nothing is stored from it. Conditional requests: none of the
four servers offers a usable validator (FRED sends `Last-Modified` = now and `no-cache`; the
Eurostat and BIS answers carry no `ETag`; the ECB `ETag` is a CDN token on a page that changes
only a few times a year), so the daily cadence and the daily cap are the load control.

## Adopted (verified live, free)

| Provider | URL | Format | Auth | Quota | Update behaviour | Future dates | Mapping to our series |
|---|---|---|---|---|---|---|---|
| FRED / ALFRED (St. Louis Fed) | `GET https://api.stlouisfed.org/fred/series/release?series_id=<id>` then `GET /fred/release/dates?release_id=<id>&include_release_dates_with_no_data=true&realtime_start=<today>&realtime_end=9999-12-31&sort_order=asc&limit=200` | JSON | free API key (`FRED_API_KEY`, env only, never logged: httpx request logging is redacted) | 120 requests/minute (published by FRED); collector budget 30/minute and 60/day; a run needs 2 requests per configured series (6 by default) | changes when the source agency announces a date; read daily | yes (CPI to 2026-12-10, Employment Situation 2026-11-06 and 2026-12-04, H.15 every business day) | each configured `macro.fred[].series_id` is resolved to its release id at run time: `us_cpi` (CPIAUCSL) -> release 10 Consumer Price Index; `us_unemployment` (UNRATE) -> 50 Employment Situation; `fed_funds` (DFF) -> 18 H.15 Selected Interest Rates |
| Eurostat | `GET https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=&category=2` (the endpoint behind the "Subscribe to iCalendar" button of the release calendar page; `category=2` = euro indicators) | iCalendar (RFC 5545), all-day events, 686 KB for all categories, ~93 KB for category 2 | none | none published; collector cap 6/day | updated "every time Eurostat updates the release calendar" (the page's own text) | yes (309 euro-indicator events from 2025-01 to 2026-12) | the file has titles but no dataset codes, so a title table is used: `hicp` <- "Inflation (HICP)"; `unemployment` <- "Unemployment"; `industrial_production` <- "Industrial production"; `gdp` <- "Flash estimate GDP and employment - EU and euro area" and "GDP main aggregates and employment". Observation ids `hicp:CZ`, `hicp:DE`, `hicp:EA21` share the dataset's entry. `sentiment` (ei_bssi_m_r2) is a DG ECFIN survey, not in Eurostat's calendar: no entry |
| ECB | `GET https://www.ecb.europa.eu/press/calendars/statscal/mfm/html/<page>.en.html` with `<page>` = `stprmp`, `stprmf`, `stpbls` | structured HTML: `<dt>dd/mm/yyyy hh:mm</dt><dd>... (Dataset: BSI)<br>Reference period: Aug-2026</dd>`; no JSON/ICS/CSV exists (those paths returned 404) | none | none published; collector cap 15/day (3 pages per run) | pages are edited when the ECB publishes its calendar | yes (e.g. Monetary developments 2026-10-27, 2026-11-26, 2026-12-30, 2027-01-29) | page `stprmp` (Dataset BSI): `euro_area_m3_growth`, `euro_area_private_credit`; `stprmf` (Dataset MIR): the four `euro_area_*_loan_rate` / `ea_*_deposit_rate` series; `stpbls` (Dataset BLS): `ea_bls_enterprise_credit_standards`. A page must declare the expected dataset on every entry, otherwise it is drift. The time is stored exactly as published (the page names no time zone). Not mapped: `ecb_deposit_rate` (FM; changes at Governing Council decisions, a different calendar) and `euro_area_10y_yield` (YC; daily, no calendar) |
| BIS | `GET https://data.bis.org/api/v0/releasecalendar` (the JSON the site's release-calendar page loads; not in the SDMX API documentation, so it is the one source here without a documented contract) | JSON array of `{category_id, category_name, release_type, time_period, release_date "YYYY/MM/DD", commentary}`, 841 rows | none | none published; collector cap 6/day | edited by BIS | yes (to 2027-01-29) | `total_credit` <- `TOTAL_CREDIT`; `debt_service` <- `DSR`; `banking_claims` <- `LBS`; `debt_securities` <- `DSS`; `global_liquidity` <- `GLI`; only `release_type = "Release"` rows (revision rows excluded). Not mapped: `property_prices` (configured on dataflow `WS_LONG_CPI` but labelled "Residential property price index": the dataflow and the label disagree, so no category is claimed) |

Evidence that each works without payment (real responses, 2026-10-03; recorded, trimmed, in
`apps/backend/tests/fixtures/release_calendar/`):

- FRED: `series/release?series_id=CPIAUCSL` -> `200 {"releases":[{"id":10,"name":"Consumer Price Index",...}]}`;
  `release/dates?release_id=10&...` -> `200` with 2026-10-14, 2026-11-10, 2026-12-10 (`count` 12 from
  2026-01-01); release 50 -> 2026-11-06, 2026-12-04; release 18 -> daily from 2026-10-05.
- Eurostat: `200 text/plain`, `BEGIN:VCALENDAR PRODID:-//Eurostat//Release calendar//EN`; events
  such as `DTSTART;VALUE=DATE:20261016 SUMMARY:Inflation (HICP)`, `20261030 Unemployment`.
- ECB: `200 text/html`; `25/09/2026 10:00 ... Monetary developments in the euro area (Dataset: BSI) Reference period: Aug-2026`,
  next `27/10/2026 10:00 ... Sep-2026`.
- BIS: `200 application/json`; `{"category_id":"TOTAL_CREDIT","release_type":"Release","time_period":"2026-Q2","release_date":"2026/12/07"}`.

## Found but not adopted

| Provider | What was found | Why not adopted |
|---|---|---|
| ČNB | Official "Schedule of CNB data publishing" `https://www.cnb.cz/export/sites/cnb/cs/.galleries/kalendar/harmonogram.xls` (`200 application/vnd.ms-excel`, 1.1 MB, one sheet per year incl. `2027_q1_en`, last saved 2026-09-10; columns publishing date / item / reference date). The HTML calendar `https://www.cnb.cz/en/cnb-news/calendar/` loads its list by script and shows no entries in the static page. | It maps to only one of our series (`cnb_repo_2w` <- "Bank Board meeting", a decision date, not a data release; `czeonia` is daily with no calendar) and reading it needs a binary-XLS dependency (`xlrd`) that the project does not have. Keyless and free, so it can be added later as one more adapter; until then `cnb_repo_2w` keeps the observed-cadence estimate. |
| UN Comtrade | `GET https://comtradeapi.un.org/public/v1/getComtradeReleases` -> `200`, 141 rows, keyless | Only releases that already happened (`dateRelease` 2026-09-27 to 2026-10-03): no future dates, so it is not a calendar. Trade data is not a macro series with a `next_release` anyway. |

## No free machine-readable calendar found

| Provider | Result |
|---|---|
| OECD | no machine-readable release calendar found. The OECD calendar pages live on `www.oecd.org`, which answered `403` to every request from this environment (data, insights and about pages); the SDMX API (`sdmx.oecd.org/public/rest/dataflow/all`, `200`) lists dataflows but no release dates. Not invented; OECD series keep the observed-cadence fallback. |
| World Bank | no machine-readable release calendar found. `api.worldbank.org/v2/sources` (`200`) gives each source's `lastupdated` after the fact, and the data catalog page is HTML without a schedule. WDI is revised in periodic dataset updates (see `WORLD_BANK_CALENDAR`); nothing is invented. |
| UN Comtrade | see above: only past releases. |

## Behaviour

- Rows are append-only. A rescheduled release is a **new** row; the old row stays and gets
  `superseded_at` (the only column that may change; deletes are refused by a trigger). A date
  that merely lies in the past is never superseded. A superseded date that is published again
  becomes a new live row.
- `known_at` = the retrieval time of the run that first saw the date. The macro context at
  `as_of` uses rows with `known_at <= as_of` that were not superseded by `as_of`, and the
  earliest `release_date >= as_of`'s UTC date.
- Coverage: one `coverage_status` row per calendar (`provider = macro_release_calendar`,
  `dataset = fred|eurostat|ecb|bis`, `entity_key = calendar`; `available_periods` = upcoming
  dates, `earliest_period`/`latest_period` = first and last upcoming date). Its stale threshold
  comes from `COVERAGE_DEFAULT_REFRESH_HOURS["macro_release_calendar"]` = 24 h (basis: the job
  runs daily and providers change calendars at most daily), so a row older than 48 h is stale.
