# StewardAI: free data sources implementation plan

Research snapshot: 29 September 2026. Companion: [Morningstar-style features plan](morningstar_features_implementation_plan.md).

## Goal and constraints

Use every provider named by the owner where a **permanently free, technically accessible** source supports a useful workflow. A free trial, sample response, marketing page, or paid-only endpoint does not count as production coverage. The sole target is the owner's **personal, single-user, self-hosted** StewardAI app. The owner has confirmed permission for the intended data use, so licensing is not a blocker or a prerequisite for these plans. Still test account access, retrieval methods, endpoint availability and quotas. A paid plan is never silently assumed and no job may incur overage charges.

“Use” means a visible decision or analysis: a country or company exposure, a defensible price and return, a peer comparison, a research finding, a thesis check, or a coverage gap. Raw ingestion without one of those consumers does not complete a task.

### Starting point in this repository

- `apps/backend/src/steward/sources/alphavantage.py` already fetches Company Overview as a valuation fallback; `sources/finnhub.py` supplies earnings dates and basic financial ratios. `valuation/collect.py` combines these with SEC/ESEF and issuer fund data.
- `macro/collect.py` already loads ECB policy rates, Eurostat, FRED, and ČNB. `reference/prices.py`, `sources/market.py`, and `reference/intraday.py` use Stooq and Yahoo for history and quotes.
- `lookthrough/` stores iShares/Vanguard holdings; `ownership/` collects SEC 13F and insider data. `reference/identity.py` and the `instrument`, `listing`, `legal_entity`, and `entity_relationship` tables resolve the current portfolio.
- `portfolio/` already supplies snapshots, transactions, drift, some risk and contribution analytics. The frontend has valuation, exposure, look-through, price, risk, and ownership panels. Preserve and extend those paths instead of creating parallel screens or duplicate truth tables.
- The current database is optimized for **held tickers**. A wider research universe needs new identity and coverage records, while keeping the existing Trading212 ticker as an alias rather than a global primary key.

### Audit of free sources already in StewardAI

This is a code audit, not a claim that every field in a filing is already collected. The [public-disclosure reconstruction plan](public_disclosure_datasets_implementation_plan.md) provides the detailed build sequence for the filing-, IR- and procurement-derived datasets below. Extend existing collectors and tables; do not create a second, conflicting copy of 13F, insider trades, shares or segment revenue.

| Existing source and code evidence | Collected today | Additional useful data and actual use | Priority / gate |
| --- | --- | --- | --- |
| SEC submissions and 8-K/6-K: `sources/edgar.py`, `news/feeds.py`, `earnings/locate.py` | Filing list, 8-K item labels, earnings exhibits and alerts. | Parse dated material agreements, acquisitions/disposals, financing, guidance, management changes and termination/completion terms into a deal/event timeline; connect events to holdings, valuation and theses. | High; extract specific documents/exhibits, not every filing body. |
| SEC companyfacts and filing XBRL: `fundamentals/collect.py`, `sources/xbrl.py`, `valuation/collect.py` | Selected shares, latest valuation facts and one-dimensional revenue by geography/segment/product. | Build as-filed, point-in-time statements and selected notes: debt maturities, leases, commitments, cash tax, R&D, buybacks, dilution, pension, customer concentration and richer segment profit/assets/capex. The SEC's separate [Financial Statement and Notes Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets) can backfill gaps after reconciliation to filings. | Highest; current `companyfacts`-style facts do not replace contextual note and dimensional extraction. |
| SEC full-text search: `news/regulatory.py`, `news/pipeline.py` | Other filers mentioning a holding near “customer”/“supplier” become **suggested** relationships. | Fetch the cited filing passage, determine who buys from whom, capture named versus anonymous counterparty, period and reported percentage, then confirm/reject the edge. Use concentration changes and portfolio propagation, never keyword co-occurrence as proof. | Highest quality fix; existing suggestions stay suggestions until validated. |
| SEC 13F: `sources/form13f.py`, `ownership/collect.py`, `ownership/read.py` | Selected portfolio CUSIPs and tracked managers, filings/amendments, top holders and changes. | Expand to watchlist where valuable; exploit stored voting/discretion and put/call flags without calling them beneficial ownership; add separately sourced Schedule 13D/G control stakes and holder identity. | High; 13F, 13D/G and fund holdings are different measures and must never be summed. |
| SEC Forms 3/4/5: `ownership/insider.py`, `ownership/insider_collect.py` | XML transactions/initial holdings with raw transaction code and direct/indirect flag for held issuers. | Parse footnotes, derivative terms, amendments and any reported plan flags; distinguish open-market P/S from grants, options, gifts and tax withholding. Calculate genuinely discretionary net purchases and ownership changes, with explicit exclusions. | High; existing rows are useful, but raw code alone is not an insider-conviction metric. |
| SEC fund and governance filings: existing EDGAR client and issuer look-through | SEC N-PORT and proxy data are not yet structured into fund/governance records. | Public N-PORT holdings/derivatives/fees/risk fields where present; DEF 14A board, pay, beneficial ownership and shareholder-vote facts; N-CEN/N-CSR only when a specific field fills a measured gap. | Medium-high; fund and proxy coverage is US-specific, delayed and filing-dependent. |
| ESEF via `sources/esef.py` and `valuation/collect.py` | Selected non-dimensional IFRS facts from filings.xbrl.org, primarily valuation. | Retain as-filed versions, dimensions and issuer extensions; add statement history and cited note blocks for segment, debt, lease and customer exposure. | High; ESMA says primary statements are detailed-tagged while notes are generally block-tagged, so not all note values are machine-readable. |
| GLEIF, OpenFIGI and ESMA: `sources/gleif.py`, `sources/openfigi.py`, `sources/esma.py`, `reference/identity.py` | LEI identity/parents/children, share-class/listing identifiers, current FIRDS venue and FITRS liquidity. | Track former names, successors, group-change dates, listing termination/status history and liquidity windows; use them for delisting-aware screens, deal entity resolution and corporate-structure changes. `sources/esma.py` currently drops cancelled FIRDS records, so historical state needs a separate ingest path. | High for identity; GLEIF parents mean accounting consolidation, never investor ownership. |
| Wikidata: `sources/wikidata.py`, `reference/identity.py` | Multilingual aliases and suggested parent, industry, product, leader and competitor links. | Use alias/website history to resolve filings and translated issuer names, and candidate industries/products/peers to expand research; retain source-rank and corroborate each consequential edge with issuer/regulator evidence before portfolio or valuation use. | Medium; community-edited graph claims are suggestions, not verified corporate structure or financials. |
| Eurostat, FRED, ECB and ČNB: `macro/collect.py`, `config/jobs.py` | A small configured set of macro/policy series for CZ, DE, euro area and US. | Add industry/energy/trade/credit/yield series with exact geography and units; use FRED/ALFRED vintages for historical as-of analysis and archive each Eurostat release from now on. Audit the current FRED `realtime_start` handling: a current-vintage fetch date is **not** proof of original release date. | High for honest backtests; Eurostat's live API exposes latest values, not past vintages. |
| iShares/Vanguard issuer data: `sources/etf_issuers.py`, `lookthrough/collect.py` | Configured fund holdings and sector/country breakdowns, retaining only a limited number of snapshots. | Add factsheet charges, benchmark, distribution policy, NAV/return basis and securities-lending/derivative disclosures. Retain dated holdings needed for historical X-Ray instead of pruning evidence needed by a saved decision. | High for portfolio; undocumented issuer endpoints require parser-drift checks. |
| iShares/Vanguard fund characteristics: `sources/fund_characteristics.py` | Fund and benchmark P/E/P/B and a forecast dividend yield where available, with issuer-specific methodology. | Track changes in fund valuation, compare it to weighted constituent fundamentals only on the same basis, and show how exclusion/capping of negative or extreme multiples affects a Morningstar-style portfolio X-Ray. | Medium-high; undocumented page/GraphQL structures and incompatible methods require parser-drift checks. |
| Issuer IR and UK/German announcements: `news/feeds.py`, `news/regulatory.py`, `documents/fetch.py` | News, regulatory announcements and research source documents. | Collect versioned company-published consensus, guidance, capital-return notices, TR-1/PDMR disclosures and deal status through a reliable retrieval method. | High; the FCA NSM's API referenced for **PIP submissions** is not a general public extraction API, so find a usable read route. |
| Trading212 and market sources: `portfolio/transactions.py`, `portfolio/sync.py`, `reference/prices.py`, `sources/market.py`, `sources/pse.py` | Transactions/snapshots, Stooq/Yahoo history and selected market/index checks. | Audit unhandled broker event subtypes and corporate actions; preserve cash/FX/fee evidence for TWR/XIRR. Compare adjusted and raw prices and validate the PSE index fallback's availability. | High for return correctness; do not infer a missing broker event from a price jump. |
| Nager.Date holidays and exchange calendars: `reference/prices.py`, `reference/calendar.py` | National public holidays and configured venue closures help explain missing FX fixes and market days. | Extend confirmed venue sessions, half-days and ad hoc closures for global price completeness, release scheduling and false-staleness suppression; show whether a gap occurred on a true trading day. | Medium; national holidays only seed suggestions and cannot be treated as exchange calendars. |

The biggest immediate wins are **SEC note/dimensional XBRL, actual passage validation of existing customer/supplier suggestions, 13D/G beside 13F, economically classified Form 4, issuer-published consensus, and true macro vintages**. Include SEDAR+ in the Canadian filing discovery pilot under the owner's stated permission; test its authentication, retrieval method, stability and throughput alongside issuer-hosted Canadian disclosures. Do not mark it blocked solely because of licensing.

## Frontend visualization contract

The existing web app favors purpose-built SVG/data components under `apps/web/src/components/charts/` and `lib/charts/`, paired with provenance-rich detail panels (for example portfolio look-through, risk, price and ownership). Extend those conventions rather than introducing generic dashboard charts: use a chart only when its visual encoding answers a decision question, and keep exact values accessible through a semantic table/list equivalent. Reuse the existing treemap, Sankey, heatmap, candlestick, radar, line/bar and shared chart primitives where their semantics fit; add a new bespoke chart only for a distinct analytic task. Charts must distinguish missing from zero, show denominator and units, preserve uncertainty/coverage, and link marks to evidence. Avoid maps or networks as decoration; include ranked or tabular alternatives for precise comparisons.

## Provider allocation and actual use

| Provider | Maximum useful free role | Product-facing use after verification | Boundary to retain |
| --- | --- | --- | --- |
| Alpha Vantage | Existing `OVERVIEW` fallback; selectively test free financial statements, daily bars, news sentiment, commodities and FX. | Fill a missing owner-held valuation input; corroborate a price or commodity exposure; link a news event to its original article. | 25 standard calls/day; endpoint-specific premium flags. Never present sentiment as a verified fact. |
| EODHD | Free one-year EOD bars and exchange/symbol discovery. | Cover a small number of held/watchlist listings that Stooq/Yahoo miss; compare close and exchange currency. | 20 calls/day and one-year history; no global historical backfill on the free plan. |
| World Bank | Keyless development and country indicators. | Country cards, economic context, demand-market and sovereign exposure in research/portfolio views. | Often annual and lagged; retain each indicator's source and definitions. |
| OECD | Free SDMX country and industry statistics. | Labour, productivity, inflation, trade and sector context with comparable definitions and release dates. | Uneven country coverage; respect rate limits. |
| FMP | Free Basic endpoint experiments for company profiles, US sector/industry snapshots, and analyst estimates. | Show a US sector comparison or estimate **if the exact free key returns that endpoint**; otherwise compute a peer/sector comparison from covered issuers. | 250 calls/day; global coverage is paid. An API documentation example is not proof of free access. |
| Diffbot | 10,000 monthly credits of Extract, KG, NLP and search. | Suggest competitors, leaders, products, funding, deals, partnerships and company relationships; cite source pages; feed the review queue. | About 25 credits per exported KG entity, fields vary; never turn a suggested relationship into a verified ownership claim without primary corroboration. |
| People Data Labs | Roughly 100 free company-enrichment credits/month; free supporting lookup/cleaning subject to limits. | Resolve hard company matches; attach dated headcount, employee-growth or funding clues to a research profile. | Small budget; avoid personal-contact data because it does not serve company analysis. |
| EPO OPS | Up to 4 GB/week of bibliographic, patent-family, citation and legal-status data. | Patent portfolio, technology concentration and competitive-intensity sections for relevant companies. | Applicant names and assignees need entity resolution; do not republish a raw patent database. |
| Finnhub | Existing calendar, US news and basic metrics; test available peers and recent earnings surprises. | Estimate-versus-actual at the event level, peer candidates, corroborative ratio checks and event alerts. | Dedicated forward estimates, standardized global statements, full ownership, ETF holdings and index constituents are marked premium. |
| Twelve Data | Basic free US/FX/crypto/reference queries and a small set of global trial symbols. | Use accessible free quotes/FX for cross-checks and visible prices where coverage supports it. | Global EOD and `/dividends`/`/splits` may be paid; test exact free-key responses. |
| BIS | Free public SDMX banking, credit, debt-securities and liquidity series. | Financial-cycle dashboard and country/bank-sector risk context. | Aggregate geography/sector data, not company financials; retain source attribution. |
| ECB | Expand the existing free SDMX integration beyond policy rates/FX. | Euro-area credit, deposits, monetary aggregates, lending rates and financial-condition context. | Preserve original observations and label transformations/revisions; third-party and confidential datasets differ. |
| UN Comtrade | Registered free API: 500 calls/day, up to 100,000 records/call. | Bilateral product-level trade trends and country/sector exposure for private research. | Trade rows do not identify company customers/suppliers; respect free API quotas. |

The endpoint and availability facts behind this table are linked in [Source references](#source-references). Phase 0 verifies free access with actual accounts before writing collectors; the owner's permission is the usage assumption.

## Cross-source data contract

Implement this once before adding collectors. It lets the app expose uncertainty instead of pretending providers agree.

1. **Identity:** `issuer_id`/`legal_entity_id` for the business, `security_id` for a share class or fund, `listing_id` for an exchange line, and `provider_symbol` for each vendor. Store ISIN, LEI, FIGI, CIK, MIC and trading currency with valid-from/to dates and match evidence. Never merge an ADR, ordinary share, ETF, or index merely because tickers match.
2. **Observation:** domain, metric, canonical entity/security/country, period start/end, fiscal period, value, unit, scale, currency, source, source URL/document, publisher timestamp, retrieval timestamp, valid time, vintage/revision, transformation version, and source priority. Preserve null/unknown distinctly from zero.
3. **Access and quota:** provider + endpoint + account tier + required credential + free-tier availability + quota/weight + expiry/review date. Record the owner's stated permission once as a project assumption; only a technical denial, missing credential or paid-only endpoint blocks collection.
4. **Evidence and quality:** original source payload, normalized record, validator results, disagreements, confidence and owner decision. Retain a fetch audit and source link; set storage retention based on analytical need and operational cost.
5. **Coverage:** expected universe, matched securities, available periods, freshness, access state and reason for every gap. Expose coverage at metric, provider, country, fund and portfolio levels.
6. **Point-in-time:** queries for screens/backtests/reports must only use records known by the requested as-of date. Keep revisions and restatements; never backfill future knowledge into a historical decision.

### Implementation contract for every provider

The following is the work required *inside* each `D2`–`D6` adapter, not an optional second pass:

1. **Endpoint manifest:** record exact path, parameters, pagination, timezone, response units, free-plan evidence, request cost, update cadence and sample fixture. A documented endpoint without a successful free-key test is marked `access_unverified` or `paid_only`, not a licensing block.
2. **Discovery and identity:** map the provider's symbol or country/product code to the canonical issuer, security, listing, geography or HS revision. Persist the mapping decision and rejected candidates. A ticker-only company match cannot publish a fact.
3. **Fetch and budget:** reserve credits atomically before each call; use conditional requests/incremental dates where supported. Record response status, provider request ID where available, bytes/credits, retry time and a redacted error. Do not retry a hard paid-tier or authentication denial until configuration changes.
4. **Normalize and validate:** parse into typed records; check dates, currencies, unit scales, balance identities, plausible ranges, uniqueness and the source's own metadata. Quarantine unexpected schema changes or an unbalanced series before publication.
5. **Reconcile and publish:** compare against the source of record and existing alternatives. Promote valid observations from an accessible source; keep the last verified value when a later request fails. Record an explicit conflict when values differ beyond a metric-specific tolerance.
6. **Consume:** add a named backend calculation or user-visible view that uses the record, plus a link from the output to the source, publication date and coverage state. Verify that disabling the key returns `unavailable` or falls back cleanly, rather than zero.

### Concrete storage and API shape

| Record | Minimum keys and invariants | Existing code to extend |
| --- | --- | --- |
| Provider endpoint access | `(provider, endpoint, account_tier, effective_from)`; credential state, free-tier availability, quota period/weight, tested date and test outcome. Never infer access from another endpoint. | `config/jobs.py`, `settings.py`, new migration and source governor. |
| Usage ledger | Provider/endpoint, UTC request time, reset window, weight reserved/used, outcome and job ID. A transaction or lock prevents two workers from spending the final credits twice. | `jobs/runner.py`, `jobs/queue.py`, `job` and `job_step`. |
| Canonical identity | Stable issuer/security/listing IDs; provider IDs with valid dates and match evidence. Unresolved mappings have a review state and never silently join by name. | `reference/identity.py`, existing `instrument`, `instrument_identifier`, `listing`, `legal_entity`. |
| Source observation | Domain-specific typed fields plus `source_id`, `provider_endpoint`, `published_at`, `retrieved_at`, `known_at`, observation period, revision and payload hash. Distinct releases never overwrite one another. | Existing `price_bar`, `macro_series`, `fundamentals`, `etf_snapshot` plus narrow new domain tables where needed. |
| Derived metric | Method version, input observation IDs, as-of cutoff, value/unit, coverage denominator, warnings and recompute timestamp. Invalidate when an input or formula changes. | New backend analytics/read layer; never calculate authoritative figures in React. |
| Coverage status | `(entity, metric, period, use_case)` with `available`, `stale`, `missing`, `not_covered`, `paid_only`, `access_unverified`, `quota_exhausted` or `disputed`, and a human-readable reason. | New owner-only read API and Activity/Config UI. |

Add owner-authenticated FastAPI routes under `apps/backend/src/api/` for `GET /data/coverage`, `GET /sources/access` and `GET /sources/usage`. Keep keys and raw provider payloads out of ordinary read responses. Extend `apps/backend/openapi.json` and the frontend's API types using the repository's existing generation path. Tests belong with `apps/backend/tests/test_sources.py`, `test_reference_data.py`, `test_migrations.py` and focused new files; include an offline fixture for every adapter and one integration test for accessible versus denied endpoints.

### Source work packets and allocation

These are candidate calls and consumers; Phase 0 determines which endpoints can be enabled. Scheduling is **event/need driven**, with a conservative default that never assumes an undocumented quota.

| Source | Fetch and normalize first | Derived decision or screen | Refresh and priority |
| --- | --- | --- | --- |
| Alpha Vantage | Existing overview, then test statement endpoints, daily OHLCV, `NEWS_SENTIMENT`, relevant commodity/FX series. Normalize fiscal periods and quote units before comparison. | Fill a missing valuation input; cross-check a held price; article candidate for a thesis; input-cost context. | Reserve the shared 25-call/day budget for held gaps first, then events and watchlist; financials after releases, prices only when primary source fails. |
| EODHD | Test exchange/symbol discovery and one-year EOD for a known listing; reconcile exchange suffix, local session and currency. | Resolve held/watchlist price gaps and source disagreements. | Spend the 20-call/day free allowance on unresolved held listings before research symbols; backfill only requested windows. |
| World Bank | Query indicator metadata and selected annual country series with release/vintage metadata where available. | Long-run country demand, income, debt and population context; portfolio geography narrative. | Release-driven annual refresh, then recompute only exposed holdings. |
| OECD | Pin SDMX dataflows and code lists; parse country, industry, measure, frequency, adjustment and units. | Comparable labour/productivity/inflation and industry context, with a peer-country chart. | Monthly or quarterly by series; cache code lists and avoid full-history polling. |
| FMP | Test exact Basic-key profile, sector/industry, estimate and ETF/ownership endpoints separately. | Accessible US sector comparison or estimate; otherwise a documented coverage gap and own-data comparison. | Probe once in Phase 0, then refresh endpoints actually accessible for free. |
| Diffbot | Resolve organization by domain; pull only relevant KG fields, Extract/NLP source pages and their dates. | Candidate competitor, deal, product, executive and partnership graph; review queue and change feed. | Monthly credit allocation weighted to held names, then watchlist, then discovery; requery changed organizations rather than whole neighborhoods. |
| People Data Labs | Company lookup/enrichment for hard matches and dated firmographic fields; record field-level source and date. | Identity conflict resolution and qualified headcount/funding signal in company research. | Small monthly exception queue; spend credits only on useful company gaps. |
| EPO OPS | Search applicant aliases, retrieve patent families, bibliographic/CPC/citation/legal events, deduplicate family members. | Technology trend and competitive overlap, with a family drill-down. | Incremental weekly/monthly for selected issuers; spend the 4 GB/week allowance only on relevant families. |
| Finnhub | Keep earnings calendar/basic metrics; test peers, surprises, news, ownership, ETF/index and estimates with the free key. | Earnings-event comparisons and peer candidates; accessible ownership/fund data, otherwise explicit gaps. | Calendar near events, metrics after filings; premium-denied endpoints never enter scheduled jobs. |
| Twelve Data | Test symbol search and free quote/FX/reference calls; distinguish global trial symbols from actually accessible markets. | Price/FX discrepancy detection, symbol diagnostics and visible charts for accessible free data. | Exception-first within the free credit budget; no paid corporate-action dependency. |
| BIS | Pin SDMX dataset keys and dimensions for credit, debt-service, banking claims, debt securities and liquidity. | Credit-cycle and bank-sector risk context linked to country/sector exposure. | Release-driven, retaining first-release and revised values. |
| ECB | Extend current SDMX jobs with selected money, loans, deposits, rates and financial-condition series. | Euro-area monetary context and portfolio scenario inputs. | Follow series frequency and release calendar; preserve existing ECB values and revisions. |
| UN Comtrade | Query selected reporter/partner/HS/period slices; store HS revision and value/quantity units. | Product and bilateral trade concentration for mapped company or portfolio exposure. | Periodic slices for mapped exposures only; free registered quota is not a bulk global mirror. |

For the small vendor quotas, implement a **priority queue**, not a fixed request per company: (1) held securities with missing decision-critical data, (2) held securities with a material conflict or upcoming event, (3) watchlist gaps, (4) exploratory coverage. Track demand deferred to the next reset window. Reserve part of each quota for a failure retry or urgent event; make the reserve configurable after the 30-day shadow run. A failed premium probe must not be retried daily.

### Measurable coverage targets

These are proposed *release targets*, not claims about what free sources can provide. Phase 0 records the starting baseline and Phase 8 compares the result by market and asset class.

- **Identity:** 100% of held positions have a reviewed issuer/security/listing path or a visible unresolved exception; no ambiguous match is auto-published.
- **Provenance:** 100% of displayed new facts and derived figures carry source, as-of date, method and access state.
- **Price:** target at least 95% of held market value with a usable latest close and explicit currency/adjustment basis; publish the remaining value as an unknown bucket, not a blended return.
- **Portfolio analytics:** any window labelled complete has reconciled flows and all material price/FX/corporate-action inputs. A partial window displays its missing-value share.
- **Global reach:** publish a dashboard by exchange, country, asset class and metric showing tested symbols, valid records, history length and freshness. Never extrapolate the 30-security audit sample into a global coverage percentage.
- **Cost:** actual spend remains $0; the usage ledger proves no paid endpoint or overage was used.

## Phase 0 — Free access and sample audit

**Dependency:** none. **Outcome:** a tested, zero-cost source matrix and a representative symbol/country corpus.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- No new charts are required in the access spike. Specify the shared frontend states that later phases must support: verified value, stale last-good value, partial coverage, not covered, paid-only, quota exhausted, access unverified, disputed and temporarily unavailable. Show source, as-of date and coverage denominator wherever a view consumes new data.


**Backend**
- [ ] `D0.1` Create `docs/data/provider-access.md` with one row per **endpoint**, not merely provider: free account/key type, authentication, quota/reset timezone, market/history scope, rate limit, tested date, working request, observed response and paid-only denial if applicable. Record the owner's permission as the shared project assumption. Include all 13 named providers.
- [ ] `D0.2` Register free keys only for selected test accounts. Store actual keys in the existing backend secret settings; put only variable names and empty placeholders in `.env.example`. Never log keys or expose them to the browser. Use a zero spending cap where supported; otherwise avoid a payment method and disable upgrades/overages. Confirm a paid-tier denial is a typed `paid_only` result.
- [ ] `D0.3` Build a reproducible fixture of at least 30 securities: US and non-US ordinary shares including a Canadian issuer, an ADR, dual listings, a delisted/renamed line, small caps, and UCITS/US ETFs; add at least 12 countries across OECD and non-OECD regions and 10 HS products. Include current holdings and watchlist items without copying secrets or personal transactions into fixtures.
- [ ] `D0.4` Probe each proposed endpoint with a free key and record `200`, empty, sample-only, `403`, `429`, stale, and symbol mismatch distinctly. Capture actual request weight, not just HTTP call count. For FMP analyst estimates and sector endpoints, Finnhub ownership/ETF/index/forward-estimate endpoints, and Twelve corporate actions, explicitly record the premium denial or usable free response.
- [ ] `D0.5` For FMP, Twelve, UN Comtrade, PDL, EODHD, Alpha Vantage and Finnhub, determine which endpoints actually return usable free data, their coverage, quotas, field limits and refresh costs. Use accessible data in the owner's dashboard; keep paid-only or technically unavailable endpoints as measured gaps.
- [ ] `D0.6` Benchmark match coverage, accuracy and latency against primary filings, issuer fact sheets, Trading212 quotes and official statistical tables. Rank providers by **measured incremental coverage**, not catalog size.
- [ ] `D0.7` Inventory the free *existing* source endpoints as rigorously as the new vendors: SEC submissions, companyfacts, archive, Financial Statement and Notes datasets, 13F/3–5/13D-G/N-PORT/proxy; ESEF/filings.xbrl.org; GLEIF; ESMA FIRDS; Eurostat/FRED/ECB/ČNB; ETF issuer files; issuer IR; UK FCA NSM; EDINET; Companies House; TED; and SEDAR+. Record retrieval method, authentication, lag, version history, rate limits and a tested URL. Find an actual FCA read route rather than mistaking its PIP submission API for one. Treat SEDAR+ as an active access-method pilot under the owner's permission.

**Exit checks:** the matrix has no unknown access state for a production endpoint; every source has a tested free route, a paid-only response or an explicit technical gap; a disabled/missing key never breaks the existing app.


## Phase 1 — Shared identity, provenance, quotas and availability

**Dependency:** Phase 0. **Outcome:** every later adapter writes through one governed pipeline.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Extend the existing reference gaps, job status and freshness patterns into a source-coverage view: a matrix of provider × dataset × held/watchlist entity, with freshness and access state, plus a compact request-budget burn chart and drill-through to the failed endpoint/job. Preserve the current value while a refresh fails. Provide accessible table equivalents and source/evidence links.


**Backend**
- [ ] `D1.1` Add versioned migrations alongside `supabase/migrations/` for canonical `issuer`, `security`, `security_listing`, `provider_identifier`, `provider_access`, `source_observation`, `source_conflict`, and `coverage_status` records. Link existing `instrument`/`instrument_identifier` rows; migrate without replacing Trading212's ticker-based foreign keys in one step. Keep owner-only RLS, explicit `app_backend` privileges and safe indices.
- [ ] `D1.2` Implement a match service using ISIN/LEI/FIGI/CIK/MIC, then names and domains only as suggestions. Score candidates, reject conflicting currencies/share classes, expose unresolved matches in the existing reference-gap UI, and allow an owner override with history.
- [ ] `D1.3` Add an adapter protocol under `apps/backend/src/steward/sources/`: `discover`, `fetch`, `normalize`, `validate`, `coverage`, and `access`. A source returns `available`, `not_covered`, `paid_only`, `access_unverified`, `quota_exhausted`, `temporarily_failed`, or `disputed`; it must not return an empty success for a 403/429.
- [ ] `D1.4` Add one request governor in the worker: daily/monthly credits, per-minute weights, server `Retry-After`, jitter, per-endpoint priority, and a hard stop before any paid threshold. Schedule slow sources once per release and use incremental windows. Keep independent source jobs separately visible in `job`/`job_step`.
- [ ] `D1.5` Add source-specific retention policies driven by analytical need, storage cost and privacy. Archive evidence needed for point-in-time replay; protect keys and personal data; avoid collecting PDL contact/person data unrelated to company analysis. Include provenance and access state in every API response.
- [ ] `D1.6` Extend `config/jobs.py`, the jobs editor, API/OpenAPI contract and generated TypeScript types for provider switches, refresh intervals and quota status. Roll out with all new providers off by default until their sample audit passes.
- [ ] `D1.7` Add cross-source reconciliation for prices, currencies, periods, units and legal entities. Rules select a source of record and preserve alternatives; a disagreement becomes a reviewable item rather than a silent override.
- [ ] `D1.8` Add a shared public-disclosure document/version and evidence layer for existing collectors: accession or regulator ID, stable URL, content hash, filing/acceptance/publication/retrieval timestamps, amendment/supersession links, cited page/table/span and `known_at`. Link current SEC news, financial facts, 13F and insider rows to it without duplicating their domain tables; historical queries select only versions known at the cutoff.
- [ ] `D1.9` Preserve historical listing lifecycle in ESMA FIRDS and GLEIF identity ingestion: cancellations, terminated listings, former names and successor/predecessor links with valid dates. Add regression fixtures for a delisting and renamed issuer so an as-of screen cannot accidentally use a current symbol or entity graph.

**Exit checks:** an ADR/ordinary share pair stays distinct but shares an issuer; a provider 403/429 cannot erase the last good public observation; all new tables pass owner/non-owner/anon RLS tests; historical as-of queries exclude later revisions.


## Phase 2 — Global macro, financial cycle and trade

**Dependency:** Phase 1. **Sources:** World Bank, OECD, BIS, ECB, UN Comtrade. **Outcome:** country and sector exposure can be explained with dated, comparable data.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- For macro, use aligned small-multiple time series with release/revision markers and selectable first-release versus latest-vintage values; compare countries with indexed lines or a ranked slopegraph, not a mixed-unit shared axis. For country exposure use a choropleth only as an overview with a ranked bar/table for precise comparison. For Comtrade, use a bilateral flow Sankey for product→partner mix, paired with a concentration trend and an explicit unknown/unmapped share. Only show issuer impact after an evidence-backed segment/product→country/HS mapping; label reported, estimated and owner-assigned weights differently.


**Backend**
- [ ] `D2.1` Extend `macro_series` or add a versioned observation table so its current source check admits the new providers and stores frequency, seasonal adjustment, unit multiplier, geography, release time and revision/vintage. Do not overwrite earlier published values on update.
- [ ] `D2.2` Add World Bank Indicators API adapter for a curated set: nominal/real GDP, population, income level, government debt, external debt, industry/value-added mix, trade openness and energy dependence. Cache annual releases, surface missing years, and retain indicator metadata and provenance.
- [ ] `D2.3` Add OECD SDMX adapter for inflation, unemployment, productivity, wages, consumer/business confidence, housing and selected industry series. Store code lists and exact transformation definitions; compare overlapping ECB/Eurostat/World Bank observations without assuming they are identical concepts.
- [ ] `D2.4` Expand existing ECB configuration beyond the deposit rate: money/credit aggregates, MFI loan and deposit rates, government yield curves, lending survey/financial-condition series where public. Use the ECB API's revision/history support and distinguish actual ECB observations from calculated growth rates.
- [ ] `D2.5` Add BIS SDMX v2 adapter for credit to non-financial sector, debt service ratios, international banking claims, international debt securities, global liquidity and property-price series. Map residence/ultimate-risk and borrower-sector definitions explicitly; attach release calendars and citation strings.
- [ ] `D2.6` Add UN Comtrade API adapter for selected reporter–partner–HS–period slices, with HS revision mapping and quantity/value units. Compute export/import growth, partner concentration, revealed trade dependence and changes in product mix. Do **not** infer a named issuer's supply contract from a national trade row.
- [ ] `D2.7` Define a separate, owner-editable mapping from company segment/product/geography to country or HS exposure. Keep `reported`, `estimated`, and `user_assigned` weights distinct and sum unknown exposure into an explicit remainder.
- [ ] `D2.8` Build country and industry context cards for holding research and portfolio exposure: latest value, five/ten-year range, release/as-of date, comparable countries, sources, and “how this could affect this holding” tied to the mapped exposure. Macro alerts trigger only on genuinely new observations, not revisions of old periods.
- [ ] `D2.9` Correct the existing FRED vintage assumption: fetch real-time/vintage observations (ALFRED/FRED API where available), store publication/revision windows and distinguish first release from later revision. Archive each Eurostat response from activation onward because its live API does not reconstruct past releases; show an explicit `vintage_unavailable` state for earlier dates. Test a deliberately revised series against an as-of backtest.
- [ ] `D2.10` Audit existing FRED/Eurostat/ČNB/ECB series coverage against actual holdings exposure. Add only selected industry production, energy, yield/credit, exchange-rate and wage series with a named company or portfolio consumer; normalize units and seasonal adjustment, then show how macro changes affect mapped segments rather than generic country commentary.

**Exit checks:** one recession/credit example, one currency/rate example and one HS trade example can be traced from source observation through exposure mapping to an affected holding; zero-exposure and unknown-exposure paths do not manufacture a company effect. UN Comtrade data appears after its free API access and quota behavior pass the Phase 0 test.


## Phase 3 — Price coverage and corporate-action truth

**Dependency:** Phase 1. **Sources:** EODHD, Alpha Vantage, Twelve Data, Finnhub, existing Stooq/Yahoo/Trading212. **Outcome:** defensible price and return history for the investable portfolio.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Extend price/risk views with a candlestick/volume chart and corporate-action/event markers; use an event-study panel around splits/dividends only after adjusted/raw series are reconciled. Show cross-holding correlations as a clustered diverging heatmap with selectable windows and shared-observation counts. Surface venue-session coverage as a calendar/coverage strip so genuine missing trading days stand out from holidays. All views retain currency, adjustment basis, source and as-of context.


**Backend**
- [ ] `D3.1` Expand the existing `price_bar` source constraint and metadata with canonical listing, raw versus adjusted type, quote unit (`GBP` versus `GBX`), MIC, exchange-local session date, UTC fetch time, source timestamp, and access state. Never splice differently adjusted series under one symbol.
- [ ] `D3.2` Use EODHD's free one-year EOD data only for high-value unresolved held/watchlist listings, after its exchange symbol list confirms the suffix. Spend no more than its free 20-call daily budget. Use it as fallback/corroboration, not the historical source for a global screener.
- [ ] `D3.3` Evaluate Alpha Vantage daily OHLCV and available commodity/FX series within its 25-call daily budget. Assign calls first to holdings without a usable primary price, then to source disagreements. Retain the existing Company Overview job's share of that budget.
- [ ] `D3.4` Probe Twelve Data Basic by market/reference endpoint. Treat global trial symbols as tests; wire visible charts only to endpoints that return the needed free data for actual portfolio symbols. Probe Finnhub free quotes by exchange and record technical denials separately from missing market coverage.
- [ ] `D3.5` Create a corporate-action ledger for splits, cash/stock dividends, symbol changes, mergers, spin-offs and delistings. Use company/exchange/filing or issuer records first; compare vendor actions only if a free endpoint is accessible. Twelve `/splits` and `/dividends` are paid-tier endpoints, so record that gap rather than planning them as a free source.
- [ ] `D3.6` Implement adjusted-close and reinvested total-return series as separate derived artifacts. Reconcile split factors, dividend currency, ex-date, withholding assumptions and FX dates. When an action is missing, mark total return unavailable and keep price return visible.
- [ ] `D3.7` Add price-source diagnostics in the holding view: source, exchange, quote currency, adjustment basis, last session, disagreements, and days missing. Add a per-portfolio completeness score for return windows and benchmarks.
- [ ] `D3.8` Evaluate Alpha Vantage `NEWS_SENTIMENT` on a small set of held/watchlist tickers within the shared 25-call budget. Use ticker/topic relevance and sentiment only to rank candidate articles for the existing news/research pipeline; fetch and cite the original publisher before surfacing a thesis event. Track false matches and repeated syndicated stories.
- [ ] `D3.9` Use accessible free Alpha Vantage commodity and FX observations in a compact input-cost and currency context panel for companies with a reported or owner-mapped exposure. Show the commodity series, quote units, as-of date and exposure assumption; do not infer an issuer's margin sensitivity from a price move alone.
- [ ] `D3.10` Audit the existing Nager.Date holiday and configured exchange-calendar path. Store confirmed exchange-specific sessions, half-days and exceptional closures separately from national-holiday suggestions; use those dates to evaluate price/FX freshness, return-window completeness and release jobs. Test a national holiday when an exchange trades and an ad hoc exchange closure when the country has no holiday.

**Exit checks:** split/dividend fixtures preserve economic value across the action; GBX/GBP and ADR-ratio fixtures cannot create 100× errors; 1Y return from a fallback source is reproducible; unsupported global markets are labelled, not backfilled from trial symbols.


## Phase 4 — Fundamentals, sectors and the estimates boundary

**Dependency:** Phases 1 and 3. **Sources:** SEC/ESEF baseline, Alpha Vantage, Finnhub, FMP, issuer reports. **Outcome:** comparable company histories and honest forward-looking fields.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Build an as-filed financial explorer with statement line-item small multiples and period/version selectors; use a cash-to-earnings waterfall for cash conversion, a debt-maturity timeline/stacked bars by year and currency, and a segment treemap (area = reported revenue, color = margin/growth only when definitions align). For estimates, use a dated fan/range chart with mean/median/range and analyst count, plus revision slope between comparable snapshots. Separate actual, guidance, consensus and scenario visually and expose restatements and missing coverage.


**Backend**
- [ ] `D4.1` Expand the existing `fundamentals`/`valuation` models from latest per-share figures to a periodized statement fact store: income, cash flow, balance sheet, shares, segments, basis (US GAAP/IFRS), fiscal period, currency, restatement lineage and filing publication time. Reuse SEC/ESEF parsers; never mix reported and adjusted figures in one ratio.
- [ ] `D4.2` Add Alpha Vantage statement/overview probes behind the Phase 0 endpoint gates. Use successful free responses to fill specific missing fields or challenge SEC/ESEF-derived values; preserve provenance and source confidence. Reserve budget for the existing overview fallback.
- [ ] `D4.3` Retain Finnhub basic metrics as a second-source cross-check and earnings calendar as an event source. Evaluate free peers and last-four-quarter surprise data for candidate peers and earnings history. Mark global standardized statements, forward estimates, fund ownership, ETF/index constituents as unavailable on its free plan.
- [ ] `D4.4` Test FMP Basic profile, sector/industry snapshot and `analyst-estimates` endpoints on the Phase 0 corpus. Record symbols, field limits and exact free-key errors. If a sector observation is accessible through the free key, label its US-exchange universe; otherwise compute peer/sector statistics from the app's own covered issuer set. Never label that set “global market average.”
- [ ] `D4.5` Introduce a forward-data taxonomy: `company_guidance`, `owner_scenario`, `published_analyst_consensus`, and `event_calendar_estimate`. Record provider, analyst count, metric, basis, fiscal period, currency, estimate vintage and update time. A missing consensus remains null; the model may explain but not create one.
- [ ] `D4.6` Build peer sets from business lines, region, capitalization and validated competitors. Calculate revenue growth, margins, ROIC, leverage, free-cash-flow conversion and valuation under one documented formula per metric. Display the number of covered peers and excluded peers with reasons.
- [ ] `D4.7` Produce user-facing financial-history and peer cards, an earnings actual-versus-available-estimate card, and a sector comparison with the universe definition. Link each number to its source and show the accounting/fiscal-period caveat.
- [ ] `D4.8` Extend SEC XBRL beyond the current selected facts and one-dimensional revenue. Backfill as-filed statements, segment profit/assets/capex and selected note facts (debt maturities, leases, commitments, taxes, R&D, repurchases, dilution, pensions and customer concentration) from filing instances and the SEC Financial Statement and Notes datasets; retain dimensions, units, text-block citations and restatement lineage. Reconcile every extracted total to the filed statement before valuation or peer use.
- [ ] `D4.9` Extend ESEF parsing to dimensional IFRS facts, issuer extensions and filed versions. For block-tagged notes, extract only cited passages or tables with reviewed units and period; classify a value as unavailable rather than silently treating a whole note block as a structured numerical fact.
- [ ] `D4.10` Add an issuer IR registry for company guidance, publicly posted consensus files and published segment consensus. Version every accessible PDF/XLSX/HTML release, retain analyst-count and metric-basis metadata, and feed parsed figures into actual-versus-consensus, revisions and segment analysis. Broker-level estimates remain absent only where no free accessible source provides them.

**Exit checks:** sample GAAP/IFRS peers cannot be compared without an explicit common metric definition; a restatement changes the current view but does not change an old as-of report; a premium-only forward estimate never appears as an inferred free value.


## Phase 5 — Company graph, leadership and patents

**Dependency:** Phases 1 and 4. **Sources:** Diffbot, PDL, EPO OPS, existing GLEIF/Wikidata/EDGAR. **Outcome:** a sourced business and technology map that can affect a thesis.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Represent legal structure as a time-aware entity graph with edge type, evidence tier and valid dates; keep consolidation/control distinct from ownership. Use a patent landscape matrix (technology class × year, size = families, color = citations or legal status) and a ranked peer/leadership comparison rather than a decorative network. Alias/peer suggestions remain visually distinct from confirmed facts and open the source passage.


**Backend**
- [ ] `D5.1` Add Diffbot organization matching by website/domain and resolved legal name, with one monthly credit pool for holdings/watchlist before wider discovery. Pull competitor, acquisition, funding, leadership, product and parent candidates plus source URLs and dates; spend Extract/NLP calls on relevant primary pages rather than exporting entire graph neighborhoods.
- [ ] `D5.2` Extend `entity_relationship` without weakening the existing GLEIF parent definition. Add typed `partner`, `acquirer`, `acquired`, `investor`, `investment`, `product`, `technology`, and `executive` edges in a new normalized edge model if the old constraint cannot represent them. Include start/end validity, evidence, direction and `suggested`/`confirmed`/`rejected` status. Accounting parent and commercial relationship remain separate types.
- [ ] `D5.3` Use PDL's small free company allowance only where identity, headcount trend or funding remains unresolved. Avoid personal-contact enrichment because it does not serve company analysis. Compare headcount and funding to company filings/announcements; store dated observations and a retrieval audit.
- [ ] `D5.4` Add EPO OPS patent search by applicant aliases and subsidiaries, then reconcile patent families to the canonical issuer. Store priority date, jurisdiction, family, CPC classes, citations, legal status and confidence in ownership; deduplicate one invention filed in many offices. Separate applications, grants, active rights and expired rights.
- [ ] `D5.5` Compute patent trends by CPC/technology, family quality proxies, grant/expiry timing and competitor overlap. Explain that counts/citations are signals rather than patent value or freedom-to-operate opinions.
- [ ] `D5.6` Connect validated deals, leadership changes, products and patents to thesis KPIs and news clusters. A disputed or suggested relationship can prompt review, but cannot automatically trigger a high-confidence thesis breach.
- [ ] `D5.7` Build a company “Business network and innovation” view: evidence-linked edges, time range, source agreement, product/technology map, patent trend, and a compact list of what changed since the last review.
- [ ] `D5.8` Replace the SEC full-text keyword relationship shortcut with passage-level review. Resolve both counterparties and direction, distinguish named from anonymous customers, keep reported revenue/procurement share and fiscal period, and record explicit rejection/contradiction evidence. Build concentration and knock-on portfolio exposure only from confirmed, dated edges; supplement with issuer disclosures and TED award records.
- [ ] `D5.9` Structure material SEC 8-K/6-K items and cited exhibits, merger/proxy/registration documents and issuer releases into deal and financing events with announcement, amendment, approval, closing and termination states. Extract price, consideration mix, ownership, debt/covenants and share issuance when present; show an evidence-linked transaction timeline and recompute dilution, leverage and valuation scenarios only from confirmed terms.
- [ ] `D5.10` Use GLEIF Level 2 accounting consolidation, lifecycle events and issuer announcements to detect parent/successor changes, then reconcile them with share-class identity and deal events. Never promote an accounting parent to a beneficial shareholder or infer a partnership from common branding.
- [ ] `D5.11` Use existing Wikidata QIDs, multilingual aliases, websites, industry/product and leadership links as candidate discovery for international document matching and peer research. Store statement-level provenance/last-check; confirm consequential relationships and executive changes against current issuer or regulator disclosures, retain disputed/superseded claims, and exclude Wikidata-only ownership or financial claims from authoritative analytics.

**Exit checks:** a subsidiary patent is not attributed to a parent without a dated ownership path; a same-name company match stays suggested; a rejected Diffbot edge does not reappear unchanged; a confirmed deal cites primary evidence.


## Phase 6 — Funds, indices, institutional ownership and look-through

**Dependency:** Phases 1, 3 and 4. **Sources:** existing issuer holdings/SEC 13F, Finnhub/FMP free probes, public fund filings. **Outcome:** transparent portfolio exposure rather than claimed full global fund coverage.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Extend portfolio X-Ray using a nested treemap for issuer/sector/geography and a Sankey from portfolio position → fund → underlying issuer, sized by look-through weight; preserve cash, unlooked-through funds and unknown remainder. For ownership, show quarter-by-quarter holder breadth and share changes with a zero-centered diverging bar or slopegraph, and a top-holder rank-flow chart; annotate quarter end and filing lag. Never stack/sum 13F, N-PORT, issuer fund holdings or control stakes as if they were disjoint ownership.


**Backend**
- [ ] `D6.1` Extend `lookthrough/` beyond configured CSPX/VWCE where issuer-published holdings are accessible. Record fund ISIN/share class, holdings as-of date, assets, expense ratio, cash/derivative remainder, coverage and look-through basis. Do not equate an ETF proxy's constituents with an official index history.
- [ ] `D6.2` Test Finnhub ETF profile/holdings, index constituents and institutional ownership plus FMP ETF/ownership endpoints under actual free keys. Their documented premium features remain disabled when denied. Preserve provider adapters as opt-in probes, not required dependencies.
- [ ] `D6.3` Use SEC N-PORT data for US fund-holdings gaps and existing Form 13F for US institutional ownership. Keep distinct reporting lags, investor thresholds and the difference between fund positions and beneficial ownership. Map CUSIP/ISIN/issuer carefully.
- [ ] `D6.4` Aggregate direct shares plus fund holdings by canonical issuer and report top overlap, region/sector/currency exposure, fee drag and “unknown fund weight.” Keep all as-of dates visible; never distribute unknown weight proportionally to known positions.
- [ ] `D6.5` Add a fund/index coverage ledger: official holdings, issuer downloadable data, regulatory filing, proxy fund, unavailable. Benchmarks without total-return or historical constituents should be flagged before performance/backtest use.
- [ ] `D6.6` Add Schedule 13D/G initial filings and amendments as a separate beneficial-ownership/control channel beside existing 13F positions. Parse reporting person/group, class, reported shares/percent, purpose and effective date; reconcile to the issuer's share class, surface stake/intent changes, and never add 13D/G percentages to 13F or fund weights.
- [ ] `D6.7` Enrich the current Forms 3/4/5 pipeline with transaction footnotes, derivatives, amendments, reported plans and economic classification. Compute open-market buys/sells separately from option exercises, grants, gifts and tax withholding; expose insider role, direct/indirect ownership, net-discretionary activity and limitations. Pilot UK PDMR disclosures as a jurisdiction adapter after Phase 0 retrieval tests.
- [ ] `D6.8` Retain enough dated iShares/Vanguard holdings and factsheet metadata for historical look-through and add public SEC N-PORT holdings only where fund identity and report dates reconcile. Show fund overlap, concentration and coverage with reporting lag and unknown assets; use N-CEN/N-CSR or proxy facts only for validated gaps.
- [ ] `D6.9` Audit the existing iShares/Vanguard fund-characteristics collectors for page/GraphQL availability and parser drift. Preserve each issuer's P/E/P/B exclusion/capping rules, fund-versus-benchmark basis and effective date; compare to constituent-weighted metrics only with aligned definitions, then use compatible data in portfolio valuation/X-Ray and alert on real changes rather than parser drift.

**Exit checks:** fund holdings weights plus cash/derivatives/unclassified reconcile to the published snapshot within tolerance; duplicate ADR and ordinary-share exposure is merged at issuer level but traceable to both securities; stale fund holdings are visibly stale.


## Phase 7 — Make the new data usable throughout the app

**Dependency:** Phases 2–6. **Outcome:** every accessible source has a concrete consumer; missing data has an honest UI.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Integrate these views into existing holding, research, Overview, portfolio, digest and Activity surfaces instead of creating parallel dashboards. Add a filterable coverage atlas by dataset/entity/period and a screen builder with universe size, excluded count and missing-data treatment. Every chart needs a plain-language takeaway, units/denominator, source/as-of, freshness, coverage state, keyboard access and a table/list equivalent; clicking a mark drills to evidence.


**Backend**
- [ ] `D7.1` Add `/data/coverage` and `/sources/access` owner-only read APIs with coverage by security, geography, metric, period and source; quota headroom, freshness and last denial. Never return API keys.
- [ ] `D7.2` Extend holding read models with global financial history, country/trade exposure, business network, patents and source disagreements, including explicit gaps and primary-versus-vendor provenance.
- [ ] `D7.3` Extend the portfolio overview with aggregate macro sensitivities (weighted, reported versus inferred), country and product concentration, adjusted-return coverage and fund overlap. Roll up only mapped/covered holdings; display the denominator beside every percentage.
- [ ] `D7.4` Add read-only research APIs for country/industry/company context so deep research and chat cite stored observations. Let AI explain links and uncertainty; keep numeric aggregations in deterministic code. Add “known as of” dates to every report claim.
- [ ] `D7.5` Add event rules for credit stress, trade exposure changes, material patent/legal-status changes and significant confirmed company-graph events. Throttle and deduplicate alerts using existing `news/` and `monitor` machinery. Show a decision trail to the owner.
- [ ] `D7.6` Update backend README, setup guide, OpenAPI and web README with exact source setup, user-visible scope, expected data delays and removal/disable behavior.
- [ ] `D7.7` Expose stable read models for filed-note evidence, separate control/institutional positions, classified insider activity, validated dependencies, deal states and public consensus vintages. Every derived figure carries source passage, known-at date and explicit coverage reason.

**Exit checks:** each of the 13 providers has a documented user-facing or internal quality-control consumer and an access status; owner can follow a portfolio-level insight back to the underlying source; removing any optional key degrades coverage but does not fail portfolio sync.


## Phase 8 — Verification, operations and zero-cost access gate

**Dependency:** all prior phases. **Outcome:** repeatable, auditable operation under free limits.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Run visual acceptance against representative desktop and narrow layouts, dense and sparse coverage, stale/error states and keyboard/screen-reader navigation. Compare chart aggregates against API totals and source-linked records; ensure legends, units, denominators and partial coverage are visible. Record which screens remain unavailable when optional keys/providers are disabled.


**Backend**
- [ ] `D8.1` Add parser fixtures and contract tests for every adapter, including empty/partial responses, schema drift, revised data, 403/429 and quota reset. Use live tests sparingly against an explicit test budget; CI must not need third-party keys.
- [ ] `D8.2` Run a 30-day shadow collection on held/watchlist securities and representative countries. Measure metric coverage, source disagreement, false matches, freshness, quota consumption, failed jobs and value added over existing sources. Publish a sign-off report; keep low-value adapters as dormant, documented gap-fill options rather than spending their free quotas routinely.
- [ ] `D8.3` Reconcile sampled prices, corporate actions, financial statements, fund weights, macro releases and patent families manually against primary sources. Set domain-specific error thresholds before enabling any new alert or valuation conclusion.
- [ ] `D8.4` Test backup/restore and retention, key expiration, source removal and migration rollback. Ensure the app can recompute derived metrics from archived observations and does not retain unnecessary raw payloads indefinitely.
- [ ] `D8.5` Keep a zero-cost access gate: repeat Phase 0 if an endpoint or free tier changes, and disable a collector only for a paid-only response, expired key, unavailable retrieval route or exhausted quota. Licensing is assumed covered by the owner's permission. A zero-cost requirement may leave a feature unavailable rather than silently switching to a paid API.
- [ ] `D8.6` Add audit-specific fixtures and review samples for filing amendments/restatements, XBRL dimension collisions, SEC notes, 13D/G groups, Form 4 non-discretionary codes, false customer/supplier mentions, anonymous customer percentages, deal reversals and stale consensus. Require sampled evidence precision and point-in-time correctness before publishing each derived signal; report measured coverage by jurisdiction and metric.

**Exit checks:** provider spend is $0; no accidental premium endpoint or overage is reachable; coverage and uncertainty are visible; a source outage never changes a previously verified fact into an unsupported claim.


## Handoff to the feature plan

Phase 1 supplies canonical identity and point-in-time provenance. Phases 2–6 supply macro/trade, return, financial, graph/patent and fund/ownership capabilities. [The companion plan](morningstar_features_implementation_plan.md) builds screeners, research pages, X-Ray-style views, portfolio performance and other analytics only on those **actual, accessible, covered** outputs.

## Data still missing after this plan

The 13 sources complement one another, but do not collectively create a complete free global terminal. Track technical, coverage and history gaps in `coverage_status` and in the feature plan:

| Missing or uneven dataset | Best free path already available to the app | Product consequence |
| --- | --- | --- |
| Long, clean global total-return history and complete corporate actions | Exchange/issuer announcements and the existing Stooq/Yahoo path where available; selective Alpha Vantage/EODHD checks. | Return, benchmark and backtest windows can be shorter or partial; never splice price-only and total-return series. |
| Complete point-in-time global listed-company financials, segments and shares | SEC filings, ESEF filings and issuer annual/interim reports; vendor facts only as field-level gap fills. | Deep financial history and cross-market screener coverage are measured, not assumed. |
| Global analyst consensus and revision history | Verified free event estimates or company guidance when available. | Consensus screens, revisions and surprise histories remain unavailable for many securities. Owner scenarios are labelled separately. |
| Full global institutional/insider ownership and short-interest history | SEC 13F/insider filings and jurisdiction-specific official filings added case by case. | US coverage is deeper; non-US ownership cannot be inferred from a provider marketing page. |
| Full historical index membership, constituent weights and official total-return benchmarks | Issuer-published fund holdings or public index factsheets as dated proxies, with provenance. | Historical index attribution/backtesting is disabled without genuine as-of constituents. |
| Global fund holdings/NAV/fees and fixed-income security terms | Issuer disclosures and US N-PORT where available. | Fund comparison/X-Ray shows stale or unknown weight; bond duration/quality only where actually published. |
| Company-level supplier/customer contracts and geographic revenue for all issuers | Reported segments, cited company disclosures and owner-reviewed graph evidence. | UN Comtrade and macro data provide context, not invented company counterparties or revenue weights. |
| Corporate actions and tax treatments across every jurisdiction | Broker ledger, issuer disclosures and owner-confirmed exceptions. | Personal return and gain calculations identify unreconciled events; no unsupported tax filing result. |

The owner can prioritize later free-source additions by **affected portfolio value × decision impact × missing-data severity**, subject to the same free-access and evidence rules. A request for deep global coverage should result in a measured country/exchange coverage map, not a claim that all markets are equally supported.

## Source references

Official provider pages reviewed on 29 September 2026; Phase 0 must check actual free account access again at implementation time. Linked terms are background references, not licensing gates under the owner's stated permission.

- [Alpha Vantage limits](https://www.alphavantage.co/premium/), [API documentation](https://www.alphavantage.co/documentation/), [terms](https://www.alphavantage.co/terms_of_service/)
- [EODHD free EOD endpoint](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes), [limits](https://eodhd.com/financial-apis/api-limits), [commercial-use guidance](https://eodhd.com/financial-apis/commercial-vs-personal-license-use)
- [World Bank API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392), [reuse terms](https://data.worldbank.org/summary-terms-of-use)
- [OECD API](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html), [terms](https://www.oecd.org/en/about/terms-conditions.html)
- [FMP plans and display rights](https://site.financialmodelingprep.com/developer/docs/pricing), [sector data](https://site.financialmodelingprep.com/sector-performance), [estimates endpoint](https://site.financialmodelingprep.com/developer/docs/stable/financial-estimates)
- [Diffbot pricing](https://www.diffbot.com/pricing), [organization fields](https://www.diffbot.com/docs/ontology/organization), [terms](https://www.diffbot.com/company/terms)
- [PDL free company credits](https://www.peopledatalabs.com/data-lab/recipe/custom-audience-generation), [company enrichment](https://www.peopledatalabs.com/company-data/enrichment-api), [subscription agreement](https://privacy.peopledatalabs.com/policies?name=services-subscription-agreement)
- [EPO OPS limits](https://www.epo.org/en/searching-for-patents/data/web-services/ops), [terms](https://www.epo.org/en/service-support/ordering/terms-and-conditions/ops-terms-and-conditions)
- [Finnhub endpoint documentation](https://finnhub.io/docs/api/quote), [commercial offering](https://api.finnhub.io/pricing-startups-and-enterprise)
- [Twelve Data pricing](https://twelvedata.com/pricing), [endpoint documentation](https://twelvedata.com/docs), [terms](https://twelvedata.com/terms)
- [BIS API](https://stats.bis.org/api-doc/v2/), [permitted-use terms](https://data.bis.org/help/legal)
- [ECB API](https://data.ecb.europa.eu/help/api/overview), [reuse policy](https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.nl.html)
- [UN Comtrade free plan](https://uncomtrade.org/docs/subscriptions/), [API](https://uncomtrade.org/docs/un-comtrade-api/), [re-dissemination policy FAQ](https://uncomtrade.org/docs/faqs-on-use-and-re-dissemination/)
