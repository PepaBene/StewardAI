# Free provider access register

Test snapshot: **2026-09-29**. The owner has confirmed permission for this personal,
single-user, self-hosted use. That permission is a project assumption, not an endpoint access
result. Production remains disabled until the configured account repeats a probe successfully;
no payment method or overage is permitted. Requests below contain placeholders, never secrets.

States have operational meaning: `available` is a successful free response, `paid_only` is an
observed plan denial, and `access_unverified`/`temporarily_failed` is a visible technical gap.
An empty response is `not_covered`, never success. Quotas are provider-published ceilings and
must be confirmed in `provider_access` during the account-specific probe.

| Provider | Endpoint / working request (secret redacted) | Auth / free quota and reset | Scope | Test result (2026-09-29) | Weight / rate |
|---|---|---|---|---|---|
| Alpha Vantage | `query?function=OVERVIEW&symbol=IBM&apikey=$ALPHA_VANTAGE_API_KEY` | key; 25 standard requests/day, provider reset | company overview | `available` 200 JSON with Symbol | 1 |
| Alpha Vantage | `query?function=NEWS_SENTIMENT&tickers=IBM&apikey=…` | same shared daily budget | recent articles | `access_unverified` pending owner-key probe | endpoint-dependent |
| EODHD | `api/eod/AAPL.US?from=2025-09-29&api_token=$EODHD_API_KEY&fmt=json` | key; 20/day | free-plan symbols, one year | `access_unverified` pending account probe | 1 |
| World Bank | `api.worldbank.org/v2/country/CA/indicator/NY.GDP.MKTP.CD?format=json` | none | annual country indicators | `available` 200 observations | unmetered; courteous pacing |
| OECD | `sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/.M.IR3TIB…` | none | SDMX public datasets | `available` 200 SDMX | rate limited; Retry-After |
| FMP | `stable/profile?symbol=AAPL&apikey=$FMP_API_KEY` | key; Basic 250/day | US profile | `access_unverified` account probe required | 1 |
| FMP | `stable/analyst-estimates?symbol=AAPL&apikey=…` | key | analyst estimates | `access_unverified`; owner-key probe required, documentation is not an observed denial | 1; hard-block until probe |
| Diffbot | `v3/article?token=$DIFFBOT_API_KEY&url=<url>` | key; 10,000 credits/month | source-page extraction | `access_unverified`; off by default | varies; KG export ~25 |
| People Data Labs | `v5/company/enrich?website=example.com` | key; about 100 company enrichments/month | company only; no personal contacts | `access_unverified`; off by default | 1 |
| EPO OPS | `3.2/rest-services/published-data/publication/epodoc/EP1000000/biblio` | OAuth client credentials; 4 GB/week | patents/families/legal status | `access_unverified` credentials absent | bytes |
| Finnhub | `api/v1/calendar/earnings?token=$FINNHUB_API_KEY` | key; free account limits | US earnings calendar | `available` in existing collector | 1 |
| Finnhub | `api/v1/stock/ownership?symbol=AAPL&token=…` | key | ownership | `access_unverified`; owner-key probe required | 1; hard-block until probe |
| Twelve Data | `time_series?symbol=AAPL&interval=1day&apikey=$TWELVE_DATA_API_KEY` | key; Basic credit/minute/day limits | US plus trial symbols | `access_unverified` owner-key probe required | endpoint credits |
| Twelve Data | `dividends?symbol=AAPL&apikey=…` and `splits?...` | key | corporate actions | `access_unverified`; owner-key probe required | no retry after observed denial |
| BIS | `stats.bis.org/api/v2/data/dataflow/BIS/WS_TC/2.0/.?startPeriod=2020` | none | public SDMX financial cycle | `available` public response | Retry-After |
| ECB | `data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata` | none | public SDMX, revision history | `available` existing collector | courteous pacing |
| UN Comtrade | `comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=124&period=2025&cmdCode=27` | preview keyless; subscription key for free registered quota (500/day, 100k rows/call) | selected reporter/partner/HS slices | `available` preview; registered production `access_unverified` | 1 call |

## Existing public disclosure and reference routes

| Source | Tested read route | Auth / lag / history | State |
|---|---|---|---|
| SEC submissions/companyfacts/archive | `data.sec.gov/submissions/CIK0000320193.json`; `data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json`; Archives accession URLs | descriptive `SEC_USER_AGENT`; filing-time; amendments retained | `available` |
| SEC statement-and-notes datasets | `sec.gov/files/dera/data/financial-statement-and-notes-data-sets/2026q2_notes.zip` | none; quarterly bulk/versioned by archive | `available` |
| SEC 13F, Forms 3–5, 13D/G, N-PORT, proxy | issuer submissions plus accession documents | same UA; filing lag and amendments | `available` discovery; form-specific parsing coverage varies |
| ESEF / filings.xbrl.org | `filings.xbrl.org/api/entities/5493001KJTIIGC8Y1R12.json` | none; as filed | `available` |
| GLEIF | `api.gleif.org/api/v1/lei-records?filter[lei]=…` | none; current and relationship records | `available` |
| ESMA FIRDS/FITRS | ESMA register downloads | none; publication lag; cancelled records must be retained | `available` |
| Eurostat / FRED / ECB / ČNB | dissemination API; FRED observations/vintages; ECB Data Portal; ČNB ARAD files | FRED key only; Eurostat live API has no old vintages | `available`; pre-activation Eurostat `vintage_unavailable` |
| ETF issuer files / issuer IR | configured iShares/Vanguard downloads and issuer URLs | none; undocumented endpoints/parser drift | `available` for configured funds |
| FCA NSM | `data.fca.org.uk/#/nsm/nationalstoragemechanism` public search/download UI | no general extraction API verified; PIP API is write-only | `temporarily_failed` automation gap, not licensing |
| EDINET | public EDINET API document list/download | API key registration; filings retained | `access_unverified` until key |
| Companies House | public company filing-history API | free API key; statutory filings | `access_unverified` until key |
| TED | `api.ted.europa.eu/v3/notices/search` | public API; EU notices | `available` |
| SEDAR+ | public search UI and issuer-hosted Canadian filings pilot | interactive retrieval; stability/throughput unresolved | `temporarily_failed` automated route under active pilot |

## Probe and benchmark procedure

1. Run the 30-security/12-country/10-HS fixture in
   `apps/backend/tests/fixtures/free_sources/audit_corpus.json`; store status, elapsed time,
   bytes, request id and credits in `provider_access`/`provider_quota_usage`.
2. Compare identifiers to primary filings/factsheets, prices to Trading212, and statistics to
   the official table. Record mismatch and incremental valid coverage—not vendor catalog size.
3. A missing key returns `access_unverified`; 401 returns `access_unverified`, 402/403 plan
   denial `paid_only`, 429 `quota_exhausted`, empty 2xx `not_covered`, and 5xx
   `temporarily_failed`. Preserve the last good observation in every case.
4. Never retry a paid/authentication denial until configuration changes. Redact query keys and
   headers from logs and evidence.
