# Macro release calendar

Tested **2026-10-03** from the agent sandbox (outbound HTTPS through the egress proxy, TLS
verification on). Purpose: the macro context returns `next_release` (a date the provider itself
published) with `next_release_basis`:

* `provider_calendar`: a date in the provider's own release calendar;
* `published_rule` (third pass): a date derived from a release **rule and a holiday list that the
  provider itself publishes** (ECB yield curves: "every TARGET business day" + the ECB's T2 closing
  days; ČNB CZEONIA: "the next working day" + the ČNB bank holiday list). Not an estimate; it can
  still move (the ECB says an update "may be delayed or postponed");
* `observed_cadence`: the median-gap estimate (`next_release_estimate`), only a labelled fallback
  for series with neither.

Stored in `public.macro_release_calendar` (column `source` holds the basis) by the job
`macro_release_calendar` (`POST /jobs/macro-release-calendar`, default daily 07:20 UTC).

Rules that apply to every source below: free only (a free FRED key, or keyless); nothing paid is
called, no payment method; one calendar read per provider per job run (at most one scheduled run
per day), each request reserved against the quota ledger first, with the collector's literal
daily cap per calendar (`CALENDAR_BUDGETS`: FRED 60, ECB 15, the others 6, incl. BLS); a 402 is recorded as `paid_only`
and hard-blocks the endpoint, a 429 or a refused reservation as `quota_exhausted`. A payload
that is not in the verified shape is quarantined (kept in `macro_payload_archive`, dataset
`release_calendar:quarantine`) and nothing is stored from it. Conditional requests: none of the
servers offers a usable validator (FRED sends `Last-Modified` = now and `no-cache`; the
Eurostat and BIS answers carry no `ETag`; the ECB `ETag` is a CDN token on a page that changes
only a few times a year), so the daily cadence and the daily cap are the load control.

## Per-series coverage (before -> after the third pass, 2026-10-03)

`next_release` resolves from the stored row whose `source` is `provider_calendar` or
`published_rule`, otherwise the labelled `observed_cadence` estimate. Time = the time of day the
provider publishes, never converted; its zone is `next_release_timezone` (see "Time zones").

| Series (source / id) | Before (second pass) | Now | Calendar source and what the date means | Time (zone, how known) |
|---|---|---|---|---|
| fred / `us_cpi` | provider_calendar (date) | provider_calendar | BLS iCal (own rows, `bls:cpi`) wins over FRED release 10; the two are compared on every run (identical today: 14 Oct, 10 Nov, 10 Dec 2026) | **08:30, America/New_York, stated by the provider** (`TZID=US-Eastern`) |
| fred / `us_unemployment` | provider_calendar (date) | provider_calendar | BLS iCal Employment Situation (`bls:empsit`) wins over FRED release 50 (identical today: 6 Nov, 4 Dec) | **08:30, America/New_York, stated** |
| fred / `fed_funds` | provider_calendar | provider_calendar | FRED release 18 (H.15), every business day (the Federal Reserve's, not BLS's) | none (date only) |
| eurostat / `hicp:CZ` and any non-euro geo | provider_calendar | provider_calendar | "Inflation (HICP)" (full release) | none |
| eurostat / `hicp:EA21`, `hicp:DE`, any euro-area geo | provider_calendar | provider_calendar | full release **and** "Flash estimate inflation euro area" | none |
| eurostat / `unemployment`, `industrial_production` | provider_calendar | provider_calendar | "Unemployment", "Industrial production" | none |
| eurostat / `gdp` | provider_calendar | provider_calendar | preliminary flash + flash + full release (next 30 Oct) | none |
| eurostat / `sentiment` (`ei_bssi_m_r2`) | provider_calendar | provider_calendar | DG ECFIN "Publication dates 2026" PDF (29 Oct, 27 Nov, 7 Jan 2027) | 11:00, Europe/Brussels, **assumed from the agency's location** (the PDF says `11h00`, no zone) |
| ecb / `euro_area_m3_growth`, `euro_area_private_credit` | provider_calendar | provider_calendar | statistical calendar `stprmp` (BSI) | 10:00, Europe/Berlin, assumed (the page names no zone) |
| ecb / 4 MIR rate series | provider_calendar | provider_calendar | `stprmf` (MIR) | 10:00, Europe/Berlin, assumed |
| ecb / `ea_bls_enterprise_credit_standards` | provider_calendar | provider_calendar | `stpbls` (BLS = bank lending survey) | 10:00, Europe/Berlin, assumed |
| ecb / `ecb_deposit_rate` (FM) | provider_calendar | provider_calendar | Governing Council monetary-policy decision day (29 Oct, 17 Dec, 4 Feb 2027) | none (the page gives dates only) |
| ecb / `euro_area_10y_yield` (YC) | estimate | **published_rule** | the next TARGET business day: the ECB yield-curve page states "updated every TARGET business day at noon (12:00 CET)" and the ECB's T2 page lists the closing days (Saturday, Sunday, 1 Jan, Good Friday, Easter Monday, 1 May, 25 and 26 Dec); dates are derived for the next 14 days, every run. The page adds that an update "may be delayed ... or postponed to the following TARGET business day", so the row's title says so | **12:00, Europe/Berlin, stated by the provider** ("CET") |
| cnb / `cnb_repo_2w` | provider_calendar | provider_calendar | Bank Board meeting dates (5 Nov, 17 Dec; 2027 not yet published) | 14:30, Europe/Prague, assumed (the page says "2:30 p.m.", no zone) |
| cnb / `czeonia` | estimate | **published_rule** | every Czech working day: the ČNB's CZEONIA methodology page states "published on the immediately following working day, D+1, usually by 9.30 a.m. local time"; working days = not Saturday/Sunday and not on the ČNB's published bank holiday list (13 per year, no sliding holidays; 2025 and 2026 are published, so dates stop at 31 Dec 2026 until the 2027 list appears) | none: "usually by 9.30" is an upper bound, not a release time; stored in the title only |
| bis / `total_credit` (WS_TC) | provider_calendar | provider_calendar | category TOTAL_CREDIT | none |
| bis / `debt_service` (WS_DSR) | provider_calendar | provider_calendar | DSR | none |
| bis / `banking_claims` (WS_LBS_D_PUB) | provider_calendar | provider_calendar | LBS | none |
| bis / `global_liquidity` (WS_GLI) | provider_calendar | provider_calendar | GLI | none |
| bis / `debt_securities` (WS_DEBT_SEC2_PUB) | provider_calendar | provider_calendar | IDS | none |
| bis / `property_prices` | provider_calendar **via the wrong flow (WS_LONG_CPI, category CPI)** | provider_calendar **via WS_SPP, category RPP** | the series now holds what its label says (real residential property price index, 2010 = 100, dataflow WS_SPP "Selected residential property prices"); dated by the three "Selected residential" release types of category RPP (29 Oct partial update 2, 26 Nov standard release, 23 Dec partial update 1); the "Detailed residential release" rows belong to WS_DPP and are not used | none |
| oecd / `inflation`, `unemployment`, `productivity`, `wages`, `consumer_confidence`, `business_confidence`, `housing` (7) | estimate | estimate | no free forward date source (evidence below) | n/a |
| world_bank / 9 WDI indicators | estimate | estimate | only a list of update months (evidence below) | n/a |
| un_comtrade trade slices | estimate | estimate | releases list is past only (evidence below) | n/a |

Net, counting configured series keys (a Eurostat key is one series across its geos; UN Comtrade
slices are not counted): 41 keys in the catalogue (3 FRED, 5 Eurostat, 9 ECB, 2 ČNB, 6 BIS,
7 OECD, 9 World Bank). Dated by the provider (`provider_calendar` or `published_rule`):
23 before, **25** now (two by `published_rule`). Still estimated: 16 (the 7 OECD series, the 9
World Bank indicators) plus UN Comtrade, each with the evidence below.

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
| BLS (third pass) | `GET https://www.bls.gov/schedule/news_release/bls.ics` | iCalendar, 313 events, `DTSTART;TZID=US-Eastern:YYYYMMDDTHHMMSS` (08:30 for CPI and Employment Situation) | none, but BLS answers `403` without a `User-Agent` that carries a contact address (its documented policy): `BLS_USER_AGENT`, falling back to `SEC_USER_AGENT`; without either no request is made and the calendar is reported `access_unverified` (FRED still gives the date) | none published; cap 6/day | edited by BLS; the iCal lists about a year | yes (CPI 14 Oct, 10 Nov, 10 Dec; Employment Situation 6 Nov, 4 Dec) | FRED series `CPIAUCSL` -> "Consumer Price Index" and `UNRATE` -> "Employment Situation"; rows stored under provider `fred` (the observation source) with calendar key `bls:cpi` / `bls:empsit` and the read path prefers them over FRED's date-only rows (the agency that publishes the release is the authority). Every event must carry `TZID=US-Eastern` and the calendar must declare it, else drift |
| ECB yield-curve rule (third pass) | `GET https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_area_yield_curves/html/index.en.html` and `GET https://www.ecb.europa.eu/paym/target/t2/html/index.en.html` | HTML text | none | counted in the ECB cap (6 requests per run in all) | pages edited by the ECB | rule: every TARGET business day (derived 14 days ahead) | `euro_area_10y_yield`. Both pages must still contain the verified sentences (yield curve: "updated every TARGET business day at noon (12:00 CET)"; T2: "closed on Saturday and Sunday" and the six closing days in order), otherwise drift and nothing is stored. Good Friday and Easter Monday are computed from Easter |
| ČNB CZEONIA rule (third pass) | `GET https://www.cnb.cz/en/public/media-service/schedules-and-other-info/bank-holidays-in-the-czech-republic/` and `GET https://www.cnb.cz/en/financial-markets/money-market/czeonia-reference-interest-rate/calculation-methodology-for-the-czeonia-reference-rate/` | HTML | none | counted in the ČNB cap (3 requests per run in all) | pages edited by the ČNB | rule: every Czech working day, only inside years whose holiday list is published | `czeonia`. Strict: 13 holidays per listed year, every weekday as the page states it, the "sliding holiday" sentence present, Good Friday and Easter Monday equal to the computed ones, and the D+1 sentence on the methodology page; else drift |

## Cross-checks of dates between independent sources (2026-10-03)

| Series | Sources compared | Result |
|---|---|---|
| `us_cpi` | FRED release 10 vs BLS iCal `https://www.bls.gov/schedule/news_release/bls.ics` | 14 Oct, 10 Nov, 10 Dec 2026 identical. Since the third pass the job compares them on every run (step detail `bls_fred_date_mismatch`, a log warning) and the read path returns the BLS's own date and 08:30 time: a mismatch is reported, never silently resolved |
| `us_unemployment` | FRED release 50 vs BLS iCal (Employment Situation) | 6 Nov, 4 Dec 2026 identical (same comparison and preference) |
| `euro_area_10y_yield` | the rule "every TARGET business day" with the T2 closing days vs the dates of the ECB's own YC dataset (`YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y`, 2 Jan - 1 Oct 2026) | all 192 dates in the dataset are TARGET business days and every TARGET business day of that range is in it (no postponement or venue closure showed up in 2026). The 2025 range answered `504` (ECB gateway), so only 2026 was compared |
| `czeonia` | the rule "Czech working days" with the ČNB holiday list vs the dates of the CZEONIA fixings (`yearly.txt?calendarYear=2025` and `2026`) | 251 fixing days in 2025 (2 Jan - 31 Dec) and 189 in 2026 (2 Jan - 1 Oct) are exactly the Czech working days; none missing, none extra (the publication day is the next working day, D+1) |
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

## Inaccuracies found (first, second and third pass; all fixed)

1. `debt_securities` was mapped to BIS category DSS; it is collected from WS_DEBT_SEC2_PUB, i.e. IDS.
2. `gdp` skipped the preliminary flash estimate (t+30): the next date was 13 Nov, the dataset next
   changes on 30 Oct.
3. `hicp` for the euro area and its member states skipped the flash estimate.
4. **`property_prices` defect (third pass, see "BIS property_prices").** The series was labelled
   "Residential property price index" but collected dataflow WS_LONG_CPI (consumer prices); the
   second pass dated it from the CPI category. It now holds WS_SPP and is dated from RPP.
5. **The BIS collector never reached the data (third pass).** `data_request` was
   `<base>/data/BIS,WS_SPP,1.0/<key>` with `Accept: ...json;version=2.0.0`: live, BIS answers `406`
   to that Accept header and `500` ("Could not find structure type with class 'BIS,WS_SPP,1.0'") to
   that path. The working form is `<base>/data/dataflow/BIS/WS_SPP/1.0/<key>` with SDMX-JSON
   `version=1.0.0` (`sdmx_keys.data_request`). The OECD takes version 2.0.0 on its own path. So no
   BIS series could have been stored by the old request, and no mislabelled CPI rows can have been
   written by the live collector; the migration handles them anyway (below).
6. **Four of the six BIS and all seven OECD default keys were wrong (third pass),** see the
   dimension tables below. Two OECD flows named in the defaults do not exist
   (`DF_EARNINGS`, `DF_RHPI`) and `DF_PDB_LV` / `DF_CLI` could not be used for the series they
   were configured for. They are corrected, with the old keys kept as `KEY_CORRECTIONS` so a
   stored configuration is still repaired at request time.
7. **SDMX-JSON 1.0 dataset-level attributes were ignored (third pass).** BIS values carry
   `UNIT_MULT` (6 = millions) at dataset level under `structure.attributes.dataset` (2.0 calls it
   `dataSet`); the parser read only `dataSet` and so stored millions of USD with multiplier 1.
8. **ECB: 4 of 9 default keys were broken (third pass, live):** a key that repeats the dataset
   prefix (`BSI/BSI.M...`, `MIR/MIR.M...`, `BLS/BLS.Q...`) answers `400`; the private-credit key
   used a sector code without a series (`404`). Corrected (table below), `ECB_KEY_CORRECTIONS`.
9. **ALFRED (third pass, real FRED key):** the vintage request for `us_cpi` (`units=pc1`,
   `output_type=1`, a real-time range) is refused by FRED with HTTP 400 ("If output_type is '1' and
   units is not 'lin', then realtime_start must equal realtime_end ..."), so CPI vintages were
   never stored; and a real-time window is clipped to the requested `realtime_start`, so an
   observation first published before it carried the clip date as its release date. See "ALFRED".

## Time zones of published release times (third pass)

`next_release_timezone` (IANA name or null) and `next_release_timezone_source`
(`stated_by_provider` | `agency_location` | null) sit next to `next_release_time` in the macro
context. Rules: a zone is only attached to a release that has a time; a zone the provider itself
states always wins; otherwise the zone is that of the publishing agency's seat, which is an
**assumption** and is labelled `agency_location` so a client can show "14:30 (Europe/Prague,
assumed from the bank's location)". The mapping is one constant table
(`steward.sources.release_calendar.AGENCY_TIMEZONES`, one test per entry):

| Agency (calendar the time is read from) | Zone | Why |
|---|---|---|
| ECB (statistical calendar, yield curve) | Europe/Berlin | Frankfurt; same offsets as CET/CEST. Statistical calendar pages: no zone stated (checked `stprmp`, the meeting calendar) -> `agency_location`. Yield-curve page states "12:00 CET" -> `stated_by_provider` |
| ČNB | Europe/Prague | the Bank Board page says "2:30 p.m." with no zone -> `agency_location` |
| DG ECFIN | Europe/Brussels | the PDF says `11h00`, no zone -> `agency_location` |
| Eurostat | Europe/Luxembourg | same offsets as Brussels; no Eurostat row has a time today |
| BIS | Europe/Zurich | Basel; BIS rows have no time today |
| BLS | America/New_York | the iCal states `TZID=US-Eastern` (with its EST/EDT rules) -> `stated_by_provider` |
| FRED, BEA | America/New_York | St. Louis Fed / Commerce; FRED rows have no time (BLS rows carry the stated one) |
| OECD | Europe/Paris | no OECD time is collected today |
| World Bank, IMF | America/New_York | Washington, DC; no time collected today |
| UN Comtrade | America/New_York | New York; no time collected today |

Existing rows with a time (ECB 10:00, ECFIN 11:00, ČNB 14:30) are backfilled by migration
`20261007140000` as `agency_location`; the table's append-only trigger is lifted for that one
deterministic update only. The database refuses a zone without a time, a zone without its source
and any other source value (check constraints).

## SDMX dimension orders, verified live (2026-10-03)

Structures from `stats.bis.org/api/v2/structure/dataflow/BIS/<id>/+?references=all` and
`sdmx.oecd.org/public/rest/v1/dataflow/<agency>/<id>/<version>?references=all`; every key below
returned the intended single series (`tests/fixtures/sdmx_live`, recorded answers; the test
`test_the_pinned_key_matches_the_live_dimension_order_and_selects_one_series` checks that the
response's dimension order equals `DOCUMENTED_DSD` and that every key slot equals the response's
value of that dimension). The area slot is **not** first everywhere: BIS starts with `FREQ`, OECD
`DSD_RHPI` with `REF_AREA_TYPE`.

| Series | Flow (version) | Dimension order | Pinned key (US) | Before |
|---|---|---|---|---|
| bis `total_credit` | WS_TC 2.0 | FREQ.BORROWERS_CTY.TC_BORROWERS.TC_LENDERS.VALUATION.UNIT_TYPE.TC_ADJUST | `Q.US.P.A.M.770.A` (770 = % of GDP) | already corrected from the area-first form |
| bis `debt_service` | WS_DSR 1.0 | FREQ.BORROWERS_CTY.DSR_BORROWERS | `Q.US.P` | already corrected |
| bis `banking_claims` | WS_LBS_D_PUB 1.0 | FREQ.L_MEASURE.L_POSITION.L_INSTR.L_DENOM.L_CURR_TYPE.L_PARENT_CTY.L_REP_BANK_TYPE.L_REP_CTY.L_CP_SECTOR.L_CP_COUNTRY.L_POS_TYPE | `Q.S.C.A.TO1.A.5J.A.5A.A.US.N` (stocks of cross-border total claims of all reporting banks on US residents; `L_CP_COUNTRY` is the area) | `.Q.S.A.TO1` (5 of 12 slots; the area went into FREQ) |
| bis `debt_securities` | WS_DEBT_SEC2_PUB 1.0 | FREQ.ISSUER_RES.ISSUER_NAT.ISSUER_BUS_IMM.ISSUER_BUS_ULT.MARKET.ISSUE_TYPE.ISSUE_CUR_GROUP.ISSUE_CUR.ISSUE_OR_MAT.ISSUE_RE_MAT.ISSUE_RATE.ISSUE_RISK.ISSUE_COL.MEASURE | `Q.US.3P.1.1.C.A.A.TO1.A.A.A.A.A.I` (amounts outstanding, all issuers/currencies/maturities, issued by US residents) | `.Q.C.1` (3 of 15 slots) |
| bis `global_liquidity` | WS_GLI 1.0 | FREQ.CURR_DENOM.BORROWERS_CTY.BORROWERS_SECTOR.LENDERS_SECTOR.L_POS_TYPE.L_INSTR.UNIT_MEASURE | `Q.USD.US.P.A.A.B.USD` (USD credit, loans and debt securities, to non-financial sectors in the US) | `.Q.US` (3 of 8 slots) |
| bis `property_prices` | **WS_SPP** 1.0 | FREQ.REF_AREA.VALUE.UNIT_MEASURE | `Q.US.R.628` (VALUE R = real, UNIT_MEASURE 628 = index, 2010 = 100; 771 would be the y/y change) | flow WS_LONG_CPI (FREQ.REF_AREA.UNIT_MEASURE: consumer prices, monthly/annual only, no quarterly data) |
| oecd `inflation` | DF_PRICES_ALL (OECD.SDD.TPS) 1.0 | REF_AREA.FREQ.METHODOLOGY.MEASURE.UNIT_MEASURE.EXPENDITURE.ADJUSTMENT.TRANSFORMATION | `USA.M.N.CPI.PA._T.N.GY` (CPI, all items, y/y) | `.M.CPI.PC_CHGPP` (4 of 8 slots, a code that does not exist) |
| oecd `unemployment` | DF_IALFS_UNE_M 1.0 | REF_AREA.MEASURE.UNIT_MEASURE.TRANSFORMATION.ADJUSTMENT.SEX.AGE.ACTIVITY.FREQ | `USA.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M` | `.M.Y_GE15.PS._T._T` (FREQ is last) |
| oecd `productivity` | DF_PDB (OECD.SDD.**TPS**) 2.0 | REF_AREA.FREQ.MEASURE.ACTIVITY.UNIT_MEASURE.PRICE_BASE.TRANSFORMATION.ASSET_CODE.CONVERSION_TYPE | `USA.A.GDPHRS._T.USD_PPP_H.LR.N._Z.PPP` (GDP per hour worked, constant prices and PPPs) | agency `OECD.SDD.PDB`, flow `DF_PDB_LV` (its structure is not served: `500`) |
| oecd `wages` | DF_HOU_EAR (OECD.SDD.TPS) 1.0 | REF_AREA.MEASURE.UNIT_MEASURE.ADJUSTMENT.ACTIVITY.SECTOR.FREQ | `USA.EAR.IX.Y._Z.S1D.Q` (hourly earnings index, private sector, seasonally adjusted) | flow `DF_EARNINGS` does not exist (the earnings flows are `DSD_EARNINGS@...`, annual) |
| oecd `consumer_confidence` | DF_CS (OECD.SDD.STES) 4.0 | REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION.TIME_HORIZ.METHODOLOGY | `USA.M.CCICP.PB._Z.Y._Z._Z.N` (composite consumer confidence, percentage balance) | `DF_CLI`, measure `CSCICP03` (a legacy MEI code; DF_CLI holds leading indicators) |
| oecd `business_confidence` | DF_BTS (OECD.SDD.STES) 4.0 | same nine dimensions | `USA.M.BCICP.PB.C.Y._Z._Z.N` (composite business confidence, manufacturing) | `DF_CLI`, `BSCICP03` |
| oecd `housing` | DF_RHPI_ALL (OECD.SDD.TPS) 1.1 | REF_AREA_TYPE.REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ADJUSTMENT.TRANSFORMATION.VINTAGE.DWELLINGS | `COU.USA.Q.RHPI.IX.N._Z.EXISTING.SINGLE_F` (house price index, existing single-family) | flow `DF_RHPI` does not exist; the area slot is the second one |

A `unit` or `definition` that no longer fits the corrected series is replaced at request time
(productivity: "USD PPP per hour (constant prices)"; confidence: "percentage balance"; BIS banking
claims, debt securities and liquidity: "(millions)" with `UNIT_MULT` 6 read from the response).
The OECD Data Explorer lists every dataflow as `NonProductionDataflow` (annotation on all 1,548),
which is the API's normal state, not a restriction.

## BIS property_prices (defect and handling)

* Fix: the shipped configuration collects `BIS,WS_SPP,1.0` with `Q..R.628`; a stored configuration
  that still holds `BIS,WS_LONG_CPI,1.0` + `.Q.R` is corrected at request time by `KEY_CORRECTIONS`
  (and the calendar follows the **corrected** flow, `_effective_bis_flow`, so the stored config is
  dated from RPP too). The key is validated against the structure (`BIS,WS_LONG_CPI` is documented as
  `FREQ.REF_AREA.UNIT_MEASURE`, no quarterly data: a quarterly key for it can never be right).
* Calendar: category RPP, only the rows of the three "Selected residential" release types (they
  update WS_SPP); "Detailed residential release" is WS_DPP and is not used.
* Data already in production (migration `20261007140000_macro_release_calendar_third_pass.sql`,
  rollback `supabase/rollback/20261007140000_macro_release_calendar_third_pass.down.sql`):
  observations stored under `series_id = 'property_prices'` whose `source_url` names WS_LONG_CPI
  (or that have no `VALUE` dimension and are not WS_SPP) are **re-keyed** to `series_id =
  'bis_long_cpi'`, losslessly (every column kept, `metadata.rekeyed_from = 'property_prices'`), with
  a `macro_series_metadata` row that says what they are; nothing mislabelled stays readable as
  property prices. The calendar rows that dated `property_prices` through category CPI are
  superseded (`superseded_at`, the append-only mechanism; history at an earlier `as_of` is kept).
  Rollback: the rows go back to `property_prices` (unless an identical key already exists, in which
  case they stay re-keyed), `superseded_at` is cleared, the metadata row is removed. Proven by
  `tests/test_macro_release_calendar_third_pass.py` (data round trip incl. a WS_SPP row and another
  series left untouched) and `tests/test_lifecycle_phase8.py` (catalog fingerprint round trip).
  Likelihood: low (see inaccuracy 5: the old request form cannot have returned data), but the
  migration costs nothing when no such rows exist.

## ALFRED output_type=1 (real FRED key, 2026-10-03)

Verified live (`UNRATE`, `CPIAUCSL`; recordings in `tests/fixtures/alfred_live`): `output_type=1`
returns one row per observation and real-time window (`realtime_start`, `realtime_end`, value):
UNRATE 2025-07 was `4.2` from 2025-08-01 and `4.3` from 2026-01-09, `realtime_end` 9999-12-31 for
the open window. Two consequences, both fixed in `store_fred_vintages`:

* the window is clipped to the requested `realtime_start`: with `realtime_start=2016-01-01`
  UNRATE 2015-06 (first published 2015-07-02) came back with `realtime_start 2016-01-01`. The
  request now starts at FRED's earliest real-time date `1776-07-04`, which gives the true first
  release dates (2015-07-02);
* `units` other than `lin` with `output_type=1` over a range is refused (`400`), so `us_cpi`
  (`pc1`) is fetched as levels and the year-over-year change is applied per vintage with FRED's
  formula, rounded half up to five decimals: the derived current vintage equals FRED's own `pc1`
  for every month with a value (checked on 12 months; October 2025 has no CPI at all and gets no
  row). Other transformations have no vintage history and are reported, not faked.

## ECB Data Portal keys, verified live

| Series | Shipped key | Live | Now |
|---|---|---|---|
| `ecb_deposit_rate` | `FM/D.U2.EUR.4F.KR.DFR.LEV` | 200, 2.5 (2026-10-03) | same |
| `euro_area_private_credit` | `BSI/M.U2.N.A.A20.A.1.U2.2300.Z01.E` | 404 (no series) | `BSI/M.U2.N.A.A20.A.1.U2.0000.Z01.E` (loans to euro area residents granted by MFIs excl. Eurosystem, stocks, 25.2 trillion EUR) |
| `euro_area_10y_yield` | `YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y` | 200, 3.592 (2026-10-01); a long window can answer `504` | same |
| `euro_area_m3_growth` | `BSI/BSI.M.U2.Y.V.M30.A.I.U2.0000.Z01.A` | 400 | `BSI/M.U2.Y.V.M30.X.I.U2.2300.Z01.A` (M3 annual growth, 3.49 for 2026-08) |
| `euro_area_household_loan_rate` | `MIR/MIR.M.U2.B.A2C.AM.R.A.2250.EUR.N` | 400 | `MIR/M.U2.B.A2C.AM.R.A.2250.EUR.N` (3.6) |
| `euro_area_corporate_loan_rate` | `MIR/M.U2.B.A2I.AM.R.A.2240.EUR.N` | 200 (3.77) | same |
| `ea_household_overnight_deposit_rate` | `MIR/M.U2.B.L21.A.R.A.2250.EUR.N` | 200 (0.29) | same |
| `ea_corporate_term_deposit_rate` | `MIR/M.U2.B.L22.A.R.A.2240.EUR.N` | 200 (2.25; one `504` earlier) | same |
| `ea_bls_enterprise_credit_standards` | `BLS/BLS.Q.U2.ALL.O.E.Z.B3.ST.S.WFNET` | 400 | `BLS/Q.U2.ALL.O.E.Z.B3.ST.S.WFNET` (7.29, 2026-Q3) |

Eurostat: the five shipped filter sets were each answered with one series for DE (HICP also for
EA21; CZ has no 2026-09 yet), so the filters pin a single series per geography. Comtrade: the
keyed endpoint (`/data/v1/get`) cannot be called without `UN_COMTRADE_API_KEY` (not in this
environment); the keyless `/public/v1/preview/C/A/HS` answers the same query parameters and row
fields the parser reads (`reporterCode`, `partnerCode`, `flowCode`, `classificationCode`, `cmdCode`,
`period`, `primaryValue`, `qty`, `netWgt`; recorded in `tests/fixtures/comtrade_preview_live.json`).
World Bank: the nine indicator codes are confirmed by `/v2/indicator/<id>` (all source "World
Development Indicators", names match the labels); the data endpoint (`/v2/country/.../indicator/...`)
answered a Cloudflare `502` for every variant during this session (while `/v2/sources` answered
`200`), so the live row shape and the `lastupdated` header of a data response could not be
re-checked now; the parser stays as verified earlier by recorded fixtures.

## Found but not adopted

| Provider | What was found | Why not adopted |
|---|---|---|
| ČNB data-publishing schedule | `https://www.cnb.cz/export/sites/cnb/cs/.galleries/kalendar/harmonogram.xls` (`200`, 1.1 MB, one sheet per year, `2027_q1_en` preliminary) | the only series of ours it could date is the one the HTML page already dates; binary XLS would need `xlrd` (used only for the cross-check). The calendar page `https://www.cnb.cz/en/cnb-news/calendar/` is script-loaded |
| BEA | `https://www.bea.gov/news/schedule` (`200` HTML, `/ical` also returns HTML) | no BEA series is collected |
| Eurostat XML events endpoint | `.../o/calendars/events?category=2` (`200 text/xml`) | same service and dates as the iCal (identical); the iCal stays the parsed source |
| World Bank data catalog (third pass) | `https://datacatalog.worldbank.org/search/dataset/0037712/World-Development-Indicators` (`200`, embedded JSON): `maintenance_information.update_frequency "Quarter"`, `update_schedule "April, July, September, December"`, `last_updated_date 2026-10-01`; `https://api.worldbank.org/v2/sources` -> WDI `lastupdated 2026-07-13` | months, not dates: a forward date from it would be an estimate (and WDI's own last update, 13 Jul, does not match "July" updates by day). Not stored. The catalog API (`/api/3/action/package_search`) is `404` |
| IMF (third pass) | `https://api.imf.org/external/sdmx/2.1/dataflow/all/all/latest` (`200`, structures only); `https://data.imf.org/en/Release%20Calendar` (`200`, script-loaded Sitecore page; no dates in the HTML); `https://dsbb.imf.org/sdds/arc` (`200`, the same shell as before); `https://www.imf.org/external/np/sta/ardc/index.aspx` (`200`, script-loaded) and `https://www.imf.org/en/Data/Statistics/ardc` (`403` Access Denied: an access control, not circumvented) | the IMF release calendars list the IMF's own datasets and national agencies' advance releases; none of them is an OECD or World Bank series we hold, and no JSON/XML calendar is served |
| UN Comtrade (third pass) | `https://comtradeapi.un.org/public/v1/getComtradeReleases` (`200`, 141 rows, `dateRelease` 2026-09-27 to 2026-10-03), `getDA/...` (availability); `files/v1/app/reference/ReleaseSchedule.json` `404`; `comtrade.un.org/data/release-calendar` is a script shell | both lists are releases that already happened: no future date |

## No free forward date found (these keep the estimate)

| Provider | Evidence (2026-10-03) |
|---|---|
| OECD | `www.oecd.org` (`/en/data/insights/statistical-releases.html`, `/en/about/events.html`, `/en/data.html`, the RSS and calendar paths) answers `403` with a Cloudflare challenge, also with a descriptive User-Agent and contact address: an access control, not circumvented. Hosts that answer give no dates: `data-explorer.oecd.org/release-calendar` is the data-explorer single-page app (the same shell for every path; its 1.6 MB bundle has no release-calendar code or endpoint), `sdmx.oecd.org/public/rest/v1/dataflow/all` (`200`, 1,548 dataflows) carries only layout/default/search annotation types (`NonProductionDataflow`, `LAYOUT_*`, `DEFAULT`, `RELATED_DATA`, ... none with a release or update date), `stats.oecd.org/RSS/` is `404`. A date inferred from a national source (BLS for US CPI) would be wrong: the OECD republishes on its own schedule |
| World Bank | see "Found but not adopted": months only, no dates; `datatopics.worldbank.org/.../release-schedule.html` and `data.worldbank.org/about/data-release-calendar` were `404` earlier; release notes exist only after publication |
| IMF | see above |
| UN Comtrade | see above |
| ECB TARGET closing days | not a rejection: adopted as `published_rule` (above). Not found: an ECB **public holidays** list (`/home/html/holidays.en.html` carries no list in its HTML), which is why the rule's title keeps the ECB's own caveat that an update may be postponed |

## Remaining estimated series and what would change that

The 7 OECD series (blocked at `www.oecd.org`), the 9 World Bank indicators (months only) and UN
Comtrade (past releases only). If the OECD opens a machine-readable calendar or lifts the challenge
for programmatic clients, one more adapter is the path; until then these series keep
`next_release_basis = observed_cadence`.

Wire note for readers of `next_release_time`: the time is the provider's wall clock, never
converted; its zone is `next_release_timezone`, and `next_release_timezone_source` says whether the
provider stated it. A null time means the calendar gives a date only, and then the zone is null.

## Behaviour

- Rows are append-only. A rescheduled release is a **new** row; the old row stays and gets
  `superseded_at` (the only column that may change; deletes are refused by a trigger). A date
  that merely lies in the past is never superseded. A superseded date that is published again
  becomes a new live row.
- `known_at` = the retrieval time of the run that first saw the date. The macro context at
  `as_of` uses rows with `known_at <= as_of` that were not superseded by `as_of`, and the
  earliest `release_date >= as_of`'s UTC date (rows of the BLS, calendar key `bls:%`, first for the
  FRED series BLS publishes). A `published_rule` row is re-derived by every run for the next 14
  days; rows are never deleted, so an `as_of` in the past sees what was known then.
- Coverage: one `coverage_status` row per calendar (`provider = macro_release_calendar`,
  `dataset = fred|bls|eurostat|ecb|bis|ecfin|cnb`, `entity_key = calendar`; `available_periods` = upcoming
  dates, `earliest_period`/`latest_period` = first and last upcoming date). Its stale threshold
  comes from `COVERAGE_DEFAULT_REFRESH_HOURS["macro_release_calendar"]` = 24 h (basis: the job
  runs daily and providers change calendars at most daily), so a row older than 48 h is stale.
