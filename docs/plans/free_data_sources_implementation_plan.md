# StewardAI: free data sources implementation plan

Research snapshot: 29 September 2026. Companion: [Morningstar-style features plan](morningstar_features_implementation_plan.md).

## Goal and constraints

Use every provider named by the owner where its **permanently free entitlement** permits a useful workflow. A free trial, sample response, marketing page, or paid-only endpoint does not count as production coverage. The present target is the existing **single-user, self-hosted** StewardAI app. Before any multi-user, public, or commercial launch, re-run the display, redistribution, storage, and derived-data checks in Phase 0. A paid entitlement is never silently assumed and no job may incur overage charges.

“Use” means a visible decision or analysis: a country or company exposure, a defensible price and return, a peer comparison, a research finding, a thesis check, or a coverage gap. Raw ingestion without one of those consumers does not complete a task.

### Starting point in this repository

- `apps/backend/src/steward/sources/alphavantage.py` already fetches Company Overview as a valuation fallback; `sources/finnhub.py` supplies earnings dates and basic financial ratios. `valuation/collect.py` combines these with SEC/ESEF and issuer fund data.
- `macro/collect.py` already loads ECB policy rates, Eurostat, FRED, and ČNB. `reference/prices.py`, `sources/market.py`, and `reference/intraday.py` use Stooq and Yahoo for history and quotes.
- `lookthrough/` stores iShares/Vanguard holdings; `ownership/` collects SEC 13F and insider data. `reference/identity.py` and the `instrument`, `listing`, `legal_entity`, and `entity_relationship` tables resolve the current portfolio.
- `portfolio/` already supplies snapshots, transactions, drift, some risk and contribution analytics. The frontend has valuation, exposure, look-through, price, risk, and ownership panels. Preserve and extend those paths instead of creating parallel screens or duplicate truth tables.
- The current database is optimized for **held tickers**. A wider research universe needs new identity and coverage records, while keeping the existing Trading212 ticker as an alias rather than a global primary key.

## Provider allocation and actual use

| Provider | Maximum useful free role | Product-facing use after verification | Boundary to retain |
| --- | --- | --- | --- |
| Alpha Vantage | Existing `OVERVIEW` fallback; selectively test free financial statements, daily bars, news sentiment, commodities and FX. | Fill a missing owner-held valuation input; corroborate a price or commodity exposure; link a news event to its original article. | 25 standard calls/day; endpoint-specific premium flags; standard terms are personal and noncommercial. Never present sentiment as a verified fact. |
| EODHD | Free one-year EOD bars and exchange/symbol discovery. | Cover a small number of held/watchlist listings that Stooq/Yahoo miss; compare close and exchange currency. | 20 calls/day and one-year history; no global historical backfill or public display promise on the free plan. |
| World Bank | Keyless development and country indicators. | Country cards, economic context, demand-market and sovereign exposure in research/portfolio views. | Often annual and lagged; inspect each indicator's third-party rights. |
| OECD | Free SDMX country and industry statistics. | Labour, productivity, inflation, trade and sector context with comparable definitions and release dates. | Uneven country coverage; respect dataset-specific rights and rate limits. |
| FMP | Free Basic endpoint experiments for company profiles, US sector/industry snapshots, and analyst estimates. | Show a US sector comparison or estimate **only if the exact free key returns that endpoint and display rights are confirmed**; otherwise use it internally to identify data gaps. | 250 calls/day; global coverage is paid; data display/redistribution needs a separate agreement. An API documentation example is not proof of free entitlement. |
| Diffbot | 10,000 monthly credits of Extract, KG, NLP and search. | Suggest competitors, leaders, products, funding, deals, partnerships and company relationships; cite source pages; feed the review queue. | About 25 credits per exported KG entity, fields vary; never turn a suggested relationship into a verified ownership claim without primary corroboration. |
| People Data Labs | Roughly 100 free company-enrichment credits/month; free supporting lookup/cleaning subject to limits. | Resolve hard company matches; attach dated headcount, employee-growth or funding clues to a research profile where use rights allow. | Small budget; avoid person contact data and personal profiles; third-party distribution is restricted. |
| EPO OPS | Up to 4 GB/week of bibliographic, patent-family, citation and legal-status data. | Patent portfolio, technology concentration and competitive-intensity sections for relevant companies. | Applicant names and assignees need entity resolution; do not republish a raw patent database. |
| Finnhub | Existing calendar, US news and basic metrics; test available peers and recent earnings surprises. | Estimate-versus-actual at the event level, peer candidates, corroborative ratio checks and event alerts. | Dedicated forward estimates, standardized global statements, full ownership, ETF holdings and index constituents are marked premium. |
| Twelve Data | Basic free US/FX/crypto/reference queries and a small set of global trial symbols. | Internal cross-check and symbol diagnostics where permitted; do not depend on it for global visible prices. | Basic is internal non-display; global EOD and `/dividends`/`/splits` are paid; free tier prohibits commercial use. |
| BIS | Free public SDMX banking, credit, debt-securities and liquidity series. | Financial-cycle dashboard and country/bank-sector risk context. | Aggregate geography/sector data, not company financials; follow its attribution and commercial-inclusion conditions. |
| ECB | Expand the existing free SDMX integration beyond policy rates/FX. | Euro-area credit, deposits, monetary aggregates, lending rates and financial-condition context. | Preserve original observations and label transformations/revisions; third-party and confidential datasets differ. |
| UN Comtrade | Registered free API: 500 calls/day, up to 100,000 records/call. | Bilateral product-level trade trends and country/sector exposure for private research. | Trade rows do not identify company customers/suppliers. Public/commercial re-dissemination or derived-data service requires a separate premium-license decision. |

The endpoint and license facts behind this table are linked in [Source references](#source-references). Phase 0 verifies them with actual free accounts before writing collectors.

## Cross-source data contract

Implement this once before adding collectors. It lets the app expose uncertainty instead of pretending providers agree.

1. **Identity:** `issuer_id`/`legal_entity_id` for the business, `security_id` for a share class or fund, `listing_id` for an exchange line, and `provider_symbol` for each vendor. Store ISIN, LEI, FIGI, CIK, MIC and trading currency with valid-from/to dates and match evidence. Never merge an ADR, ordinary share, ETF, or index merely because tickers match.
2. **Observation:** domain, metric, canonical entity/security/country, period start/end, fiscal period, value, unit, scale, currency, source, source URL/document, publisher timestamp, retrieval timestamp, valid time, vintage/revision, transformation version, and source priority. Preserve null/unknown distinctly from zero.
3. **Entitlement:** provider + endpoint + account tier + permitted uses (`private_display`, `commercial_display`, `storage`, `redistribution`, `derived_output`) + quota/weight + expiry/review date. An absent permission is a closed gate, not an optimistic default.
4. **Evidence and quality:** original public-source payload or permitted excerpt, normalized record, validator results, disagreements, confidence and owner decision. Keep restricted vendor payloads out of long-lived storage when its terms prohibit caching; retain a non-sensitive fetch audit and a source link instead.
5. **Coverage:** expected universe, matched securities, available periods, freshness, permitted-use status and reason for every gap. Expose coverage at metric, provider, country, fund and portfolio levels.
6. **Point-in-time:** queries for screens/backtests/reports must only use records known by the requested as-of date. Keep revisions and restatements; never backfill future knowledge into a historical decision.

### Implementation contract for every provider

The following is the work required *inside* each `D2`–`D6` adapter, not an optional second pass:

1. **Endpoint manifest:** record exact path, parameters, pagination, timezone, response units, free-plan evidence, request cost, allowed use, update cadence and sample fixture. A documented endpoint without a successful free-key test is marked `not_entitled`.
2. **Discovery and identity:** map the provider's symbol or country/product code to the canonical issuer, security, listing, geography or HS revision. Persist the mapping decision and rejected candidates. A ticker-only company match cannot publish a fact.
3. **Fetch and budget:** reserve credits atomically before each call; use conditional requests/incremental dates where supported. Record response status, provider request ID where available, bytes/credits, retry time and a redacted error. Do not retry a hard entitlement denial.
4. **Normalize and validate:** parse into typed records; check dates, currencies, unit scales, balance identities, plausible ranges, uniqueness and the source's own metadata. Quarantine unexpected schema changes or an unbalanced series before publication.
5. **Reconcile and publish:** compare against the source of record and existing alternatives. Promote only a valid observation with permitted output rights; keep the last verified value when a later request fails. Record an explicit conflict when values differ beyond a metric-specific tolerance.
6. **Consume:** add a named backend calculation or user-visible view that uses the record, plus a link from the output to the source, publication date and coverage state. Verify that disabling the key returns `unavailable` or falls back cleanly, rather than zero.

### Concrete storage and API shape

| Record | Minimum keys and invariants | Existing code to extend |
| --- | --- | --- |
| Provider endpoint entitlement | `(provider, endpoint, account_tier, effective_from)`; permission flags, quota period/weight, terms URL, tested date and test outcome. Never infer rights from another endpoint. | `config/jobs.py`, `settings.py`, new migration and source governor. |
| Usage ledger | Provider/endpoint, UTC request time, reset window, weight reserved/used, outcome and job ID. A transaction or lock prevents two workers from spending the final credits twice. | `jobs/runner.py`, `jobs/queue.py`, `job` and `job_step`. |
| Canonical identity | Stable issuer/security/listing IDs; provider IDs with valid dates and match evidence. Unresolved mappings have a review state and never silently join by name. | `reference/identity.py`, existing `instrument`, `instrument_identifier`, `listing`, `legal_entity`. |
| Source observation | Domain-specific typed fields plus `source_id`, `provider_endpoint`, `published_at`, `retrieved_at`, `known_at`, observation period, revision, rights tag and payload hash. Distinct releases never overwrite one another. | Existing `price_bar`, `macro_series`, `fundamentals`, `etf_snapshot` plus narrow new domain tables where needed. |
| Derived metric | Method version, input observation IDs, as-of cutoff, value/unit, coverage denominator, warnings and recompute timestamp. Invalidate when an input or formula changes. | New backend analytics/read layer; never calculate authoritative figures in React. |
| Coverage status | `(entity, metric, period, use_case)` with `available`, `stale`, `missing`, `not_covered`, `not_entitled`, `quota_exhausted` or `disputed`, and a human-readable reason. | New owner-only read API and Activity/Config UI. |

Add owner-authenticated FastAPI routes under `apps/backend/src/api/` for `GET /data/coverage`, `GET /sources/entitlements` and `GET /sources/usage`. Keep keys and restricted payloads out of all responses. Extend `apps/backend/openapi.json` and the frontend's API types using the repository's existing generation path. Tests belong with `apps/backend/tests/test_sources.py`, `test_reference_data.py`, `test_migrations.py` and focused new files; include an offline fixture for every adapter and one integration test for the permitted/denied UI boundary.

### Source work packets and allocation

These are candidate calls and consumers; Phase 0 determines which endpoints can be enabled. Scheduling is **event/need driven**, with a conservative default that never assumes an undocumented quota.

| Source | Fetch and normalize first | Derived decision or screen | Refresh and priority |
| --- | --- | --- | --- |
| Alpha Vantage | Existing overview, then test statement endpoints, daily OHLCV, `NEWS_SENTIMENT`, relevant commodity/FX series. Normalize fiscal periods and quote units before comparison. | Fill a missing valuation input; cross-check a held price; article candidate for a thesis; input-cost context. | Reserve the shared 25-call/day budget for held gaps first, then events and watchlist; financials after releases, prices only when primary source fails. |
| EODHD | Test exchange/symbol discovery and one-year EOD for a known listing; reconcile exchange suffix, local session and currency. | Resolve held/watchlist price gaps and source disagreements. | Spend the 20-call/day free allowance on unresolved held listings before research symbols; backfill only requested windows. |
| World Bank | Query indicator metadata and selected annual country series with release/vintage metadata where available. | Long-run country demand, income, debt and population context; portfolio geography narrative. | Release-driven annual refresh, then recompute only exposed holdings. |
| OECD | Pin SDMX dataflows and code lists; parse country, industry, measure, frequency, adjustment and units. | Comparable labour/productivity/inflation and industry context, with a peer-country chart. | Monthly or quarterly by series; cache code lists and avoid full-history polling. |
| FMP | Test exact Basic-key profile, sector/industry, estimate and ETF/ownership endpoints separately. | Licensed US sector comparison or an available estimate; otherwise a documented coverage gap and possibly a permitted internal comparison. | Probe once in Phase 0, then refresh only endpoints with explicit free entitlement and use rights. |
| Diffbot | Resolve organization by domain; pull only relevant KG fields, Extract/NLP source pages and their dates. | Candidate competitor, deal, product, executive and partnership graph; review queue and change feed. | Monthly credit allocation weighted to held names, then watchlist, then discovery; requery changed organizations rather than whole neighborhoods. |
| People Data Labs | Company lookup/enrichment for hard matches and dated firmographic fields; record field-level permitted use. | Identity conflict resolution and qualified headcount/funding signal in company research. | Small monthly exception queue only; do not enrich people or routinely mirror company profiles. |
| EPO OPS | Search applicant aliases, retrieve patent families, bibliographic/CPC/citation/legal events, deduplicate family members. | Technology trend and competitive overlap, with a family drill-down. | Incremental weekly/monthly for selected issuers; spend the 4 GB/week allowance only on relevant families. |
| Finnhub | Keep earnings calendar/basic metrics; test peers, surprises, news, ownership, ETF/index and estimates with the free key. | Earnings-event comparisons and peer candidates; optional permitted ownership/fund data, otherwise explicit gaps. | Calendar near events, metrics after filings; premium-denied endpoints never enter scheduled jobs. |
| Twelve Data | Test symbol search and allowable quote/FX/reference calls; distinguish global trial symbols from entitled markets. | Internal price/FX discrepancy detection and symbol diagnostics. | Exception-only within the free credit budget; no unlicensed chart or corporate-action dependency. |
| BIS | Pin SDMX dataset keys and dimensions for credit, debt-service, banking claims, debt securities and liquidity. | Credit-cycle and bank-sector risk context linked to country/sector exposure. | Release-driven, retaining first-release and revised values. |
| ECB | Extend current SDMX jobs with selected money, loans, deposits, rates and financial-condition series. | Euro-area monetary context and portfolio scenario inputs. | Follow series frequency and release calendar; preserve existing ECB values and revisions. |
| UN Comtrade | Query selected reporter/partner/HS/period slices; store HS revision and value/quantity units. | Product and bilateral trade concentration for mapped company or portfolio exposure. | Periodic slices for mapped exposures only; free registered quota is not a bulk global mirror. |

For the small commercial-provider quotas, implement a **priority queue**, not a fixed request per company: (1) held securities with missing decision-critical data, (2) held securities with a material conflict or upcoming event, (3) watchlist gaps, (4) exploratory coverage. Track demand deferred to the next reset window. Reserve part of each quota for a failure retry or urgent event; make the reserve configurable after the 30-day shadow run. A failed premium probe must not be retried daily.

### Measurable coverage targets

These are proposed *release targets*, not claims about what free sources can provide. Phase 0 records the starting baseline and Phase 8 compares the result by market and asset class.

- **Identity:** 100% of held positions have a reviewed issuer/security/listing path or a visible unresolved exception; no ambiguous match is auto-published.
- **Provenance:** 100% of displayed new facts and derived figures carry source, as-of date, method and entitlement state.
- **Price:** target at least 95% of held market value with a usable latest close and explicit currency/adjustment basis; publish the remaining value as an unknown bucket, not a blended return.
- **Portfolio analytics:** any window labelled complete has reconciled flows and all material price/FX/corporate-action inputs. A partial window displays its missing-value share.
- **Global reach:** publish a dashboard by exchange, country, asset class and metric showing tested symbols, valid records, history length and freshness. Never extrapolate the 30-security audit sample into a global coverage percentage.
- **Cost:** actual spend remains $0; the usage ledger proves no paid endpoint, overage or unlicensed export was used.

## Phase 0 — Entitlement and sample audit

**Dependency:** none. **Outcome:** a tested, zero-cost source matrix and a representative symbol/country corpus.

- [ ] `D0.1` Create `docs/data/provider-entitlements.md` with one row per **endpoint**, not merely provider: free account type, endpoint, quota and reset timezone, market/history scope, private display, storage, derivative and redistribution rights, rate limit, tested date, and authoritative terms URL. Include all 13 named providers.
- [ ] `D0.2` Register free keys only for selected test accounts. Store actual keys in the existing backend secret settings; put only variable names and empty placeholders in `.env.example`. Never log keys or expose them to the browser. Use a zero spending cap where supported; otherwise avoid a payment method and disable upgrades/overages. Confirm a free-tier denial is a typed `not_entitled` result.
- [ ] `D0.3` Build a reproducible fixture of at least 30 securities: US and non-US ordinary shares, an ADR, dual listings, a delisted/renamed line, small caps, and UCITS/US ETFs; add at least 12 countries across OECD and non-OECD regions and 10 HS products. Include current holdings and watchlist items without copying secrets or personal transactions into fixtures.
- [ ] `D0.4` Probe each proposed endpoint with a free key and record `200`, empty, sample-only, `403`, `429`, stale, and symbol mismatch distinctly. Capture actual request weight, not just HTTP call count. For FMP analyst estimates and sector endpoints, Finnhub ownership/ETF/index/forward-estimate endpoints, and Twelve corporate actions, explicitly record the premium denial or usable free response.
- [ ] `D0.5` Decide whether each source may be used for private display, internal cross-check only, or public/commercial display. Add a release checklist that blocks public features using FMP, Twelve, UN Comtrade, PDL, EODHD, Alpha Vantage or Finnhub data without the needed rights. Do not substitute scraped pages to evade an endpoint restriction.
- [ ] `D0.6` Benchmark match coverage, accuracy and latency against primary filings, issuer fact sheets, Trading212 quotes and official statistical tables. Rank providers by **measured incremental coverage**, not catalog size.

**Exit checks:** the matrix has no unknown entitlement for a production endpoint; every source has either a permitted role or an explicit `blocked_free_tier` state; a disabled/missing key never breaks the existing app.

## Phase 1 — Shared identity, provenance, quotas and availability

**Dependency:** Phase 0. **Outcome:** every later adapter writes through one governed pipeline.

- [ ] `D1.1` Add versioned migrations alongside `supabase/migrations/` for canonical `issuer`, `security`, `security_listing`, `provider_identifier`, `provider_entitlement`, `source_observation`, `source_conflict`, and `coverage_status` records. Link existing `instrument`/`instrument_identifier` rows; migrate without replacing Trading212's ticker-based foreign keys in one step. Keep owner-only RLS, explicit `app_backend` privileges and safe indices.
- [ ] `D1.2` Implement a match service using ISIN/LEI/FIGI/CIK/MIC, then names and domains only as suggestions. Score candidates, reject conflicting currencies/share classes, expose unresolved matches in the existing reference-gap UI, and allow an owner override with history.
- [ ] `D1.3` Add an adapter protocol under `apps/backend/src/steward/sources/`: `discover`, `fetch`, `normalize`, `validate`, `coverage`, and `entitlement`. A source returns `available`, `not_covered`, `not_entitled`, `quota_exhausted`, `temporarily_failed`, or `disputed`; it must not return an empty success for a 403/429.
- [ ] `D1.4` Add one request governor in the worker: daily/monthly credits, per-minute weights, server `Retry-After`, jitter, per-endpoint priority, and a hard stop before any paid threshold. Schedule slow sources once per release and use incremental windows. Keep independent source jobs separately visible in `job`/`job_step`.
- [ ] `D1.5` Add source-specific retention policies. Archive public/first-party evidence where allowed; protect keys and personal data; do not copy PDL person records or unrestricted vendor raw dumps into Supabase. Include provenance and data-rights tags in every API response.
- [ ] `D1.6` Extend `config/jobs.py`, the jobs editor, API/OpenAPI contract and generated TypeScript types for provider switches, refresh intervals and quota status. Roll out with all new providers off by default until their sample audit passes.
- [ ] `D1.7` Add cross-source reconciliation for prices, currencies, periods, units and legal entities. Rules select a source of record and preserve alternatives; a disagreement becomes a reviewable item rather than a silent override.

**Exit checks:** an ADR/ordinary share pair stays distinct but shares an issuer; a provider 403/429 cannot erase the last good public observation; all new tables pass owner/non-owner/anon RLS tests; historical as-of queries exclude later revisions.

## Phase 2 — Global macro, financial cycle and trade

**Dependency:** Phase 1. **Sources:** World Bank, OECD, BIS, ECB, UN Comtrade. **Outcome:** country and sector exposure can be explained with dated, comparable data.

- [ ] `D2.1` Extend `macro_series` or add a versioned observation table so its current source check admits the new providers and stores frequency, seasonal adjustment, unit multiplier, geography, release time and revision/vintage. Do not overwrite earlier published values on update.
- [ ] `D2.2` Add World Bank Indicators API adapter for a curated set: nominal/real GDP, population, income level, government debt, external debt, industry/value-added mix, trade openness and energy dependence. Cache annual releases, surface missing years, and retain indicator metadata/license flags.
- [ ] `D2.3` Add OECD SDMX adapter for inflation, unemployment, productivity, wages, consumer/business confidence, housing and selected industry series. Store code lists and exact transformation definitions; compare overlapping ECB/Eurostat/World Bank observations without assuming they are identical concepts.
- [ ] `D2.4` Expand existing ECB configuration beyond the deposit rate: money/credit aggregates, MFI loan and deposit rates, government yield curves, lending survey/financial-condition series where public. Use the ECB API's revision/history support and distinguish actual ECB observations from calculated growth rates.
- [ ] `D2.5` Add BIS SDMX v2 adapter for credit to non-financial sector, debt service ratios, international banking claims, international debt securities, global liquidity and property-price series. Map residence/ultimate-risk and borrower-sector definitions explicitly; attach release calendars and citation strings.
- [ ] `D2.6` Add UN Comtrade API adapter for selected reporter–partner–HS–period slices, with HS revision mapping and quantity/value units. Compute export/import growth, partner concentration, revealed trade dependence and changes in product mix. Do **not** infer a named issuer's supply contract from a national trade row.
- [ ] `D2.7` Define a separate, owner-editable mapping from company segment/product/geography to country or HS exposure. Keep `reported`, `estimated`, and `user_assigned` weights distinct and sum unknown exposure into an explicit remainder.
- [ ] `D2.8` Build country and industry context cards for holding research and portfolio exposure: latest value, five/ten-year range, release/as-of date, comparable countries, sources, and “how this could affect this holding” tied to the mapped exposure. Macro alerts trigger only on genuinely new observations, not revisions of old periods.

**Exit checks:** one recession/credit example, one currency/rate example and one HS trade example can be traced from source observation through exposure mapping to an affected holding; zero-exposure and unknown-exposure paths do not manufacture a company effect. A commercial release test excludes UN Comtrade-derived displays unless an appropriate subscription/license is recorded.

## Phase 3 — Price coverage and corporate-action truth

**Dependency:** Phase 1. **Sources:** EODHD, Alpha Vantage, Twelve Data, Finnhub, existing Stooq/Yahoo/Trading212. **Outcome:** defensible price and return history for the investable portfolio.

- [ ] `D3.1` Expand the existing `price_bar` source constraint and metadata with canonical listing, raw versus adjusted type, quote unit (`GBP` versus `GBX`), MIC, exchange-local session date, UTC fetch time, source timestamp, and entitlement. Never splice differently adjusted series under one symbol.
- [ ] `D3.2` Use EODHD's free one-year EOD data only for high-value unresolved held/watchlist listings, after its exchange symbol list confirms the suffix. Spend no more than its free 20-call daily budget. Use it as fallback/corroboration, not the historical source for a global screener.
- [ ] `D3.3` Evaluate Alpha Vantage daily OHLCV and available commodity/FX series within its 25-call daily budget. Assign calls first to holdings without a usable primary price, then to source disagreements. Retain the existing Company Overview job's share of that budget.
- [ ] `D3.4` Probe Twelve Data Basic for explicitly allowed market/reference cases. Treat global trial symbols as tests and its free non-display data as internal validation only; do not wire a user-visible global chart to an unlicensed endpoint. Probe Finnhub free quotes only where entitlement and exchange rules permit.
- [ ] `D3.5` Create a corporate-action ledger for splits, cash/stock dividends, symbol changes, mergers, spin-offs and delistings. Use company/exchange/filing or issuer records first; compare vendor actions only if a free entitlement exists. Twelve `/splits` and `/dividends` are paid-tier endpoints, so record that gap rather than planning them as a free source.
- [ ] `D3.6` Implement adjusted-close and reinvested total-return series as separate derived artifacts. Reconcile split factors, dividend currency, ex-date, withholding assumptions and FX dates. When an action is missing, mark total return unavailable and keep price return visible.
- [ ] `D3.7` Add price-source diagnostics in the holding view: source, exchange, quote currency, adjustment basis, last session, disagreements, and days missing. Add a per-portfolio completeness score for return windows and benchmarks.
- [ ] `D3.8` Evaluate Alpha Vantage `NEWS_SENTIMENT` on a small set of held/watchlist tickers within the shared 25-call budget. Use ticker/topic relevance and sentiment only to rank candidate articles for the existing news/research pipeline; fetch and cite the original publisher before surfacing a thesis event. Track false matches and repeated syndicated stories.
- [ ] `D3.9` Use permitted Alpha Vantage commodity and FX observations in a compact input-cost and currency context panel for companies with a reported or owner-mapped exposure. Show the commodity series, quote units, as-of date and exposure assumption; do not infer an issuer's margin sensitivity from a price move alone.

**Exit checks:** split/dividend fixtures preserve economic value across the action; GBX/GBP and ADR-ratio fixtures cannot create 100× errors; 1Y return from a fallback source is reproducible; unsupported global markets are labelled, not backfilled from trial symbols.

## Phase 4 — Fundamentals, sectors and the estimates boundary

**Dependency:** Phases 1 and 3. **Sources:** SEC/ESEF baseline, Alpha Vantage, Finnhub, FMP, issuer reports. **Outcome:** comparable company histories and honest forward-looking fields.

- [ ] `D4.1` Expand the existing `fundamentals`/`valuation` models from latest per-share figures to a periodized statement fact store: income, cash flow, balance sheet, shares, segments, basis (US GAAP/IFRS), fiscal period, currency, restatement lineage and filing publication time. Reuse SEC/ESEF parsers; never mix reported and adjusted figures in one ratio.
- [ ] `D4.2` Add Alpha Vantage statement/overview probes behind the Phase 0 endpoint gates. Use successful free responses to fill specific missing fields or challenge SEC/ESEF-derived values; preserve provenance and source confidence. Reserve budget for the existing overview fallback.
- [ ] `D4.3` Retain Finnhub basic metrics as a second-source cross-check and earnings calendar as an event source. Evaluate free peers and last-four-quarter surprise data for candidate peers and earnings history. Mark global standardized statements, forward estimates, fund ownership, ETF/index constituents as unavailable on its free plan.
- [ ] `D4.4` Test FMP Basic profile, sector/industry snapshot and `analyst-estimates` endpoints on the Phase 0 corpus. Record symbols, field limits and exact free-key errors. If a sector observation is licensed for the private UI, label its US-exchange universe; otherwise compute peer/sector statistics from the app's own covered issuer set. Never label that set “global market average.”
- [ ] `D4.5` Introduce a forward-data taxonomy: `company_guidance`, `owner_scenario`, `published_analyst_consensus`, and `event_calendar_estimate`. Record provider, analyst count, metric, basis, fiscal period, currency, estimate vintage, update time and use permission. A missing consensus remains null; the model may explain but not create one.
- [ ] `D4.6` Build peer sets from business lines, region, capitalization and validated competitors. Calculate revenue growth, margins, ROIC, leverage, free-cash-flow conversion and valuation under one documented formula per metric. Display the number of covered peers and excluded peers with reasons.
- [ ] `D4.7` Produce user-facing financial-history and peer cards, an earnings actual-versus-available-estimate card, and a sector comparison with the universe definition. Link each number to its filed or permitted source and show the accounting/fiscal-period caveat.

**Exit checks:** sample GAAP/IFRS peers cannot be compared without an explicit common metric definition; a restatement changes the current view but does not change an old as-of report; a premium-only forward estimate never appears as an inferred free value.

## Phase 5 — Company graph, leadership and patents

**Dependency:** Phases 1 and 4. **Sources:** Diffbot, PDL, EPO OPS, existing GLEIF/Wikidata/EDGAR. **Outcome:** a sourced business and technology map that can affect a thesis.

- [ ] `D5.1` Add Diffbot organization matching by website/domain and resolved legal name, with one monthly credit pool for holdings/watchlist before wider discovery. Pull competitor, acquisition, funding, leadership, product and parent candidates plus source URLs and dates; spend Extract/NLP calls on relevant primary pages rather than exporting entire graph neighborhoods.
- [ ] `D5.2` Extend `entity_relationship` without weakening the existing GLEIF parent definition. Add typed `partner`, `acquirer`, `acquired`, `investor`, `investment`, `product`, `technology`, and `executive` edges in a new normalized edge model if the old constraint cannot represent them. Include start/end validity, evidence, direction and `suggested`/`confirmed`/`rejected` status. Accounting parent and commercial relationship remain separate types.
- [ ] `D5.3` Use PDL's small free company allowance only where identity, headcount trend or funding remains unresolved. Avoid personal-contact enrichment. Compare headcount and funding to company filings/announcements; store the provider's allowed derived insight and retrieval audit, not a public mirror of PDL profiles.
- [ ] `D5.4` Add EPO OPS patent search by applicant aliases and subsidiaries, then reconcile patent families to the canonical issuer. Store priority date, jurisdiction, family, CPC classes, citations, legal status and confidence in ownership; deduplicate one invention filed in many offices. Separate applications, grants, active rights and expired rights.
- [ ] `D5.5` Compute patent trends by CPC/technology, family quality proxies, grant/expiry timing and competitor overlap. Explain that counts/citations are signals rather than patent value or freedom-to-operate opinions.
- [ ] `D5.6` Connect validated deals, leadership changes, products and patents to thesis KPIs and news clusters. A disputed or suggested relationship can prompt review, but cannot automatically trigger a high-confidence thesis breach.
- [ ] `D5.7` Build a company “Business network and innovation” view: evidence-linked edges, time range, source agreement, product/technology map, patent trend, and a compact list of what changed since the last review.

**Exit checks:** a subsidiary patent is not attributed to a parent without a dated ownership path; a same-name company match stays suggested; a rejected Diffbot edge does not reappear unchanged; a confirmed deal cites primary evidence.

## Phase 6 — Funds, indices, institutional ownership and look-through

**Dependency:** Phases 1, 3 and 4. **Sources:** existing issuer holdings/SEC 13F, Finnhub/FMP free probes, public fund filings. **Outcome:** transparent portfolio exposure rather than claimed full global fund coverage.

- [ ] `D6.1` Extend `lookthrough/` beyond configured CSPX/VWCE where issuer-published holdings and use terms permit. Record fund ISIN/share class, holdings as-of date, assets, expense ratio, cash/derivative remainder, coverage and look-through basis. Do not equate an ETF proxy's constituents with an official index history.
- [ ] `D6.2` Test Finnhub ETF profile/holdings, index constituents and institutional ownership plus FMP ETF/ownership endpoints under actual free keys. Their documented premium features remain disabled when denied. Preserve provider adapters as opt-in probes, not required dependencies.
- [ ] `D6.3` Use SEC N-PORT data for US fund-holdings gaps and existing Form 13F for US institutional ownership. Keep distinct reporting lags, investor thresholds and the difference between fund positions and beneficial ownership. Map CUSIP/ISIN/issuer carefully.
- [ ] `D6.4` Aggregate direct shares plus fund holdings by canonical issuer and report top overlap, region/sector/currency exposure, fee drag and “unknown fund weight.” Keep all as-of dates visible; never distribute unknown weight proportionally to known positions.
- [ ] `D6.5` Add a fund/index coverage ledger: official holdings, issuer downloadable data, regulatory filing, proxy fund, unavailable. Benchmarks without total-return or historical constituents should be flagged before performance/backtest use.

**Exit checks:** fund holdings weights plus cash/derivatives/unclassified reconcile to the published snapshot within tolerance; duplicate ADR and ordinary-share exposure is merged at issuer level but traceable to both securities; stale fund holdings are visibly stale.

## Phase 7 — Make the new data usable throughout the app

**Dependency:** Phases 2–6. **Outcome:** every permitted source has a concrete consumer; missing data has an honest UI.

- [ ] `D7.1` Add `/data/coverage` and `/sources/entitlements` owner-only read APIs and frontend panels in Activity/Config. Show coverage by security, geography, metric, period and source; quota headroom, freshness and last denial; never show API keys.
- [ ] `D7.2` Extend holdings with sections for global financial history, country/trade exposure, business network, patents, and source disagreements. Hide no-data cards only after showing a clear gap link; label vendor-derived versus primary values.
- [ ] `D7.3` Extend the portfolio overview with aggregate macro sensitivities (weighted, reported versus inferred), country and product concentration, adjusted-return coverage and fund overlap. Roll up only mapped/covered holdings; display the denominator beside every percentage.
- [ ] `D7.4` Add read-only research APIs for country/industry/company context so deep research and chat cite stored observations. Let AI explain links and uncertainty; keep numeric aggregations in deterministic code. Add “known as of” dates to every report claim.
- [ ] `D7.5` Add event rules for credit stress, trade exposure changes, material patent/legal-status changes and significant confirmed company-graph events. Throttle and deduplicate alerts using existing `news/` and `monitor` machinery. Show a decision trail to the owner.
- [ ] `D7.6` Update backend README, setup guide, OpenAPI and web README with exact source setup, user-visible scope, expected data delays and removal/disable behavior.

**Exit checks:** each of the 13 providers has a documented user-facing or internal quality-control consumer and an entitlement status; owner can follow a portfolio-level insight back to the underlying source; removing any optional key degrades coverage but does not fail portfolio sync.

## Phase 8 — Verification, operations and zero-cost release gate

**Dependency:** all prior phases. **Outcome:** repeatable, auditable operation under free limits.

- [ ] `D8.1` Add parser fixtures and contract tests for every adapter, including empty/partial responses, schema drift, revised data, 403/429 and quota reset. Use live tests sparingly against an explicit test budget; CI must not need third-party keys.
- [ ] `D8.2` Run a 30-day shadow collection on held/watchlist securities and representative countries. Measure metric coverage, source disagreement, false matches, freshness, quota consumption, failed jobs and value added over existing sources. Publish a sign-off report; keep low-value adapters as dormant, documented gap-fill options rather than spending their free quotas routinely.
- [ ] `D8.3` Reconcile sampled prices, corporate actions, financial statements, fund weights, macro releases and patent families manually against primary sources. Set domain-specific error thresholds before enabling any new alert or valuation conclusion.
- [ ] `D8.4` Test backup/restore and retention, entitlement expiry, source removal and migration rollback. Ensure the app can recompute derived metrics from allowed stored observations and does not retain restricted raw vendor payloads beyond permitted limits.
- [ ] `D8.5` Keep a public-product launch gate: repeat Phase 0 for each provider and disable any source-derived display, download or API output lacking rights. A zero-cost requirement may mean a feature remains unavailable rather than silently switching to a paid API.

**Exit checks:** provider spend is $0; no accidental premium endpoint or overage is reachable; coverage and uncertainty are visible; a source outage never changes a previously verified fact into an unsupported claim.

## Handoff to the feature plan

Phase 1 supplies canonical identity and point-in-time provenance. Phases 2–6 supply macro/trade, return, financial, graph/patent and fund/ownership capabilities. [The companion plan](morningstar_features_implementation_plan.md) builds screeners, research pages, X-Ray-style views, portfolio performance and other analytics only on those **actual, entitled, covered** outputs.

## Data still missing after this plan

The 13 sources complement one another, but do not collectively create a free, fully licensed global terminal. Track these as explicit gaps in `coverage_status` and in the feature plan:

| Missing or uneven dataset | Best free path already available to the app | Product consequence |
| --- | --- | --- |
| Long, clean global total-return history and complete corporate actions | Exchange/issuer announcements and the existing Stooq/Yahoo path where permitted; selective Alpha Vantage/EODHD checks. | Return, benchmark and backtest windows can be shorter or partial; never splice price-only and total-return series. |
| Complete point-in-time global listed-company financials, segments and shares | SEC filings, ESEF filings and issuer annual/interim reports; vendor facts only as field-level gap fills. | Deep financial history and cross-market screener coverage are measured, not assumed. |
| Global analyst consensus and revision history | Verified free event estimates or company guidance when available. | Consensus screens, revisions and surprise histories remain unavailable for many securities. Owner scenarios are labelled separately. |
| Full global institutional/insider ownership and short-interest history | SEC 13F/insider filings and jurisdiction-specific official filings added case by case. | US coverage is deeper; non-US ownership cannot be inferred from a provider marketing page. |
| Full historical index membership, constituent weights and official total-return benchmarks | Issuer-published fund holdings or public index factsheets as dated proxies, with provenance. | Historical index attribution/backtesting is disabled without genuine as-of constituents. |
| Global fund holdings/NAV/fees and fixed-income security terms | Issuer disclosures and US N-PORT where allowed. | Fund comparison/X-Ray shows stale or unknown weight; bond duration/quality only where actually published. |
| Company-level supplier/customer contracts and geographic revenue for all issuers | Reported segments, cited company disclosures and owner-reviewed graph evidence. | UN Comtrade and macro data provide context, not invented company counterparties or revenue weights. |
| Corporate actions and tax treatments across every jurisdiction | Broker ledger, issuer disclosures and owner-confirmed exceptions. | Personal return and gain calculations identify unreconciled events; no unsupported tax filing result. |

The owner can prioritize later free-source additions by **affected portfolio value × decision impact × missing-data severity**, subject to the same endpoint rights and evidence rules. A request for deep global coverage should result in a measured country/exchange coverage map, not a claim that all markets are equally supported.

## Source references

Official provider pages reviewed on 29 September 2026; Phase 0 must check terms and actual account access again at implementation time.

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
