# StewardAI news coverage and analytics improvement plan

**Status:** research-backed proposal  
**Date:** 2026-09-30  
**Scope:** News as an event input for portfolio oversight, research, and alerts

## Executive recommendation

Improve the event signal and its trustworthiness before broadening headline volume. StewardAI already has a strong pipeline foundation: configurable sources, URL normalization, stale/blocked-source filtering, syndicated-story clustering, holding linkage, classification, severity rules, corroboration, alert routing, and news-focused tests/evaluation fixtures (`apps/backend/src/steward/news/pipeline.py`, `apps/backend/src/steward/news/`). The plan should extend those foundations around source coverage, provenance, event resolution, analytics quality, and feedback.

Use a **source hierarchy**:

1. **Primary evidence first:** regulatory filings and regulator notices, company investor-relations releases, exchange notices, central banks/statistical agencies, courts and other relevant public bodies.
2. **Licensed professional news feeds** when commercial redistribution and latency justify their cost. Evaluate one financial feed vendor against the actual portfolio universe and display/use rights before committing.
3. **Permitted RSS/Atom and publisher APIs** for additional reputable reporting. Store metadata and short feed excerpts where terms permit; send users to the publisher for the article.
4. **Broad discovery aggregators** (for example GDELT) for coverage-gap discovery and corroboration, not as the sole source of a high-severity event.
5. **Targeted page fetching/scraping only as a governed fallback** for a named source that offers no usable feed/API and whose terms/robots policy and rights have been reviewed. Do not build a full-text news mirror.

Keep the current event pipeline. Add an explicit evidence/provenance layer and measure missed events, false escalations, duplicate reduction, and source diversity. Rank evidence quality and event materiality separately from article sentiment; do not present sentiment as a return forecast.

## What exists today

The pipeline docstring describes an end-to-end `news_ingest` flow: read configured sources, normalize and freshness-filter, drop blocked/stale/non-matching items and existing URLs, cluster syndicated copies, classify new items, assess event/severity/corroboration, then create and dispatch matching alerts. The code imports collectors for EDGAR, Finnhub, Google News, generic pages and RSS, along with separate regulatory and EDGAR full-text-search collection. This is a good base for incremental improvement.

Likely risks to address (verify against current runtime configuration during implementation):

- Configured sources and thresholds may give uneven geography, language, asset-class, or event-type coverage.
- Matching a story to a holding is not equivalent to proving that the holding is materially affected; entity aliases, subsidiaries, suppliers, and fund look-through need explicit confidence and relation types.
- News articles are noisy proxies for events: duplicated wire copies, revisions, rumors, opinion, and delayed publication can distort counts and sentiment.
- Current pipeline summary suggests unmatched stories are discarded. That can hide important macro, sector, supply-chain, or portfolio-wide events unless they are captured through explicit watch topics.
- “Confirmed” needs transparent evidence: independent sources should be genuinely independent, and primary-source confirmation should be distinguishable from multiple syndicated copies.
- Evaluation fixtures are present, but production observability and a labeled recall/precision benchmark should drive tuning.

## Research findings and source choices

### Coverage and acquisition

- **SEC EDGAR:** use SEC-hosted submissions/XBRL APIs and RSS for US filings, with required identification and responsible request behavior. These are primary-source event records, not ordinary press coverage. SEC describes its APIs and provides official RSS feeds: [EDGAR APIs](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgar-application-programming-interfaces-apis), [SEC RSS feeds](https://www.sec.gov/about/rss-feeds).
- **Finnhub:** its documented company-news endpoint provides headline, timestamp, summary, URL, source, and related symbols; the docs describe North American company coverage and a separate premium news-sentiment endpoint with US-only coverage. It is a useful candidate connector, but do not infer commercial display, archival, or redistribution rights from API availability; get written confirmation for the intended product. [Finnhub API documentation](https://finnhub.io/docs/api/quote).
- **NewsAPI:** its current pricing page says the free Developer plan is for development/testing only, has delayed articles, and production requires a paid plan; it says full article text is not provided. The terms restrict reproducing/republishing copyrighted material and building a competing news database. This makes it unsuitable as a default production source without a plan and permitted-use confirmation. [Pricing](https://newsapi.org/pricing), [terms](https://newsapi.org/terms).
- **GDELT:** useful to discover international coverage gaps and find corroborating mentions across a broad news corpus. Its DOC API is a search/discovery service; validate its update cadence, language/entity matching, and rights for the actual endpoint and intended use. Treat returned mentions as leads, not proof. [GDELT DOC API overview](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), [GDELT API guide](https://docs.gdeltcloud.com/API_DOCUMENTATION_GUIDE).
- **Event Registry:** a commercial alternative to evaluate if broad multilingual event clustering and source/topic analytics would save engineering effort; its product describes global source coverage, entity/category/sentiment enrichment, and event grouping. Request a portfolio-specific coverage sample and exact product display/storage rights before selecting it. [Event Registry](https://research.eventregistry.org/).
- **Licensed wires:** Reuters Connect / LSEG News are the higher-cost route when reliable low-latency global coverage and explicit customer display rights matter. LSEG explicitly says separate licenses/approvals may be needed for use beyond internal programmatic use. [Reuters Connect](https://reutersagency.com/content-delivery-platforms/reuters-connect/), [LSEG News Service](https://developers.lseg.com/en/product/news/overview).
- **Publisher RSS and scraping:** RSS is not a blanket content license. Publisher terms vary; for example, Le Monde states its RSS use is personal/non-professional unless authorized and paid. Use source-specific rights checks rather than assuming public accessibility grants commercial redistribution. [Le Monde RSS terms example](https://www.lemonde.fr/en/rss/article/2022/06/23/the-rss-feeds-of-monde-fr_5987755_165.html).

### Analytical design

Build the internal unit of analysis as a **story/event**, with articles as source evidence. Maintain the original headline/excerpt and link provenance, but calculate event status from evidence, not from the number of copies. Materiality, confidence, novelty, and portfolio exposure are separate dimensions. Use price reaction as retrospective context and evaluation—not a label that can leak future information into live alerts.

## Proposed work plan

### Phase 0 — Establish a measurable baseline (1–2 weeks)

1. Inventory the active `news_sources` config and log effective source, geography/language, asset coverage, polling cadence, latency, source tier, fields retained, cost, and terms/rights owner.
2. Instrument each source and each pipeline stage with counts, timing, errors, stale/drop reasons, unmatched entities, duplicate/cluster rate, model failures, and alert outcomes. Avoid logging article body text unnecessarily.
3. Create a labeled benchmark of 100–200 historical portfolio-relevant events, stratified across holdings, macro/sector events, geography, event type, and severity. Include missed-event examples and hard negatives (rumors, opinion, stale/recycled stories, unrelated same-name entities).
4. Report baseline event recall, precision, alert precision, time-to-ingest, time-to-confirm, entity-link accuracy, duplicate suppression, and source concentration. Add confidence intervals and per-segment results; aggregate scores can hide blind spots.

**Exit criteria:** baseline dashboard and a reproducible benchmark; every dropped item has a reason; every alert can be traced to its evidence and configuration/model version.

### Phase 1 — Improve evidence model and event resolution (2–4 weeks)

1. Extend story records with typed evidence and provenance: publisher/source ID and tier, first-seen/published/updated times, canonical URL, feed/API identifier, language, excerpt rights/expiry, primary-vs-secondary, independent-source group, and fetch/license status.
2. Track events separately from article clusters. Add event lifecycle states (reported, corroborated, primary-confirmed, disputed, corrected, resolved) and preserve revisions/corrections rather than silently overwriting.
3. Make corroboration independence-aware: syndication groups count once; company release/filing plus independent reporting are distinct evidence; conflicting sources trigger disputed status. Show the supporting evidence behind a confirmation.
4. Upgrade entity linking with identifier-backed aliases (ticker/exchange, ISIN/LEI/CIK where available), subsidiaries, brands, executives, key counterparties, and fund look-through. Store link type and confidence, then use stricter alert thresholds for indirect exposure.
5. Add explicit portfolio-level watch topics for macro, regulation, rates, currencies, sectors, and key supply-chain/counterparty risks so relevant non-holding stories are not lost at the holding-match gate.

**Exit criteria:** improved benchmark recall without material loss of alert precision; users can inspect why an event is linked, confirmed, disputed, or escalated.

### Phase 2 — Fill the highest-value coverage gaps (2–6 weeks, parallel by source class)

1. Run a coverage audit against the actual holdings/watch topics: for each instrument, test official newsroom, regulator/exchange, major market-language sources, and current aggregators over a fixed period. Prioritize uncovered high-risk holdings and event classes.
2. Add or improve official-source adapters first (issuer IR feeds/pages, exchange notices, central banks/statistical offices, regulators, courts as relevant). Preserve a source catalog and per-source freshness/health check.
3. Add a second independent news provider only where the audit shows gaps or the source improves latency, language, or independence. Pilot with a bounded portfolio subset and score it on unique material events, not raw article count.
4. Use GDELT or a commercial event aggregator as discovery/backfill where multilingual coverage is weak. Require corroboration or label it as unverified discovery.
5. Build source-health handling: conditional requests/ETags, bounded retries and backoff, per-domain rate limits, circuit breakers, parser-change detection, and source outage visibility. A dead feed should not appear as “no news.”

**Vendor decision gate:** compare coverage of benchmark events, median/95th percentile latency, entity/tag quality, historical depth, status/SLA, attribution/display rules, caching and retention, derived analytics/model use, geographic scope, and all-in cost. Obtain written confirmation of rights for snippets, user display, model processing, and retention/deletion before launch.

### Phase 3 — Upgrade analytics and user-facing explanations (2–4 weeks)

1. Keep existing event types/severity rules, but tune them against the benchmark and expose event-level confidence, materiality, novelty, evidence count/independence, and direct-vs-indirect portfolio exposure separately.
2. Add event timelines and company/topic “what changed” summaries grounded in cited source links. Summaries should state known facts, disputed facts, and unknowns, and avoid expanding the licensed excerpt.
3. Replace undifferentiated sentiment with calibrated, aspect-aware signals: direction/uncertainty, target entity, evidence span, and model confidence. Separate factual event classification from emotional tone and opinion/editorial content.
4. Add event novelty/relevance ranking: portfolio exposure × event materiality × confidence × freshness, with source quality and duplicate count handled explicitly. Suppress repeated copies while preserving updates and new facts.
5. Surface price/volume reaction windows as “observed market reaction” with benchmark context (market/sector movement and earnings calendar where available). Keep it retrospective, not predictive language.
6. Add user feedback actions (relevant/not relevant, wrong entity, duplicate, misclassified, missing source) and feed adjudicated feedback into benchmark/eval—not directly into unreviewed model training.

**Exit criteria:** users can understand why an event matters, what evidence supports it, what remains uncertain, and how it affects their holdings or watch topics.

### Phase 4 — Governed scraping only where it earns its cost (ongoing)

Scraping can help discover issuer/agency announcements when no stable RSS/API exists, but should be the last-mile adapter for a finite, documented source list—not a general web crawler.

For each candidate site:

- Review terms of service, robots directives, publisher RSS/API terms, applicable jurisdiction, and rights for commercial use and model processing. Robots permission alone is not a content license; public accessibility alone does not establish display/retention rights.
- Prefer RSS, sitemap, JSON-LD metadata, or an official press-release API over HTML extraction. Fetch only necessary metadata and minimal permitted excerpt; retain canonical URL and send readers to the source.
- Identify the crawler, obey explicit blocks, use low request rates/caching/conditional requests, honor `Retry-After`, avoid login/paywall circumvention, and stop on access denial or legal request.
- Version selectors/parsers, monitor field completeness and layout changes, quarantine malformed results, and use source-specific kill switches.
- Record approval, contact, allowed fields, excerpt limits, retention/deletion policy, and last review date per source. Recheck when terms or publisher behavior changes.

Do not scrape full-text articles to reconstruct a commercial news corpus. If full text is needed for the product, pursue a license that expressly permits ingestion, model processing, customer-facing display, caching, and the intended retention period.

### Phase 5 — Continuous evaluation and operations (ongoing)

- Run the benchmark after changes to source config, entity mapping, prompts/models, clustering, or severity rules. Keep a versioned holdout set and human adjudication log.
- Monitor event recall proxies from primary-source calendars/filings, alert precision feedback, false positives per user/week, source outages, stale-item fraction, latency, unique-event yield by source, and cost per useful event.
- Set release gates on segment regressions (for example language, geography, high-severity event types), not only an overall score.
- Perform incident reviews for missed high-severity events and incorrect escalations; update source coverage or evidence rules with a recorded rationale.

## Suggested success measures

| Measure | Definition | Initial goal for pilot |
|---|---|---:|
| High-priority event recall | Labeled material events ingested and linked / all labeled material events | Improve by at least 15 percentage points over baseline |
| Alert precision | Human-confirmed useful alerts / reviewed alerts | At least 80% before broadening alert volume |
| Direct entity-link precision | Correct direct issuer links / reviewed direct links | At least 95% |
| Duplicate burden | Article items represented per event after clustering | Reduce by 50% while retaining distinct updates |
| High-severity latency | Published-to-ingested and published-to-confirmed p50/p95 | Establish baseline, then improve without reducing evidence quality |
| Source concentration | Share of confirmed events dependent on top source/provider | No single provider dominates independently corroborated events |
| User correction rate | Wrong entity, irrelevant, duplicate, or misleading reports per reviewed event | Trend down by segment |
| Cost efficiency | Incremental source + inference cost per unique useful event | Set after source pilot; compare monthly |

Treat these numbers as pilot targets and recalibrate with actual user volume and baseline results.

## Recommended implementation order

1. Baseline source inventory, telemetry, and event benchmark.
2. Provenance/independence-aware evidence and event lifecycle.
3. Holding/watch-topic coverage audit and primary-source gaps.
4. One bounded provider/source pilot chosen by unique benchmark coverage and clear rights.
5. Calibrated ranking, evidence-backed explanations, user feedback, and ongoing evaluation.
6. Targeted scraping only for explicitly approved, high-value sources with no suitable feed/API.

This sequencing makes it possible to tell whether a new source actually catches important events and whether analytics improve, instead of rewarding volume.

## Decisions to make before implementation

The plan can start with baseline work now. Vendor selection and scraping scope need these product facts documented during Phase 0: launch markets and supported languages; portfolio instrument/geography mix; expected user count and business model; headline/snippet display requirements; acceptable data/inference budget; and required alert latency. Keep the acquisition layer provider-neutral so those choices do not lock the event schema to a vendor.

## Research references

- [SEC EDGAR APIs](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgar-application-programming-interfaces-apis) and [SEC RSS](https://www.sec.gov/about/rss-feeds)
- [Finnhub API documentation](https://finnhub.io/docs/api/quote)
- [NewsAPI pricing](https://newsapi.org/pricing) and [terms](https://newsapi.org/terms)
- [GDELT DOC API overview](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) and [API guide](https://docs.gdeltcloud.com/API_DOCUMENTATION_GUIDE)
- [Event Registry platform](https://research.eventregistry.org/)
- [Reuters Connect](https://reutersagency.com/content-delivery-platforms/reuters-connect/) and [LSEG News Service](https://developers.lseg.com/en/product/news/overview)
- [Publisher RSS terms example: Le Monde](https://www.lemonde.fr/en/rss/article/2022/06/23/the-rss-feeds-of-monde-fr_5987755_165.html)
