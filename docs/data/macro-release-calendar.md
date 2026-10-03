# Macro release calendar

Tested **2026-10-03** from the agent sandbox (outbound HTTPS through the egress proxy, TLS
verification on). Purpose: the macro context returns `next_release` (a date the provider itself
published) with `next_release_basis = provider_calendar`. The observed-cadence estimate
(`next_release_estimate`, basis `observed_cadence`) remains only a labelled fallback for series
with no calendar entry. Stored in `public.macro_release_calendar` by the job
`macro_release_calendar` (`POST /jobs/macro-release-calendar`, default daily 07:20 UTC).

Rules that apply to every source below: free only (a free FRED key, or keyless); nothing paid is
called, no payment method; one calendar read per provider per job run (at most one scheduled run
per day), each request reserved against the quota ledger first, with the collector's literal
daily cap per calendar (`CALENDAR_BUDGETS`: FRED 60, ECB 15, the others 6); a 402 is recorded as `paid_only`
and hard-blocks the endpoint, a 429 or a refused reservation as `quota_exhausted`. A payload
that is not in the verified shape is quarantined (kept in `macro_payload_archive`, dataset
`release_calendar:quarantine`) and nothing is stored from it. Conditional requests: none of the
servers offers a usable validator (FRED sends `Last-Modified` = now and `no-cache`; the
Eurostat and BIS answers carry no `ETag`; the ECB `ETag` is a CDN token on a page that changes
only a few times a year), so the daily cadence and the daily cap are the load control.

## Per-series coverage (first pass -> second pass, 2026-10-03)

`next_release` resolves from `provider_calendar` where a row says so, otherwise the labelled
`observed_cadence` estimate. Source = the calendar the date is read from; time = published time of
day (zone as stated by the provider, never converted).

| Series (source / id) | First pass | Now | Calendar source and what the date means | Time published |
|---|---|---|---|---|
| fred / `us_cpi` | provider_calendar | provider_calendar | FRED release 10 (BLS CPI); cross-checked against the BLS iCal | no (BLS: 08:30 US Eastern, not stored) |
| fred / `us_unemployment` | provider_calendar | provider_calendar | FRED release 50 (BLS Employment Situation); cross-checked against the BLS iCal | no (BLS: 08:30 US Eastern) |
| fred / `fed_funds` | provider_calendar | provider_calendar | FRED release 18 (H.15), every business day (equals business days minus the Fed holidays 12 Oct, 11 Nov, 26 Nov, 25 Dec) | no |
| eurostat / `hicp:CZ` and any non-euro geo | provider_calendar | provider_calendar | "Inflation (HICP)" (full release) | no |
| eurostat / `hicp:EA21`, `hicp:DE`, any euro-area geo | provider_calendar (full release only) | **corrected**: full release **and** "Flash estimate inflation euro area" | the flash already updates `prc_hicp_minr` for exactly the 25 euro-area areas (checked) | no |
| eurostat / `unemployment` | provider_calendar | provider_calendar | "Unemployment" | no |
| eurostat / `industrial_production` | provider_calendar | provider_calendar | "Industrial production" | no |
| eurostat / `gdp` | provider_calendar (flash + full only) | **corrected**: + "Preliminary flash estimate GDP" (t+30): next date 30 Oct, was 13 Nov | all three euro-indicator releases cite `namq_10_gdp`, for CZ, DE and EA21 alike | no |
| eurostat / `sentiment` (`ei_bssi_m_r2`) | estimate | **provider_calendar** | DG ECFIN "Publication dates 2026" PDF, business and consumer survey results (ESI): 29 Oct 2026, 27 Nov, 7 Jan 2027 | **11:00** (stated as `11h00`, zone not named; Brussels) |
| ecb / `euro_area_m3_growth`, `euro_area_private_credit` | provider_calendar | provider_calendar | statistical calendar `stprmp` (BSI) | 10:00 (ECB page, zone not named) |
| ecb / 4 MIR rate series | provider_calendar | provider_calendar | `stprmf` (MIR) | 10:00 |
| ecb / `ea_bls_enterprise_credit_standards` | provider_calendar | provider_calendar | `stpbls` (BLS) | 10:00 |
| ecb / `ecb_deposit_rate` (FM) | estimate | **provider_calendar** | Governing Council meeting calendar, Day 2 of each monetary-policy meeting "followed by press conference" = decision day (29 Oct 2026, 17 Dec, 4 Feb 2027 ...). The FM series itself changes on the effective date a few days later; the decision is the announcement. | no (page gives dates only) |
| ecb / `euro_area_10y_yield` (YC) | estimate | estimate | none: the ECB states only a rule ("updated every TARGET business day at noon (12:00 CET)", may be postponed), not dates | - |
| cnb / `cnb_repo_2w` | estimate | **provider_calendar** | ČNB "Bank Board decisions" page, "Bank Board meetings on monetary policy in 2026" (5 Nov, 17 Dec; 2027 list not yet published) | **14:30** (the page: decision "at 2:30 p.m.", Czech local time) |
| cnb / `czeonia` | estimate | estimate | daily series, no calendar | - |
| bis / `total_credit` (WS_TC) | provider_calendar | provider_calendar | category TOTAL_CREDIT | no |
| bis / `debt_service` (WS_DSR) | provider_calendar | provider_calendar | DSR | no |
| bis / `banking_claims` (WS_LBS_D_PUB) | provider_calendar | provider_calendar | LBS | no |
| bis / `global_liquidity` (WS_GLI) | provider_calendar | provider_calendar | GLI | no |
| bis / `debt_securities` (WS_DEBT_SEC2_PUB) | provider_calendar **(wrong category DSS)** | **corrected** -> IDS | WS_DEBT_SEC2_PUB is "International debt securities (BIS-compiled)", calendar category IDS; DSS is the different dataflow WS_NA_SEC_DSS "Debt securities statistics" | no |
| bis / `property_prices` (WS_LONG_CPI) | estimate | provider_calendar (see note) | mapped through the dataflow it is really collected from: WS_LONG_CPI = "Consumer prices statistics" = category CPI (29 Oct, 26 Nov, 23 Dec). **Config defect found: the series is labelled "Residential property price index" but its dataflow is consumer prices.** Not changed here; if the flow is switched to WS_SPP/WS_DPP the mapping must follow (residential category RPP) | no |
| oecd / `inflation`, `unemployment`, `productivity`, `wages`, `consumer_confidence`, `business_confidence`, `housing` (7) | estimate | estimate | no accessible calendar (see below) | - |
| world_bank / 9 WDI indicators | estimate | estimate | no forward schedule published (see below) | - |
| un_comtrade trade slices | estimate | estimate | releases list is past only | - |

Net, counting configured series keys (a Eurostat key is one series across its geos; UN Comtrade
slices are not counted): 41 keys in the catalogue (3 FRED, 5 Eurostat, 9 ECB, 2 ČNB, 6 BIS,
7 OECD, 9 World Bank). `provider_calendar`: 19 before, **23** now. Still estimated: 18
(`euro_area_10y_yield`, `czeonia`, the 7 OECD series, the 9 World Bank indicators) plus UN
Comtrade, each with the reason in the table and in the sections below.

## Adopted (verified live, free)

| Provider | URL | Format | Auth | Quota | Update behaviour | Future dates | Mapping to our series |
|---|---|---|---|---|---|---|---|
| FRED / ALFRED (St. Louis Fed) | `GET https://api.stlouisfed.org/fred/series/release?series_id=<id>` then `GET /fred/release/dates?release_id=<id>&include_release_dates_with_no_data=true&realtime_start=<today>&realtime_end=9999-12-31&sort_order=asc&limit=200` | JSON | free API key (`FRED_API_KEY`, env only, never logged: httpx request logging is redacted) | 120 requests/minute (published by FRED); collector budget 30/minute and 60/day; a run needs 2 requests per configured series (6 by default) | changes when the source agency announces a date; read daily | yes (CPI to 2026-12-10, Employment Situation 2026-11-06 and 2026-12-04, H.15 every business day) | each configured `macro.fred[].series_id` is resolved to its release id at run time: `us_cpi` (CPIAUCSL) -> release 10; `us_unemployment` (UNRATE) -> 50; `fed_funds` (DFF) -> 18 H.15 |
| Eurostat | `GET https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=&category=2` (euro indicators) | iCalendar, all-day events | none | none published; cap 6/day | updated whenever Eurostat updates the calendar | yes (to 2026-12) | by title, because the file has no dataset codes. The title -> dataset link was read from the euro-indicator release pages themselves, which list their source datasets: `hicp` <- "Inflation (HICP)" (page 2026-09-17 lists `prc_hicp_minr`) and, for the 25 euro-area geos (`hicp:<geo>`), also "Flash estimate inflation euro area" (page 2026-10-02 lists `prc_hicp_minr`); `unemployment` <- "Unemployment"; `industrial_production` <- "Industrial production"; `gdp` <- "Preliminary flash estimate GDP - EU and euro area" (page 2026-07-30 lists `namq_10_gdp`), "Flash estimate GDP and employment - EU and euro area" (2026-08-14: `namq_10_gdp`) and "GDP main aggregates and employment". Not used: "GDP main aggregates and employment - update" (20 Oct; reference period Q2/2026, a revision of already published data; not an euro-indicator release and nothing links it to `namq_10_gdp`, so it is left out rather than guessed). A row stored for the exact series id (`hicp:EA21`) wins over the dataset row (`hicp`) in the read path |
| ECB statistical calendar | `GET https://www.ecb.europa.eu/press/calendars/statscal/mfm/html/<page>.en.html`, `<page>` = `stprmp`, `stprmf`, `stpbls` | structured HTML (`<dt>dd/mm/yyyy hh:mm</dt><dd>... (Dataset: BSI)<br>Reference period: Aug-2026</dd>`) | none | none published; cap 15/day (4 requests per run incl. the meeting calendar) | pages edited when the ECB publishes its calendar | yes (e.g. 2026-10-27, 2026-11-26, 2026-12-30, 2027-01-29) | `stprmp` (BSI): `euro_area_m3_growth`, `euro_area_private_credit`; `stprmf` (MIR): the four loan/deposit-rate series; `stpbls` (BLS): `ea_bls_enterprise_credit_standards`. Every entry must declare the expected dataset or the page is drift. Time stored as published (no zone named) |
| ECB Governing Council meetings | `GET https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html` | structured HTML (`<dt>29/10/2026</dt><dd>Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 2), followed by press conference</dd>`) | none | counted in the ECB cap | edited by the ECB | yes (to 2028) | `ecb_deposit_rate` <- Day 2 of each monetary-policy meeting. A Day 2 needs a monetary-policy meeting entry the previous day, otherwise drift (the live page has one unlabelled Day 1, 2028-10-11, which the rule tolerates). Dates only: the page has no times |
| BIS | `GET https://data.bis.org/api/v0/releasecalendar` (the JSON the site's release-calendar page loads; not in the SDMX documentation; the page itself says "Data are released no later than the specified date") | JSON array of `{category_id, category_name, release_type, time_period, release_date "YYYY/MM/DD"}` | none | none published; cap 6/day | edited by BIS | yes (to 2027-01) | through the dataflow each series is collected from (`BIS_FLOW_CATEGORY`, built from the job config): WS_TC -> TOTAL_CREDIT, WS_DSR -> DSR, WS_LBS_D_PUB -> LBS, WS_DEBT_SEC2_PUB -> IDS, WS_GLI -> GLI, WS_LONG_CPI -> CPI; only `release_type = "Release"` rows (revision rows excluded). A dataflow with no known category gets no entry |
| DG ECFIN (European Commission) | `GET https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/calendar/Publication%20dates%20<year>.pdf` (this year and next; an unpublished year answers 404 or, observed, a 301 redirect to the surveys home page, both read as "not published yet") | PDF, one page, text layer read with `pypdf` (already a dependency): per month a flash consumer-confidence date/time and the business and consumer survey results date/time | none | none published; cap 6/day (2 per run) | edited yearly | yes (2026 file: 29 Oct, 27 Nov, 7 Jan 2027) | `sentiment` (`ei_bssi_m_r2`, the Economic Sentiment Indicator), rows stored under provider `eurostat`. Strict: heading, results column, exactly 12 monthly rows in calendar order, matching month in both columns, else drift |
| ČNB | `GET https://www.cnb.cz/en/monetary-policy/bank-board-decisions/` | HTML: "Bank Board meetings on monetary policy in 2026" list of `D Month` cells, plus the sentence "decision is published ... at 2:30 p.m." | none | none published; cap 6/day | edited by ČNB (2027 list not yet out) | yes (5 Nov, 17 Dec 2026) | `cnb_repo_2w` <- the meeting dates (the repo-rate decision days), time 14:30 from the page. No `xlrd` dependency needed: the HTML list was checked against the official XLS schedule (below) and agrees |

## Cross-checks of dates between independent sources (2026-10-03)

| Series | Sources compared | Result |
|---|---|---|
| `us_cpi` | FRED release 10 vs BLS iCal `https://www.bls.gov/schedule/news_release/bls.ics` | 14 Oct, 10 Nov, 10 Dec 2026 identical |
| `us_unemployment` | FRED release 50 vs BLS iCal (Employment Situation) | 6 Nov, 4 Dec 2026 identical |
| `fed_funds` | FRED H.15 daily dates vs the Fed business-day rule | 60 dates 5 Oct - 31 Dec = every weekday except the four Fed holidays; no other independent H.15 list was reachable (the Fed's release-dates page 404) |
| `hicp` | Eurostat iCal vs Eurostat `.../o/calendars/events?category=2` (XML of the same service: adds reference period and a status `Preliminary`/`Released`) vs dataset state | all 309 euro-indicator events identical; the dataset `prc_hicp_minr` carries `updated 2026-10-02T11:00+02:00` = the flash date, and exactly the 25 euro-area geos already hold 2026-09 (Czechia does not) |
| `unemployment`, `industrial_production` | iCal vs XML | identical; the XML marks future dates `Preliminary` (dates can still move) |
| `gdp` | iCal vs XML vs euro-indicator pages 2026-07-30 / 2026-08-14 | identical; both pages list `namq_10_gdp` and carry Czechia, Germany and the euro area |
| `sentiment` | ECFIN PDF vs Eurostat dataset `ei_bssi_m_r2` vs the ECFIN press-release list | PDF 29 Sep 2026 11h00 = dataset `updated 2026-09-29T11:00+02:00`; press-release list shows 28 Aug and 29 Sep releases as in the PDF |
| `euro_area_m3_growth`, `euro_area_private_credit` | ECB `stprmp` vs `stpbsx` (balance sheet of euro-area MFIs, also BSI) | the five upcoming dates are identical (25/09, 27/10, 26/11, 30/12, 29/01) |
| `ecb_deposit_rate` | GC calendar vs the ECB monetary policy page | last decision 10 Sep 2026 on the page; next 29 Oct 2026 on the calendar; Day 1/Day 2 pairs consistent across 2026-2028 |
| `cnb_repo_2w` | HTML list vs the official XLS `harmonogram.xls` (sheet `2026_en`, read once with `xlrd` in a scratch virtualenv) | all eight 2026 "Bank Board meeting on monetary policy" rows equal the HTML list (5 Feb ... 17 Dec) |
| `debt_securities` | calendar category vs the BIS dataflow names (`stats.bis.org/api/v2/structure/dataflow/BIS/*/*`) and the BIS statistics page | WS_DEBT_SEC2_PUB = "International debt securities (BIS-compiled)", a different dataset from "Debt securities statistics" (WS_NA_SEC_DSS): the first pass mapped the wrong category |

BIS dates themselves have no second machine-readable source: `www.bis.org/statistics/relcal.htm`
renders the same data client-side, and the SDMX dataflow `BIS_REL_CAL` lists as a dataflow but
returns "No Results" for data. They are the one source in the calendar without an independent check.

## Inaccuracies found in the first pass (fixed here)

1. `debt_securities` was mapped to BIS category DSS; it is collected from WS_DEBT_SEC2_PUB, i.e. IDS.
2. `gdp` skipped the preliminary flash estimate (t+30), which already updates `namq_10_gdp`: the
   next date was 13 Nov, the dataset next changes on 30 Oct.
3. `hicp` for the euro area and its member states skipped the flash estimate: after the final
   release of 16 Oct the next change is the flash of 4 Nov, not 18 Nov. (Czechia correctly stays on
   the final release.)
4. `property_prices` was left unmapped because its label and dataflow disagree; it is now mapped
   through its dataflow (CPI) and the disagreement is reported as a configuration defect.

## Found but not adopted

| Provider | What was found | Why not adopted |
|---|---|---|
| ČNB data-publishing schedule | `https://www.cnb.cz/export/sites/cnb/cs/.galleries/kalendar/harmonogram.xls` (`200`, 1.1 MB, one sheet per year, `2027_q1_en` preliminary) | the only series of ours it could date is the one the HTML page already dates; it also lists the day of the Bank Board meeting, minutes and report dates, none of which we collect. Binary XLS would need `xlrd`: unnecessary now (used only for the cross-check). The calendar page `https://www.cnb.cz/en/cnb-news/calendar/` is script-loaded |
| BLS release schedule | `https://www.bls.gov/schedule/news_release/bls.ics` (`200 text/calendar`, 313 events, `TZID=US-Eastern` times 08:30) works with a User-Agent carrying a contact address (BLS's documented policy; without one it answers `403`) | FRED already gives the same dates (cross-checked). It would add the 08:30 US Eastern time for `us_cpi` / `us_unemployment`; not adopted because the stored row's provider is the observation source (`fred`), so a second calendar for the same series would need a merge rule. A candidate follow-up if times matter |
| BEA | `https://www.bea.gov/news/schedule` (`200` HTML, `/ical` also returns HTML) | no BEA series is collected |
| UN Comtrade | `GET https://comtradeapi.un.org/public/v1/getComtradeReleases` -> `200`, 141 rows, keyless; `getDA/C/M/HS` -> `200` (27,378 availability rows) | both list releases that already happened (`dateRelease` 2026-09-27 to 2026-10-03): no future date |
| Eurostat XML events endpoint | `.../o/calendars/events?category=2` (`200 text/xml`, 150 KB, reference period and `Released`/`Preliminary` status per event) | same service and dates as the iCal (identical); the iCal stays the parsed source. The `Preliminary` status is a hint that a date may still move |

## No free machine-readable calendar found (these keep the estimate)

| Provider | Evidence |
|---|---|
| OECD | `www.oecd.org` (every path tried: `/`, `/en/data.html`, `/en/data/datasets.html`, `/en/data/insights/statistical-releases.html` and `.rss`, `/en/about/news/release-calendar.html`, `/en/search/release-calendar.html`, `/content/dam/.../release-calendar.ics`) answers `403` with a Cloudflare "Just a moment..." challenge, also with a descriptive User-Agent and contact address; this is an access control and is not circumvented. The keyless hosts that do answer give no dates: `data-explorer.oecd.org` and `stats.oecd.org` (`200`, the data-explorer SPA, its configuration lists API hosts only), `sdmx.oecd.org/public/rest/dataflow/all` (`200`, 8.9 MB: 1,500 dataflows; the only annotation types are layout/default/search ones, none with a release date), per-dataflow `...?references=none&detail=full` (`200`, same). A date inferred from the national source (BLS for US CPI) would be wrong, because the OECD republishes on its own schedule. OECD series stay on the cadence estimate |
| World Bank | `api.worldbank.org/v2/sources` (`200`) gives WDI `lastupdated 2026-07-13` after the fact; WDI is updated quarterly (release notes `release-note/jan-2026`, `feb-2026`, `apr-2026` ... exist only after publication; `sep-2026`..`dec-2026` are `404`); the data help desk article "Data Updates and Errata" (`200`) lists past updates; `datatopics.worldbank.org/.../release-schedule.html` `404`; `data.worldbank.org/about/data-release-calendar` `404`; the data catalog API answered `429`. No forward date is published |
| IMF | `dsbb.imf.org` (`200`, JS application with the same 44 KB shell for `/` and `/sdds/arc`), `data.imf.org/.../IMF-Calendar` and `www.imf.org/external/np/sta/ardc/` (`403`). The IMF advance release calendars list national agencies' dates, which cover none of the OECD / World Bank series we hold |
| ECB yield curve (`euro_area_10y_yield`) | the page states a rule ("updated every TARGET business day at noon (12:00 CET)", "may be delayed ... or postponed"), no list of dates; deriving dates from TARGET closing days would be an inference, not a provider-published date |
| ČNB `czeonia` | daily fixing, no calendar |

## Remaining estimated series and what would change that

OECD (blocked at `www.oecd.org`), World Bank (no schedule), Comtrade (no future dates), the two
daily series. If the OECD later opens a machine-readable calendar or lifts the challenge for
programmatic clients, one more adapter (+ a provider value in the check constraint) is the path;
until then these series keep `next_release_basis = observed_cadence`.

Wire note for readers of `next_release_time`: the zone is never converted. ECB: `10:00`, zone not
named by the page; ECFIN: `11:00`, zone not named (the Commission publishes in Brussels time);
ČNB: `14:30`, Czech local time as the page states the time without a zone. A null time means the
calendar gives a date only.

## Behaviour

- Rows are append-only. A rescheduled release is a **new** row; the old row stays and gets
  `superseded_at` (the only column that may change; deletes are refused by a trigger). A date
  that merely lies in the past is never superseded. A superseded date that is published again
  becomes a new live row.
- `known_at` = the retrieval time of the run that first saw the date. The macro context at
  `as_of` uses rows with `known_at <= as_of` that were not superseded by `as_of`, and the
  earliest `release_date >= as_of`'s UTC date.
- Coverage: one `coverage_status` row per calendar (`provider = macro_release_calendar`,
  `dataset = fred|eurostat|ecb|bis|ecfin|cnb`, `entity_key = calendar`; `available_periods` = upcoming
  dates, `earliest_period`/`latest_period` = first and last upcoming date). Its stale threshold
  comes from `COVERAGE_DEFAULT_REFRESH_HOURS["macro_release_calendar"]` = 24 h (basis: the job
  runs daily and providers change calendars at most daily), so a row older than 48 h is stale.
