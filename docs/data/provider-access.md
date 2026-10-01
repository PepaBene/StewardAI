# Free provider access register

Register written **2026-09-29**, corrected **2026-10-05**. The owner has confirmed permission
for this personal, single-user, self-hosted use. That permission is a project assumption, not an
endpoint access result. Production remains disabled until the configured account records a
successful request; no payment method or overage is permitted. Requests below contain
placeholders, never secrets.

**Nothing in this repository has been observed against a live account.** The "Seed state" columns
below are what migrations seed into `provider_access` and what the documentation says to expect;
they are not probe results. A row is `available` only once a request or audit probe actually
succeeded (`provider_access.last_success_at` is set); every row that claimed `available` without
one was changed to `access_unverified` by migration `20261005100000` (the previous values are kept
in `provider_access_state_backup`).

States have operational meaning. `provider_access.state` is the *durable* verdict:
`available` (a successful free response was recorded), `paid_only` (an observed plan denial),
`access_unverified` (never confirmed, or the credential was rejected) and `disputed` (a probe
returned another entity). A transient outcome (429, 5xx, timeout, empty 2xx) never changes it: it
is kept as `last_attempt_state`, with `consecutive_failures` and the provider's `retry_at`. An
empty response is `not_covered`, never success. Quotas are provider-published ceilings and
must be confirmed in `provider_access` during the account-specific probe.

| Provider | Endpoint / request shape (secret redacted) | Auth / free quota and reset | Scope | Seed state (not a probe result) | Weight / rate |
|---|---|---|---|---|---|
| Alpha Vantage | `query?function=OVERVIEW&symbol=IBM&apikey=$ALPHA_VANTAGE_API_KEY` | key; 25 standard requests/day, provider reset | company overview | seeded `access_unverified` until the first success; the OVERVIEW response is expected to carry `Symbol` | 1 |
| Alpha Vantage | `query?function=NEWS_SENTIMENT&tickers=IBM&apikey=…` | same shared daily budget | recent articles | `access_unverified` pending owner-key probe | endpoint-dependent |
| EODHD | `api/eod/AAPL.US?from=2025-09-29&api_token=$EODHD_API_KEY&fmt=json` | key; 20/day | free-plan symbols, one year | `access_unverified` pending account probe | 1 |
| World Bank | `api.worldbank.org/v2/country/CA/indicator/NY.GDP.MKTP.CD?format=json` | none | annual country indicators | seeded `access_unverified` (keyless; becomes `available` on the first collector success) | unmetered; courteous pacing |
| OECD | `sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_FINMARK,/.M.IR3TIB…` | none | SDMX public datasets | seeded `access_unverified` (keyless) | rate limited; Retry-After |
| FMP | `stable/profile?symbol=AAPL&apikey=$FMP_API_KEY` | key; Basic 250/day | US profile | `access_unverified` account probe required | 1 |
| FMP | `stable/analyst-estimates?symbol=AAPL&apikey=…` | key | analyst estimates | `access_unverified`; owner-key probe required, documentation is not an observed denial | 1; not blocked, the first request is the probe |
| Diffbot | `v3/article?token=$DIFFBOT_API_KEY&url=<url>` | key; 10,000 credits/month | source-page extraction | `access_unverified`; off by default | varies; KG export ~25 |
| People Data Labs | `v5/company/enrich?website=example.com` | key; about 100 company enrichments/month | company only; no personal contacts | `access_unverified`; off by default | 1 |
| EPO OPS | `3.2/rest-services/published-data/publication/epodoc/EP1000000/biblio` | OAuth client credentials; 4 GB/week | patents/families/legal status | `access_unverified` credentials absent | bytes |
| Finnhub | `api/v1/calendar/earnings?token=$FINNHUB_API_KEY` | key; free account limits | US earnings calendar | seeded `access_unverified` until the existing collector succeeds | 1 |
| Finnhub | `api/v1/stock/ownership?symbol=AAPL&token=…` | key | ownership | `access_unverified`; owner-key probe required | 1; not blocked, the first request is the probe |
| Twelve Data | `time_series?symbol=AAPL&interval=1day&apikey=$TWELVE_DATA_API_KEY` | key; Basic credit/minute/day limits | US plus trial symbols | `access_unverified` owner-key probe required | endpoint credits |
| Twelve Data | `dividends?symbol=AAPL&apikey=…` and `splits?...` | key | corporate actions | `access_unverified`; owner-key probe required | no retry after observed denial |
| BIS | `stats.bis.org/api/v2/data/dataflow/BIS/WS_TC/2.0/.?startPeriod=2020` | none | public SDMX financial cycle | seeded `access_unverified` (keyless) | Retry-After |
| ECB | `data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata` | none | public SDMX, revision history | seeded `access_unverified` until the existing collector succeeds | courteous pacing |
| UN Comtrade | `comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=124&period=2025&cmdCode=27` | preview keyless; subscription key for free registered quota (500/day, 100k rows/call) | selected reporter/partner/HS slices | seeded `access_unverified`; the collector calls `/data/v1/get/C/A/HS` with the registered key (the keyless preview route is not used) | 1 call |

## Existing public disclosure and reference routes

| Source | Documented read route | Auth / lag / history | Seed state (not a probe result) |
|---|---|---|---|
| SEC submissions/companyfacts/archive | `data.sec.gov/submissions/CIK0000320193.json`; `data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json`; Archives accession URLs | descriptive `SEC_USER_AGENT`; filing-time; amendments retained | documented route; no `provider_access` row, never probed |
| SEC statement-and-notes datasets | `sec.gov/files/dera/data/financial-statement-and-notes-data-sets/2026q2_notes.zip` | none; quarterly bulk/versioned by archive | documented route; no `provider_access` row, never probed |
| SEC 13F, Forms 3–5, 13D/G, N-PORT, proxy | issuer submissions plus accession documents | same UA; filing lag and amendments | documented discovery route; form-specific parsing coverage varies; never probed |
| ESEF / filings.xbrl.org | `filings.xbrl.org/api/entities/5493001KJTIIGC8Y1R12.json` | none; as filed | documented route; no `provider_access` row, never probed |
| GLEIF | `api.gleif.org/api/v1/lei-records?filter[lei]=…` | none; current and relationship records | documented route; no `provider_access` row, never probed |
| ESMA FIRDS/FITRS | ESMA register downloads | none; publication lag; cancelled records must be retained | documented route; no `provider_access` row, never probed |
| Eurostat / FRED / ECB / ČNB | dissemination API; FRED observations/vintages; ECB Data Portal; ČNB ARAD files | FRED key only; Eurostat live API has no old vintages | documented routes; seeded `access_unverified` in `provider_access`; pre-activation Eurostat `vintage_unavailable` |
| ETF issuer files / issuer IR | configured iShares/Vanguard downloads and issuer URLs | none; undocumented endpoints/parser drift | documented for configured funds; never probed |
| FCA NSM | `data.fca.org.uk/#/nsm/nationalstoragemechanism` public search/download UI | no general extraction API verified; PIP API is write-only | `temporarily_failed` automation gap, not licensing |
| EDINET | public EDINET API document list/download | API key registration; filings retained | `access_unverified` until key |
| Companies House | public company filing-history API | free API key; statutory filings | `access_unverified` until key |
| TED | `api.ted.europa.eu/v3/notices/search` | public API; EU notices | documented route; no `provider_access` row, never probed |
| SEDAR+ | public search UI and issuer-hosted Canadian filings pilot | interactive retrieval; stability/throughput unresolved | `temporarily_failed` automated route under active pilot |

## Source setup per provider (environment variables)

Variables are read by `apps/backend/src/steward/settings.py` and listed in `.env.example`. All
are backend-only secrets (never `NEXT_PUBLIC_*`); restart the API and worker after changing
one. "Delay" is the provider's publication delay or cadence as documented by the provider; none
of the live behaviour below has been verified against a live account from this repository
(fixtures are built from documented formats), so every row stays `access_unverified` until the
Source audit (`POST /jobs/source-audit`) or the first successful collector request has recorded
success for your account.

| Provider | Variables | How to get it | Free ceiling (provider-published) | User-visible scope in the app | Expected data delay |
|---|---|---|---|---|---|
| SEC EDGAR | `SEC_USER_AGENT` (descriptive text with a contact email) | no key; the SEC requires the header | fair-access rate limit | Form 13F holders, Forms 3/4/5 insider trades and activity, Schedule 13D/G control stakes, N-PORT fund positions, XBRL fundamentals, segments, shares outstanding, valuation inputs | filing time for 8-K/Forms 3-5; 13D/G within days of the event (structured XML only from Dec 2024); 13F up to 45 days after quarter end (data sets quarterly); N-PORT public about 60 days after the report date (fiscal calendar, not calendar quarters) |
| Stooq | `STOOQ_API_KEY` | captcha page `stooq.com/q/d/?s=aapl.us&get_apikey` | n/a | daily prices, returns, risk, benchmarks | end of day |
| Alpha Vantage | `ALPHA_VANTAGE_API_KEY` | alphavantage.co free key | 25 requests/day, one budget shared by every use | price-gap fallback in `price_history`, valuation `OVERVIEW` fallback, the `alpha_vantage` news source (when enabled in the news-sources config; sentiment is a hint, never a verified fact), owner-triggered exposure context and statement history (`POST /holdings/{ticker}/exposure-context/alpha-vantage`, `.../financial-history/alpha-vantage/{statement}`) | daily bars end of day; fundamentals lag the filing |
| EODHD | `EODHD_API_KEY` | eodhd.com free plan | 20 requests/day, one year of history | last-resort price-gap fallback in `price_history` only | end of day |
| Finnhub | `FINNHUB_API_KEY` | finnhub.io dashboard | free-account limits | US earnings calendar, US company news, `/stock/metric` valuation cross-check, owner-triggered peer/EPS surprise refresh, optional fund/ownership probes | calendar daily; news minutes |
| FMP | `FMP_API_KEY` | financialmodelingprep.com | Basic 250 requests/day | access probes only (`source_audit`, `fund_probes`); no data consumer reads FMP rows except the probed fund records | n/a |
| Twelve Data | `TWELVE_DATA_API_KEY` | twelvedata.com | credit/minute/day limits | access probes only (`source_audit`); no consumer yet | n/a |
| FRED / ALFRED | `FRED_API_KEY` | fredaccount.stlouisfed.org | generous; key required | US CPI, unemployment, Fed funds in `macro_collect` and vintage history (`alfred` rows) | release day; revisions kept as new vintages |
| UN Comtrade | `UN_COMTRADE_API_KEY` | comtradedeveloper.un.org (free registered subscription key) | 500 calls/day, 100k rows/call | reporter/partner/HS trade slices in `public_data_collect` (needs provider enabled, key, and an open provider gate), mapped exposure trade alerts | annual slice (the collector requests `/C/A/HS`), published months after year end |
| EPO OPS | `EPO_OPS_CLIENT_ID`, `EPO_OPS_CLIENT_SECRET` | developers.epo.org OAuth client | about 4 GB/week | owner-triggered patent family refresh, patent trends, legal-status alerts | publication about 18 months after filing; legal status as EPO updates it |
| Diffbot | `DIFFBOT_API_KEY` | diffbot.com | about 10,000 credits/month | owner-triggered company research (`POST /holdings/{ticker}/company-research/diffbot`), off by default | on demand |
| People Data Labs | `PEOPLE_DATA_LABS_API_KEY` | peopledatalabs.com | about 100 company enrichments/month | owner-triggered company-only enrichment (`.../company-research/pdl`), no personal data, off by default | on demand |
| OpenFIGI | `OPENFIGI_API_KEY` (optional) | openfigi.com/api | higher rate with key; works without | identifier mapping in `reference_data` | weekly job |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com | pay-as-you-go (not free) | extraction, classification, digests | n/a |
| Keyless | none | n/a | courteous pacing / `Retry-After` | ECB, Eurostat, ČNB (macro_collect); World Bank, OECD, BIS (`public_data_collect`); GLEIF, ESMA FIRDS/FITRS, filings.xbrl.org ESEF, iShares/Vanguard issuer pages, Yahoo price fallback, Nager.Date holidays | per provider table above |

### Enabling a provider (three gates)

1. **Credential** in the environment (or none for keyless providers).
2. **Config switch**: `JobsConfig.providers[<name>].enabled` (default `false` for every free
   provider; edit in `/config/jobs` or `PUT /config/jobs`) with its `daily_quota`/`monthly_quota`
   reserve (defaults: Alpha Vantage 25/day, EODHD 20/day, FMP 250/day, Comtrade 500/day,
   Diffbot 10,000/month, PDL 100/month).
3. **Provider gate**: `public.provider_gate_open(provider, endpoint)` (Python:
   `steward.sources.store.provider_gate`) is true when the `provider_access` row is not
   hard-blocked, its durable state is `available` or `access_unverified`, and no provider
   Retry-After window (`retry_at`) is pending. `access_unverified` is open on purpose: the first
   real request is the probe, and its success records `available`. `paid_only` and `disputed`
   close the gate, as does a hard block (a 402, an explicit plan denial, or a 401/403 on a
   credential); a changed credential lifts the hard block at the next `source_audit`. A 429, 5xx,
   timeout or empty 2xx never closes the gate by itself, except for the Retry-After window the
   provider named, which the request governor enforces. The `source_audit` job (schedule
   disabled by default) or `POST /jobs/source-audit` probes the endpoint with your credential.
   `public_data_collect` (World Bank, OECD, BIS, Comtrade) skips any series whose provider is
   disabled or whose gate is closed; its schedule is also disabled by default (Sundays 04:00 when
   switched on). Owner-triggered POST routes return `409` when their key is missing.

### Removal and disable behaviour

Removing a key (or setting `enabled=false`) never writes a zero or deletes stored data.
Stored observations stay readable with their original source and dates; the affected collector
step records `skipped` or `access_unverified` ("credential not configured") and the source
appears as a coverage gap (`GET /data/coverage`, `GET /sources/access`).

| Removed / disabled | What degrades | What is unaffected |
|---|---|---|
| `SEC_USER_AGENT` | jobs `ownership_13f`, `control_stakes`, `nport_holdings`, `insider_trades` fail with an explicit message; EDGAR fundamentals, news filings and reconciliation are skipped | stored filings and reads |
| `STOOQ_API_KEY` | Stooq downloads refused; prices fall back to Yahoo, then Alpha Vantage, then EODHD when those are configured; remaining gaps listed on Activity | stored bars |
| `ALPHA_VANTAGE_API_KEY` | no OVERVIEW/price/news fallback; its routes answer 409 | everything else |
| `EODHD_API_KEY` | last price fallback gone | other fallbacks |
| `FINNHUB_API_KEY` | report dates come from the IR calendar page; no Finnhub news; no tier-2 valuation cross-check; fund probes `access_unverified` | EDGAR sources |
| `FMP_API_KEY`, `TWELVE_DATA_API_KEY` | probes record `access_unverified`; nothing else reads them | all |
| `FRED_API_KEY` | FRED series skipped (US CPI, unemployment, Fed funds, and FRED credit rules); ALFRED vintages stop | Eurostat, ECB, ČNB |
| `UN_COMTRADE_API_KEY` | Comtrade steps skipped; trade-exposure alerts have no new observations | World Bank, OECD, BIS |
| `EPO_OPS_*` | patent refresh route answers 409; no new legal-status readings, so no patent alerts | stored patent families (a missing reading keeps the last known status, never "expired") |
| `DIFFBOT_API_KEY`, `PEOPLE_DATA_LABS_API_KEY` | owner-triggered enrichment unavailable (409) | all |
| `ANTHROPIC_API_KEY` | AI steps fail; deterministic collectors and the free-source alert evaluator still run | |
| `free_source_alerts.enabled=false` (Event rules) | the evaluator writes nothing; no new alerts or trail rows | collectors and reads |

## Jobs and endpoints added in Phases 2-7

Jobs (schedule defaults are from `config/jobs.py`; times are cron in the Jobs config timezone):

| Job | Default | Needs | What it does |
|---|---|---|---|
| `public_data_collect` | disabled (`0 4 * * 0`) | provider enabled + audit gate; Comtrade also `UN_COMTRADE_API_KEY` | World Bank, OECD, BIS, Comtrade into `macro_observation` / `trade_observation` |
| `source_audit` | disabled (`0 3 1 * *`) | credentials of the probed providers | endpoint-level access probes into `provider_access` |
| `sec_xbrl` | Tue 05:40 | `SEC_USER_AGENT` | shares outstanding, revenue by region/segment |
| `valuation_refresh` | daily 07:45 | `SEC_USER_AGENT` (SEC part) | P/E, P/B, yield inputs; ETF provider figures |
| `insider_trades` | daily 06:30 | `SEC_USER_AGENT` | Forms 3/4/5 for held issuers |
| `control_stakes` | daily 06:45 | `SEC_USER_AGENT` | Schedule 13D/G for held issuers and tracked managers; never added to 13F or fund data |
| `nport_holdings` | Sat 05:15 | `SEC_USER_AGENT` | SEC N-PORT positions of held US funds without an issuer snapshot (UCITS do not file N-PORT); about 60 days public lag |
| `fund_probes` | disabled (`0 4 * * 0`) | `FINNHUB_API_KEY` and/or `FMP_API_KEY` | probes optional fund/index/ownership endpoints; 402/403 is `paid_only` and is not retried |
| `fund_characteristics_audit` | daily 07:30 | none | compares issuer fund P/E and P/B payload shape and rules against the last observation: `parser_drift` or `methodology_change` is flagged for review instead of being treated as a value change |
| `etf_look_through` | Sat 06:40 | none | issuer fund holdings |
| `free_source_alerts` | daily after `macro_collect` (see wiring) | none | evaluates the alert rules below |

Endpoints (owner-authenticated; see `apps/backend/openapi.json`, which is regenerated from
code): `GET /data/coverage`, `GET /sources/access`, `GET /sources/usage`, `GET /sources/status`,
`GET /macro/context/{ticker}`, `GET /macro/coverage/portfolio`, `GET|POST|DELETE /macro/exposures`,
`GET /macro/trade/{reporter}/{hs_code}`, `GET /holdings/{ticker}/{company-research-observations,
exposure-context, financial-history, forward-data, issuer-ir-releases, peer-set, peer-metrics,
corporate-actions, returns, prices/diagnostics, business-network, company-events,
patent-families, patent-trends, customer-supplier-concentration, control-stakes, insider-activity,
insider-trades, fund-positions, valuation, segments, intraday}`, `GET /funds/{aggregate, overlap,
look-through, coverage-ledger, characteristics-audit}`, `GET|PUT /funds/{isin}/factsheet`,
`POST /funds/snapshots/pin`, `GET /portfolio/{valuation, look-through, intraday}`,
`GET /alerts/free-source-trail`, and the run-now routes `POST /jobs/{public-data, source-audit,
sec-xbrl, valuation-refresh, insider-trades, control-stakes, nport-holdings, etf-look-through,
free-source-alerts}`.

## Free-source alert rules (D7.5)

Configured under `free_source_alerts` in the **Event rules** config (`/config/event-rules`);
evaluated by the `free_source_alerts` job; every decision is written to
`free_source_alert_trail` and read with `GET /alerts/free-source-trail` (filters `rule`,
`outcome`, `ticker`, paging with `before_id`).

| Rule | Fires when | Never fires on |
|---|---|---|
| `credit_stress` | a new observation of a configured `macro_observation` series (BIS, ECB or FRED; defaults: ECB bank-lending-survey enterprise credit standards above 10, BIS US debt service ratio above 15; both are uncalibrated starting points) crosses its threshold and the previous period had not | a revision or backfill of an old period; a series' first load; a value that stayed beyond the threshold; a series that is not collected |
| `trade_exposure` | a new Comtrade period moves at least 20% year on year for an HS code in an `exposure_mapping` of a held issuer with weight at least 0.05 | an unmapped (unknown) exposure; a missing prior-year value (recorded `no_baseline`); a revision |
| `patent_status` | an owned family (direct or dated-subsidiary ownership, confidence at least 0.8) goes granted/active to expired, or application to granted/active | `unknown` on either side (the last known status is kept); suggested ownership |
| `company_graph` | a `confirmed` company event (deal, leadership, relationship) or `confirmed` graph edge with primary/regulatory/owner-review evidence and a cited passage appears | `suggested` or `disputed` rows, or a "confirmed" row without primary evidence: recorded `review_needed` for the owner, no news item, no alert, and this module never writes a thesis breach |

Alerts are tier-1 synthetic news items (event types `credit_stress`, `trade_exposure`,
`patent_status`, or the existing `m_and_a`, `management_change`, `sector_competitor`,
`capital_allocation`), so the usual news assessment, Event rules severity (defaults 2, 2, 1),
notification rules, repeat throttle and daily cap apply on top. The evaluator adds one alert per
rule and scope (series, or holding plus product) per `throttle_hours` (24), a per-run cap
(`max_alerts_per_run`, 10) and a `lookback_days` (3) window. Outcomes in the trail: `raised`,
`throttled`, `revision_ignored`, `baseline`, `not_material`, `no_baseline`, `persisting`,
`review_needed`, `below_severity`; silent outcomes (below threshold, identical re-fetch) are only
counted in the job step.

## Probe and benchmark procedure

1. Run the 30-security/12-country/10-HS fixture in
   `apps/backend/tests/fixtures/free_sources/audit_corpus.json`; store status, elapsed time,
   bytes, request id and credits in `provider_access`/`provider_quota_usage`.
2. Compare identifiers to primary filings/factsheets, prices to Trading212, and statistics to
   the official table. Record mismatch and incremental valid coverage—not vendor catalog size.
3. A missing key returns `access_unverified`; 401 returns `access_unverified` (hard-blocked until
   the credential changes); 402 is `paid_only`; 403 is `paid_only` only when the body names the
   plan boundary, else `access_unverified`; a 402/403/200 whose body reports an exhausted
   allowance ("rate limit", "quota", "limit reached") is `quota_exhausted` even if it also says
   "upgrade"; 429 is `quota_exhausted`; empty 2xx `not_covered`; 5xx `temporarily_failed`. Only
   `available`, `paid_only`, `access_unverified` and `disputed` change the durable
   `provider_access.state`. Preserve the last good observation in every case.
4. Never retry a paid/authentication denial until configuration changes. Redact query keys and
   headers from logs and evidence.
