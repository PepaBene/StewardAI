# StewardAI news: coverage, full-text and interpretation plan

**Status:** research-backed proposal, revision 2  
**Date:** 2026-10-10 (rev 1: 2026-09-30)  
**Scope:** make news the main event driver: a wide global wire, deep stories for holdings, and an analysis layer that explains what a story means for this portfolio.

## 0. What changed in this revision

Rev 1 was built for a multi-user commercial product: licence-first, headline-light, "do not build a news mirror". The product is a **single-user, self-hosted** copilot (`README.md`), and the owner now wants news to be the main event driver, with Bloomberg, FT, Reuters, BBC and reliable Japan, China, Asia, Germany, Canada and UK coverage, headlines **and** full reports, and strong analysis. So:

1. **Coverage first, in three lanes** (section 3): a broad *Wire* of every headline, a *Stories* lane for portfolio and watch-topic events, and *Primary* official sources. Rev 1 kept only holding-matched items; the schema comment says "Items that match no holding are never stored" (`supabase/migrations/20260925200207_phase3_news.sql:132`). That is the biggest single limit on "powerful" and it goes.
2. **Full text through an access ladder** (section 5), not a blanket ban. Personal, local analysis of lawfully fetched text is in scope. Paywall circumvention, and redistribution, are out.
3. **Interpretation is the product** (section 6): event extraction, impact chains onto holdings, novelty, story threads, cross-country framing, thesis links, briefings, ask-the-news. Rev 1's evidence, corroboration and benchmark work stays, as the trust layer under it.
4. Rights language is now a **personal-use risk register** with owner decisions (section 11), not a vendor-licence gate. Licence risk is not zero: publisher terms still restrict automated access, and the owner carries that risk.

## 0a. Constraints from the owner (2026-10-10)

$10/month for news, no existing news subscriptions. Consequences, applied throughout: free sources only (the Wire, the Reuters sitemap, GDELT, official APIs); Bloomberg, FT, Reuters and Nikkei are headline + summary + link with analysis of what is lawfully readable; subscription-session fetching is dropped; the money goes to model inference under the funnel and sub-budgets in section 8a.

## 1. Where the code is today (verified 2026-10-10)

- Pipeline `apps/backend/src/steward/news/` (6.4k lines): `feeds.py` (RSS, page, EDGAR, Finnhub, Google News, Alpha Vantage collectors), `regulatory.py` (RNS, EQS, EDGAR full-text search), `normalize.py`, `cluster.py`, `link.py` (holding matching), `classify.py` (Claude classifier, event types), `corroborate.py`, `escalation*.py`, `digest.py`, `pipeline.py` (`news_ingest` job).
- Config `apps/backend/src/steward/config/news_sources.py`: `SourceType` = rss, page, edgar, finnhub, google_news, rns, eqs, edgar_fts, alpha_vantage; trust tiers 1–3 per source and per outlet domain; `freshness_hours=72`; tier 3 off by default. Default sources are only EDGAR, Finnhub, Fed, ECB and ČNB feeds (Google News, RNS, EQS, Alpha Vantage are shipped **disabled**).
- Tables: `source`, `story_cluster`, `news_item`, `alert`, `report`. Items matching no holding are dropped before storage (macro sources excepted).
- Already available for full text: `research/polite.py` (`PoliteFetcher`: robots, per-host rate limit, SSRF guard) and `research/browser.py` (`BrowserRenderer`, Playwright, optional). Reuse both; do not write a second fetcher.
- Web: `apps/web/src/app/(app)/news/news-view.tsx` shows a holding-linked feed with severity, tier, price reaction and source stats; `components/news/*` holds the pieces.
- Related plans to align with, not duplicate: `agentic_workflows_and_rag_plan.md` (embeddings = `gemini-embedding-2`, news indexed as headline+summary, 12–24 month vector retention; inbox alerts), `multi_provider_model_routing_plan.md` (model routing), `exposures_and_dependencies_plan.md` (exposure graph this plan's impact chains need), `free_data_sources_implementation_plan.md` (Alpha Vantage news sentiment item D3.8).

## 2. Live source probe (curl, 2026-10-10, UA `Mozilla/5.0 StewardAI-research`)

HTTP 200 with parsable items unless noted. "Items" = entries in the feed at probe time. This shows reachability only, **not** reuse terms.

| Outlet | Feed | Result | Gives |
|---|---|---|---|
| Bloomberg | `feeds.bloomberg.com/{markets,politics,technology}/news.rss` | 200, 20 each | headline, summary, link (full text paywalled) |
| Financial Times | `ft.com/rss/home`, `ft.com/{markets,companies}?format=rss` | 200, 11 / 25 / 25 | headline, short summary, link (paywalled) |
| Reuters | `feeds.reuters.com/...` | dead (connection fails; Reuters ended RSS in 2020) | – |
| Reuters | `reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml` → paged `news-sitemap/?outputType=xml&from=N` | 200, Google News sitemap | title, URL, publication time, language, image, in near real time; no summary |
| BBC | `feeds.bbci.co.uk/news/{business,world,world/asia}/rss.xml` | 200, 52 / 30 / 17 | headline, summary, link (full text open to read) |
| Nikkei Asia | `asia.nikkei.com/rss/feed/nar` | 200, 50 | headline, summary (partly paywalled) |
| Japan Times | `japantimes.co.jp/feed/` | 200, 30 | headline, summary |
| NHK World | `…/nhkworld/en/news/feeds/` | **404** (find current URL) | – |
| SCMP | `scmp.com/rss/92/feed` (business), `/4/feed` (China) | 200, 50 each | headline, summary |
| Caixin Global | `caixinglobal.com/rss/` | **403** (bot block; use Playwright or Google News fallback) | – |
| Xinhua | `xinhuanet.com/english/rss/businessrss.xml` | 200, 20 | state media: label as such |
| China Daily | `bizchina_rss.xml` | **404** | – |
| Straits Times | `straitstimes.com/news/business/rss.xml` | 200, 24 | headline, summary |
| Korea Herald / Yonhap | `kh_Business`, `en.yna.co.kr/RSS/news.xml` | 200, 50 / 101 | headline, summary |
| DW | `rss.dw.com/rdf/rss-en-bus` | 200, 20 | headline, summary; full text open |
| Handelsblatt / FAZ / Tagesschau | finanzen, aktuell/finanzen, wirtschaft | 200, 20 / 29 / 39 | German headline, summary |
| Globe and Mail / Financial Post / CBC | business feeds | 200, 100 / 10 / 20 | headline, summary |
| Guardian / Sky | business feeds | 200, 40 / 10 | headline, summary |
| Telegraph | business RSS | **402** (payment required) | – |
| Al Jazeera, Economic Times, Mint, CNBC, MarketWatch, WSJ (Dow Jones public) | RSS | 200, 25 / 50 / 35 / 30 / 10 / 61 | headline, summary |
| BoJ, BoE, Bank of Canada | RSS | 200, 51 / 50 / 10 | primary macro |

Not yet probed (do in N1): Bundesbank, PBoC, HKEXnews, GDELT DOC, Google News per-outlet queries, TDnet/EDINET, DART, CNINFO, SEDAR+, Companies House.

Web research (cited in section 12): GDELT updates every 15 minutes, machine-translates 65 languages, DOC 2.0 searches a rolling 3-month window with source-country, language, theme and domain operators, and the Global Knowledge Graph gives people, organisations, locations, themes and tone. Reuters/LSEG enterprise feeds are quote-only; Reuters public consumer tiers (about $15 and $35 a month) are websites, not APIs. Among open extractors, trafilatura scored F1 0.958 on the Scrapinghub benchmark vs newspaper4k 0.949 and Mozilla Readability 0.947.

## 3. Target architecture: three lanes

```
 Primary (tier 1)   SEC/EDGAR, RNS/EQS, EDINET/TDnet, HKEX/CNINFO, DART, SEDAR+, central banks,
                    statistics offices, issuer IR      ─┐
 Wire (tier 2)      Bloomberg, FT, Reuters(sitemap), BBC, Nikkei, SCMP, DW, Handelsblatt,  ├─► fetch ─► normalise ─► dedupe
                    Globe&Mail, … + Google News per outlet + licensed API if bought       │      (url, simhash, embedding)
 Discovery (lead)   GDELT DOC/GKG, Alpha Vantage, Finnhub                               ─┘
                                           │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        ▼                                  ▼                                  ▼
  WIRE lane: every item kept     STORIES lane: items linked to a       ANALYSIS lane (section 6):
  (headline, summary, link,      holding, watch topic or exposure →    event extraction → impact chains →
  language, translation,         story_cluster/event, corroboration,   novelty → threads → interpretation
  embedding), searchable,        severity, alerts (existing pipeline)  briefs, ask-the-news
  filterable by region/outlet
```

Rules:

- **Wire is wide and cheap.** Keep every fresh item's metadata and embedding (headline+summary), so macro, sector, geopolitical and supply-chain news is searchable even when no holding name appears. Retention: metadata and embeddings 24 months (aligned with the RAG plan), extracted full text 90 days unless the story is linked to a holding, thesis or alert.
- **Stories stay precise.** The existing link, classify, corroborate, escalate path runs on linked items only; the wire lane feeds it through entity linking, watch topics and exposure links (section 6.3).
- **Analysis runs on a funnel**, never on the whole wire (section 8, cost).
- Source tier still decides alerting: tier-2 outlets can raise a story, tier-3 and discovery items cannot alert alone.

## 4. Source plan: named outlets and regions

Add these as config (no deploy; `news_sources` is versioned config). Items marked **new type** need a collector.

| Region / need | Sources | How |
|---|---|---|
| Bloomberg, FT | public RSS above | `rss`; headline + summary + link only (no subscription, section 0a) |
| Reuters | news sitemap | **new type `news_sitemap`**: poll the sitemap index every 2–5 min, take new `<news:news>` entries (title, time, language), fetch the article through the ladder. Fallback: Google News query `site:reuters.com` per topic/holding |
| BBC | business, world, Asia, tech RSS | `rss`; article body is open: ladder rung 2 |
| UK | Guardian (also Open Platform API, key, full text—confirm terms), Sky, FT, BBC, Telegraph via Google News (RSS is 402), RNS, BoE, ONS, Companies House (free REST/stream) | `rss`, `api`, existing `rns` |
| Japan | Nikkei Asia, Japan Times, NHK (find new feed), Kyodo/Mainichi English; **primary**: EDINET API v2 (free key), TDnet (JPX, paid; fallback JPX site page), BoJ, MoF | `rss`; **new types `edinet`, `tdnet`** |
| China / HK | SCMP, Caixin (via Playwright or Google News while 403), Xinhua and CGTN (state media, labelled), Yicai/Sina via GDELT; **primary**: HKEXnews, CNINFO/SSE/SZSE announcements, PBoC, NBS | `rss`, `page`, **new types `hkex`, `cninfo`** |
| Wider Asia | Straits Times, Korea Herald, Yonhap, Economic Times, Mint, Nikkei Asia; **primary**: DART (OpenDART free API), MAS, RBI, SGX | `rss`, **new type `dart`** |
| Germany | Handelsblatt, FAZ, Tagesschau, DW, Börsen-Zeitung via Google News; **primary**: EQS (shipped, off), Bundesbank, BaFin, Bundesanzeiger (no public API: page fetch) | `rss`, existing `eqs` |
| Canada | Globe and Mail, Financial Post, CBC, BNN via Google News; **primary**: SEDAR+ (no API: governed page/RSS, N6), Bank of Canada, StatCan | `rss` |
| Czech (existing owner market) | ČNB (shipped), Hospodářské noviny, E15, Seznam Zprávy (already tiered) | add feeds |
| Global discovery | GDELT DOC per holding/topic and per country; GKG themes | **new type `gdelt`** |
| US wires | EDGAR (shipped), Finnhub (shipped), CNBC, MarketWatch, WSJ public feeds | `rss` |

Add `region` (ISO country of outlet), `language`, `bias_note` (state media, tabloid, trade press) and `paywall` (`open`, `metered`, `hard`) fields to `NewsSource`; `domain_tiers` gains the new outlets (Nikkei, SCMP, Handelsblatt, Globe and Mail, BBC, DW, Straits Times and so on at tier 2; Xinhua/CGTN tier 2 with a `state_media` flag that caps them as sole corroboration for political topics).

Paid upgrade path (not planned, owner has $10/month and no subscriptions, section 0a): enterprise feeds (LSEG News / Reuters Connect, Bloomberg Enterprise, Dow Jones/Factiva) are quote-only and out of reach. Bloomberg, FT and Reuters therefore stay **headline + summary + link**, and the analysis layer works from those plus the same story's open coverage elsewhere (BBC, Guardian, DW, CNBC, primary filings, GDELT). That is the main cost of the free route: for those three outlets the app interprets what the summary and the corroborating open sources say, and labels the depth ("summary only").

## 5. Full-text acquisition ladder (per source, in order, stop at first success)

| Rung | Method | Applies to | Stored |
|---|---|---|---|
| 1 | Feed `content:encoded` or summary | most RSS | text |
| 2 | `PoliteFetcher` + **trafilatura** (fallback newspaper4k, readability) | open pages: BBC, DW, Guardian, Globe (metered), government and IR pages | text, language, byline, authored time |
| 3 | Same URL through `BrowserRenderer` (Playwright) | JS-rendered pages, bot-gated feeds like Caixin | text |
| 4 | Official API with a key (Guardian Open Platform, EDINET, DART, Companies House) | where it exists | text/structured |
| – | **Dropped (rev 2.1):** owner-subscription session fetching. The owner has no subscriptions and a $10/month budget; revisit only if a subscription is bought | | |
| – | Never: bypass or spoof paywalls, rotate IPs or accounts, use archive mirrors to defeat paywalls, share the text | | |

Notes:

- With no subscriptions, FT, Bloomberg, Reuters and Nikkei stay headline + summary + AI analysis of the summary, with a link. Open-body outlets (BBC, Guardian API, DW, government and IR pages, metered Globe and Mail within its free allowance) get full text through rungs 1–4.
- Per-source `FetchPolicy`: ladder rungs allowed, max requests/hour, retention days, `display` (`full`, `excerpt`, `link_only`). UI shows only what policy allows; analysis may use more than display shows.
- Each stored text keeps: canonical URL, content hash, fetched-at, rung used, extractor version, and a flag when extraction looks truncated (paywall stub). Truncation detection prevents analysing a paywall stub as if it were the article.
- Dependency choice: trafilatura as default, based on the published benchmark above; verify on 30 real articles per outlet in N2 (accuracy of body, byline and date) before relying on it.

## 6. Analysis and interpretation engine (the main investment)

Everything below produces **structured, cited, versioned** records. The LLM never invents facts: each claim cites a span of the stored text, and spans are verified by string match before saving (reuse the existing `verify` and citation components, `components/news/citation.tsx`).

### 6.1 Event extraction (per story, after clustering)
Typed schema: `event_type` (existing taxonomy plus macro, geopolitical, regulation, supply-chain, labour, cyber, natural disaster), `actors`, `affected_entities` (company, sector, country, commodity, currency, rate), `action`, `magnitude` (value, unit, vs expectation), `time` (occurred, announced, effective), `status` (rumour, reported, confirmed, official), `direction` per entity with confidence, `key_claims[]` each with source span, `unknowns[]`. Fact, interpretation and speculation are separate fields so the UI never blends them.

### 6.2 Novelty and surprise
Embedding distance to the previous 30 days of the same entities/topic, plus extracted magnitude vs prior guidance or consensus when available (company guidance in the data layer). Output `novelty` (new, update, repeat, recycled) and `surprise` (none, in line, above, below, unknown). Repeats are suppressed, not deleted. Rationale: market reactions concentrate on new information, and recycled stories are a known false-alert source.

### 6.3 Impact chains onto the portfolio
Use the exposure graph (`exposures_and_dependencies_plan.md`): holdings, subsidiaries, suppliers/customers (confirmed edges only), sectors, countries of revenue, currencies, commodities, rates, ETF look-through. For each material story, the model proposes chains (`story → entity → relation → holding`) with a relation type and confidence; code, not the model, checks that each edge exists in the graph or is labelled `hypothesis`. Output per holding: direct / second-order / hypothesis, plus an exposure-weighted portfolio impact in percent of NAV (exposure, not a return forecast). This also fixes the holding-match gate: a story about Taiwan export controls reaches an owner of TSMC suppliers through the graph without naming them.

### 6.4 Story threads and "what changed"
A thread groups stories on one developing situation (a merger, a strike, a tariff dispute). Each update is diffed against the thread's last state into: new facts, changed facts, retractions or corrections, still-unknown. Timeline view per thread and per holding. Prevents ten articles from reading as ten events.

### 6.5 Cross-source and cross-country comparison
For stories with 3+ independent outlets, extract each outlet's framing: headline claim, emphasised cause, numbers cited, quotes, what is omitted. Show agreement, conflict (disputed status feeds the existing corroboration state) and regional divergence (for example Japanese vs Western coverage of the same BoJ move; Chinese state media vs SCMP vs Reuters on a tariff). Outlet metadata (`region`, `state_media`, `bias_note`) is context to the model and to the reader, never a score applied silently.

### 6.6 Claim checking
For high-severity stories only: extract checkable claims (numbers, dates, named acts), match them to primary sources already in the system (filings, IR releases, central-bank data, price data) and mark `supported`, `contradicted`, `unverified`. Cites the primary document.

### 6.7 Market context
After publication, attach observed price/volume move versus sector and market benchmark over standard windows, plus earnings-calendar proximity (existing price-reaction component). Retrospective labelling only. Build an internal event-study table (event type × region × outlet tier → abnormal return distribution) from the owner's own history to show "stories like this moved holdings by X median" with sample size and uncertainty. No predictive claims; display `n` and dispersion.

### 6.8 Themes and narratives
Maintain 20–40 portfolio-relevant themes (rates path, China demand, AI capex, energy, EU regulation, FX, tariffs). Track daily story volume, GDELT tone and attention per theme and region, and flag regime changes (volume spike, tone shift, new cross-border spread). Weekly narrative summary per theme with the three strongest threads.

### 6.9 Thesis and risk linkage
Map extracted events to thesis KPIs and risk limits (`ai_research_thesis_improvement_plan.md`). A story can strengthen, weaken or be neutral to a thesis assumption, with the cited reason. Only owner-confirmed or primary-confirmed events may mark a thesis breach; others create a review prompt, matching the rule in `free_data_sources_implementation_plan.md` D5.6.

### 6.10 Briefings and ask-the-news
- **Morning brief** (regional order: Asia close, Europe open, US pre-market): what happened overnight by theme, ranked by portfolio exposure × materiality × novelty, each line cited, with "why it matters to you" and "what to watch next".
- **Per-holding "what changed this week"**, **weekly macro/regional digest** (Japan, China, Europe, Canada, UK sections).
- **Ask-the-news** chat over the wire + stories (RAG with `gemini-embedding-2`, hybrid with the existing Postgres full-text search from `20260929110000_news_search.sql`): "what is Japanese press saying about yen intervention", "who covered the Siemens Energy guidance cut first", answers with citations and source tier shown. Shares tools with the agentic plan; do not build a second chat stack.

### 6.11 Sentiment, bounded
Aspect-level direction and uncertainty per target entity (6.1), tone from GDELT as an attention feature, an LLM-scored headline signal as one input to ranking. The Lopez-Lira and Tang study reports predictive correlation for LLM headline scores, concentrated in smaller stocks and bad news, and itself warns against relying on it for decisions; so sentiment ranks and annotates, it never triggers an alert and never appears as a forecast.

### 6.12 AI search lane (leads, not a feed)
The app's existing search-capable models find news the feeds miss. No new vendor or SDK: the lane is one more call site of the existing `LLM.complete(Request)` interface (section 6.13) using the `web_search` tool through `SearchSpec`, which already runs on OpenAI (`gpt-6-luna`/`sol`) and Google (`gemini-3.8-flash` grounding) and returns citations in the shape the source-tier checks expect. Exa/Tavily-style search APIs are not added.

**Search is the expensive part.** `docs/plans/multi_provider_model_routing_plan.md` prices web search at $10 per 1,000 calls (OpenAI) and $14 per 1,000 (Google), and `BudgetConfig.web_search_per_1000` defaults to $10. A $1–2.5 search allowance buys only about 100–250 searches a month, **and the existing news steps already spend searches**: corroboration (`corroboration_max_searches=10` per call, `corroboration_rounds=3` per unconfirmed story), escalation, `gap_research` and `ir_discovery` (their results are already cached 3 days). So there is one shared search pool, and the AI search lane must earn its share. The lane therefore has these uses, in priority order:

1. **On-demand ask-the-news (6.10).** When the stored wire cannot answer a question, the chat may search live and says so. Cost only when asked.
2. **Weekly gap sweep, not daily.** Only for holdings the N0 coverage audit marks thin (few wire items in 14 days, no primary source, non-English home market). One call per such holding per week, batched through `BatchCollector`/`parse_many` so the round is one provider batch where batch pricing applies to the model.
3. **Reuse the corroboration search.** Before adding any call, let the existing corroboration/escalation searches write their cited URLs into the Wire as `ai_search` leads: their results are already paid for.

Rules:

- **Leads only.** Output is a list of URLs plus the model's one-line reason. The prose is discarded and never stored as a fact. The pipeline fetches each URL through the ladder (section 5), runs the normal dedupe, linking, classification and corroboration, and the item enters the Wire as `source=ai_search`, tier by the outlet's domain tier (`NewsSourcesConfig.domain_tier`), flagged `discovered_by_ai`. Citations from outlets outside `allowed_domains` or the tiered list are rejected after the fact (as the Google path already does).
- **No alert on its own.** An AI-discovered item can raise a story only when corroborated by a non-AI source or a primary filing, the same rule as GDELT and tier-3 items.
- **Verification before display.** A URL that does not resolve, or whose fetched text does not support the model's reason (checked by the existing claim verifier, `verify/claims.py`), is dropped and counted as a false lead.
- **Own cap.** A per-job-type monthly cap in the budget config (new, section 6.13). At the cap the sweep stops; ingestion, triage and chat on stored news continue.
- **Metric:** leads that become unique, material, verified events per dollar. If near zero after a month, drop the sweep (use 1 and 3 only).
- **Limits it cannot fix:** search indexes lag the feeds, results are not exhaustive, and paywalled pages stay stubs. It supplements the feeds, never replaces them.

### 6.13 Integration with the existing AI architecture (binding rules for every AI step in this plan)

Nothing in this plan adds a second AI stack. Each AI step is a call site of what exists:

| Concern | Existing piece to use | What this plan adds |
|---|---|---|
| Calling models | `LLM.complete(Request)` via `routing_client` (`steward/llm/routing.py`, `client.py`); structured output with a pydantic schema; effort levels; function tools | Only new schemas (`StoryExtraction`, `ImpactChain`, `ThreadDiff`, `FramingComparison`, `Brief`, `SearchLeads`) |
| Model per step | `NewsSettings` / `JobsConfig` fields with `ModelId` and `Effort` (as `classification_model`, `corroboration_model`); tiers from the routing plan: **luna** (low effort) for volume, **flash** for analysis, **sol** only for escalation and rare deep reads | New fields: `triage_model`, `extraction_model`, `analysis_model`, `brief_model`, `search_lead_model`, each with effort; defaults luna / luna / flash / flash / luna |
| Prompts | `PromptsConfig` (`config/prompts.py`): one editable system prompt per step, with `override_providers` | New `PromptKey`s: `news_triage`, `news_extract`, `news_impact`, `news_thread`, `news_framing`, `news_brief`, `news_search_leads`, each with a default text and eval cases |
| Cost recording and caps | `budget_guard.before_request`, per-step usage recorded to `job_step`, `BudgetConfig` prices, `monthly_cap_usd` with warning and `downgrade_model`, per-run caps via `per_job_cap_usd` / `job_cap` | Each stage is its own job type or step (`news_triage`, `news_analysis`, `news_brief`, `news_ai_search`) so spend is visible per stage in `GET /costs/summary`. **Gap:** the budget config has a per-run cap and one global monthly cap, but no per-job-type monthly cap; add `per_job_monthly_cap_usd` so a runaway stage cannot eat the whole $10 |
| Repeats and bulk | `ResultCache` (`ai_result_cache`, exact repeated structured calls), provider prefix caching, `BatchCollector`/`parse_many`/`LLM.complete_batch` (50% off, `use_batch`, `batch_wait_minutes`, falls back to a direct call) | Triage and backfill use batch; cache key includes model, prompt text and content hash so an unchanged article costs nothing twice |
| Rules before AI | "Do it in code, not with AI" (routing plan), `rules_first`, `date_check`, `quote_check` | Language detect, URL/SimHash dedupe, 8-K item rules, exact entity-alias matches, sitemap/feed parsing and span verification are code; the model decides only what code cannot |
| Claim integrity | `verify/claims.py` claim verifier, `chat` source registration (`_register_claim`, `_register_context`) and figure checks | Every extracted claim and brief sentence goes through the same verifier and source registry; uncited output is not saved |
| Exposure and impact | `exposures/` module and the chat tools `event_paths` (`analytics_tools.event_reach`) and `theme_exposure`; `relationships` data | Impact chains (6.3) call `event_reach` for the graph path; the model only writes the explanation and a confidence, never invents an edge |
| Chat / ask-the-news | `chat/assistant.py` tool loop and `chat/tools.py` (`search_news`, `search_reports`, `holding`, `event_paths`, `country_context`) | New tools only: `search_wire`, `get_story_thread`, `web_news_search` (the 6.12 lane). No second chat stack |
| Jobs | `jobs/runner.py` (`create_job`, `JobContext`, steps), the scheduler, `news_ingest` | New jobs `news_triage`, `news_analysis`, `news_brief`, `news_ai_search` registered the same way; analysis consumes the queue `news_ingest` fills |
| Evaluation gates | `news/eval.py`, `tests/news/news_classification.json`, `verify/evals_gate.py`, per-provider `--live --model` runs | Golden sets for extraction, impact chains, thread diffs, framing and leads; a step moves to a cheaper model only when its gate passes, as the routing plan already requires |
| Embeddings | `gemini-embedding-2` and the RAG tables from `agentic_workflows_and_rag_plan.md` | Wire rows reuse that index; no second vector store |
| Budget presets | `switch_models.py` | Add the new steps with the luna/flash defaults above |

Rule of thumb: if a step can be done by an existing function, call it; if it needs a model, it goes through `LLM.complete` with a registered prompt, a pydantic schema, a job type for cost, a cache key, and a golden-set eval.

## 7. Dedupe, language and ranking

- Dedupe in three stages: canonical URL (existing), SimHash/MinHash of normalised text, then embedding similarity within a time window (extend `cluster.py`). Wire copies (Reuters/AP/Bloomberg syndication) form one independence group for corroboration.
- Language: detect per item; **translate headline+summary to English on ingest** with a cheap model, keep the original; translate full text only when an item enters analysis. Embeddings: `gemini-embedding-2` (already chosen in the RAG plan) is multilingual, so cross-language clustering works without translating first; compare on 50 JP/ZH/DE pairs in N3.
- Ranking = exposure × materiality × novelty × confidence × freshness, with source tier and duplicate count explicit and visible, unchanged from rev 1.

## 8. Cost and funnel

Funnel per fetch cycle: all items → dedupe (code) → embedding (`gemini-embedding-2`) → **luna triage** (relevance, entities, topic, language, English headline) → only linked or high-relevance items get extraction (6.1) and full text fetch → only material or high-exposure stories get impact chains, framing comparison and claim check on **flash**. All calls go through the existing `LLM.complete`/`routing_client`, `budget_guard` and `job_step` recording (section 6.13). The N0 baseline sizes the real daily item count and today's news spend (`GET /costs/summary`) before any figure below is trusted. Prompt-cache the stable prefix, use batch endpoints for triage and backfill.

### 8a. The $10/month envelope (owner decision D1, 2026-10-10)

Assumption to confirm: $10 is the ceiling for **new** news spend on top of what the news jobs cost today (classification, corroboration, escalation, digests); N0 reads today's figure from `GET /costs/summary` and, if the existing steps already use most of it, the existing searches are the first thing to trim. Everything else in the plan is free: RSS, the Reuters sitemap, GDELT, official filing APIs, trafilatura, Playwright, and the Postgres you already run.

Illustrative split using the **routing plan's list prices (2026-10-06)**: luna $0.10 in / $0.50 out per 1M, flash $0.75 / $3.75 (doubles 2027-01-01), sol $2 / $10, batch 50% off, search $10–14 per 1,000. Volumes are my assumptions; replace both with N0's measurements:

| Stage (job type) | Volume assumption | Model | Target |
|---|---|---|---|
| Embeddings, headline + summary | ~3,000 items/day ≈ 7M tokens/month | `gemini-embedding-2` (~$0.20/M from a third-party page, unconfirmed), batch | ~$1 |
| Triage + English headline (`news_triage`) | 90k items/month × ~150 in / ~40 out ≈ 13.5M in, 3.6M out | luna low effort, batch | ~$2 (reasoning tokens count as output: the main uncertainty) |
| Extraction on linked stories (`news_analysis`, 6.1–6.2) | ~60 stories/day × ~2.5k in, ~600 out | luna | ~$1 |
| Deep analysis + brief (`news_analysis`, `news_brief`, 6.3–6.9) | ~10 material stories/day × ~4k in, ~1.5k out, plus 1 brief/day | flash; sol only on rare escalation | ~$3 |
| Search (shared pool: AI search lane, plus the existing corroboration/escalation searches if they are trimmed into it) | ~180–250 searches/month at $10–14/1k | luna search / flash grounding | ~$2.5 |
| Reserve | | | ~$0.5 |

At these prices flash on all 60 stories/day would cost about $6/month for extraction alone, which is why extraction starts on luna and flash is reserved for material stories.

How the plan stays inside it:

1. **Per-job-type monthly caps.** The budget config today has a per-run cap (`per_job_cap_usd`) and one global `monthly_cap_usd`, with the 80% warning and the `downgrade_model` at the cap. Add `per_job_monthly_cap_usd` (section 6.13) so each stage above has its own ceiling. At its cap a stage degrades (skip deep analysis, keep triage), never stops ingestion. Wire items are always stored and embedded; analysis is what gets rationed.
2. **Hard rule: analysis is lazy.** Deep analysis runs for the top-N stories by exposure × materiality each day, and on demand when you open a story or ask a question. Unopened low-exposure stories get only triage.
3. **Tighten the wire first if cost bites:** drop low-yield feeds (measured by the N0 "unique useful events per source" report) before dropping analysis depth; cap embeddings to items that pass triage.
4. Use batch endpoints for backfill and re-analysis (50% off where offered), prompt caching on the stable system prefix, and cache by content hash so an updated article only pays for the diff.
5. N0 reports actual tokens and dollars per stage per day, so by the end of week one the table above is replaced with measured numbers. If the measured cost exceeds $10, the order of cuts is: translation of non-linked items, then embeddings of non-linked items, then daily-brief depth.

## 9. Product surface (UI work follows the `frontend-design` skill, inside the Industry design system)

1. **Wire**: live stream of all items, filters by region, language, outlet, tier, theme, "linked to my holdings", paywall status, with translation toggle and a one-line AI gloss on materiality.
2. **Stories**: threads with timeline, evidence table (independent groups, primary sources), conflict state, impact chain to holdings, price reaction.
3. **Brief**: morning and weekly briefs with citations.
4. **Ask**: chat over the news corpus.
5. **Sources**: health per feed (last success, items/day, error, latency, parser drift), per-outlet policy and rung, an "outlet unreachable" state that never renders as "no news".
6. Feedback controls on every item and claim (wrong entity, duplicate, misread, missing source) feed the benchmark, not model training.

## 10. Phases (each: steps → verify)

### N0 Baseline and benchmark (1 week, start now)
- [x] (Implemented: `news_source_run`, `news_source_health` view; per-source drop reasons.) Instrument per source and stage: items in/out, drop reasons (including the new "unmatched but kept to wire"), latency, cost, errors. → verify: dashboard backed by a SQL view; every drop has a reason.
- [x] (Implemented as `tests/news/news_benchmark.json` with per-segment Wilson intervals, `news/eval.py --set benchmark`. The 150 events are synthetic, written by us, not real labelled history; the live run against a real model is NOT done and needs API keys.) Build 150-event labelled benchmark (holdings, macro, regional, hard negatives, missed examples) stratified by region/language/event type. → verify: reproducible eval run in `news/eval.py`, per-segment recall/precision with intervals.
- [x] (Catalog in `docs/plans/news_source_catalog.md`; from a desk research pass, not live probes.) Probe remaining sources (Section 2 "not yet probed") and record terms/rate notes per source. → verify: source catalog doc rows complete.

### N1 Wire lane and source expansion (1–2 weeks)
- [x] Migration: `news_article` (wire row: source, canonical url, title, summary, lang, title_en, published_at, region, simhash, embedding, linked boolean, fetch_state) next to existing `news_item`; stop dropping unmatched items (store to wire with `linked=false`). → verify: test that an unmatched item is stored, not alerted.
- [x] New config fields `region`, `language`, `paywall`, `state_media`, `FetchPolicy`; new types `news_sitemap`, `gdelt`. Add the section 4 feeds as seeded, individually switchable sources; Telegraph/Caixin/NHK/China Daily marked `needs_alt`. → verify: `news_sources` schema tests; source-health shows each feed live.
- [x] Source health: ETag/If-Modified-Since, backoff, circuit breaker, parser-drift alert, outlet-down banner. → verify: kill a feed in test, banner appears, no "no news".

- [x] (Done for the call sites that exist now: `per_job_monthly_cap_usd`, translation/embedding/search-lead settings, PromptKeys `news_translation`/`news_search_leads`, job type `news_ai_search`. `news_triage`/`news_analysis`/`news_brief` are NOT registered: they have no call site until N6, and there is no `switch_models.py` in this repo to extend.) AI plumbing (6.13): add `per_job_monthly_cap_usd` to `BudgetConfig`; add the `NewsSettings` model/effort fields and `PromptKey`s with default prompts; register job types `news_triage`, `news_analysis`, `news_brief`, `news_ai_search`; add them to `switch_models.py`. → verify: config schema tests; a capped stage degrades without stopping `news_ingest`; per-stage spend shows in `GET /costs/summary`.
- [x] (Implemented as `news/ai_search.py` and job `news_ai_search`, off by default; a test shows a lead is stored at tier 3 and never alerts. The cost-per-call, URL-validity and false-lead measurements are NOT done: they need live keys.) AI search lane (6.12) through the existing `LLM.complete` + `SearchSpec` on the current OpenAI and Google models (no new vendor): measure cost per search call on luna and flash for 20 held tickers, URL validity, unique finds vs the feeds; implement the leads-only `ai_search` source type, reusing corroboration/escalation citations first. → verify: false-lead rate and cost per verified unique event reported; an `ai_search` item alone never alerts (test).

### N2 Full-text ladder (1–2 weeks)
- [x] (Rungs 2 and 4 hooks, 3 via the render hook, stub detector with tests, retention. Scoring 30 real articles per outlet is NOT done: it needs network. Feed text from rung 1 is parsed but not yet persisted.) `news/fulltext.py` implementing rungs 1–4 on top of `PoliteFetcher`/`BrowserRenderer`, trafilatura extractor, truncation/paywall-stub detector, content hash, retention job. → verify: 30 articles per outlet scored for body/date/byline; stub detection test with saved paywall HTML fixtures.

### N3 Language, dedupe, translation (1 week)
- [x] (Implemented and tested with fake embedder/LLM. The JP/ZH/DE pair set exists (`tests/news/crosslang_pairs.json`, synthetic); `python -m steward.news.eval_cluster --live` must be run with a Google key to set `embedding_duplicate_threshold` (default 0.90 is untuned) and prove the "clusters correctly" criterion. The embedding price is provisional.) Language detect, headline translation, multilingual embedding clustering, SimHash stage, syndication independence groups. → verify: JP/ZH/DE benchmark pairs cluster correctly; duplicate burden target met.

### N4 Official multi-country primary sources (2–4 weeks, parallel)
- [x] (Implemented in `news/primary_sources.py`, with the source types `tdnet`, `hkex`, `cninfo`, `dart`, `gazette`, `filings`, each seeded off, matched to holdings by home country and exchange stock code, with per-language event mapping in the announcement rules. EDINET and Companies House were already built as disclosure jobs (`sources/edinet.py`, `sources/companies_house.py`); the `filings` source turns the rows they stored into items, so there is no second fetch and their request budgets hold. Japan therefore has TDnet (free, no key) beside EDINET, England has the Gazette beside RNS and Companies House. Bank of Japan, Bank of England, Bundesbank and Bank of Canada ship as seeded macro RSS sources (feeds probed 200 on 2026-10-10). **Verified live 2026-10-10:** TDnet list pages, HKEXnews title search, CNINFO query and lookup, the four central-bank feeds; the TDnet, HKEX and CNINFO test fixtures are trimmed live responses. **Not verified:** DART (needs `DART_API_KEY`; response shape follows the OpenDART docs), The Gazette (site was in scheduled maintenance; parsing is generic Atom with a whole-name match), EDINET and Companies House (no keys; the earlier modules say the same), FCA National Storage Mechanism (the documented index name was refused, so it is not shipped). **Not done:** SEDAR+ and Bundesanzeiger (the reviewed governed-fetch path); the latency check against the official sites, which needs the sources enabled for a few days; stock-code matching assumes the listing code appears in the holding's listing tickers, which is unproven for Asian listings until a real holding is enabled. Existing saved `event_rules` configs keep their old rule list: the new per-market rules apply to new configs, or after the owner adds them in the Event rules form.) Ship each as its own source type with an event mapping. → verify: per-country filings of a held issuer appear within an agreed latency vs the official site.

### N5 Evidence model and event lifecycle (rev 1 Phase 1, 2–3 weeks)
- [x] (Implemented in `news/evidence.py` and migration `20261029110000`: each stored item carries `evidence_kind` (primary_filing, regulator, company_release, central_bank, wire, outlet, aggregator, lead), `independence_key` and `is_correction`; each story has a `lifecycle` derived in `assess` from the corroboration status plus the evidence, and every change is appended to `story_lifecycle_event` with its reason and the items behind it. Disputed comes from the existing corroboration rules or the owner's "false"; corrected from a correction notice in the story (English, German, French, Japanese, Chinese, Korean markers; a Japanese forecast revision is not one); resolved is the owner closing the story (`POST /stories/{id}/close`); `GET /stories/{id}/evidence` shows why a story is linked, confirmed, disputed or corrected. Links carry a confidence by how they were found, and a story whose links to its holding are all indirect (competitor, supplier, customer, topic) alerts only from `indirect_alert_min_severity` (default 2, a behaviour change from 1). Portfolio watch topics (`portfolio_topics` in the news sources config; rates, tariffs, antitrust, currencies and semiconductors seeded) tag wire items in `news_article.topics`; a tag never links a holding or alerts. **Not done:** entity linking by executives, brands and fund look-through (identifier-backed aliases already come from GLEIF, Wikidata and listing tickers in `entity_alias`); a UI for the evidence view (API only); backfilling provenance on items stored before this migration (their kind is null and they count as no primary evidence); a measured recall/precision change, which needs the live benchmark run.) Typed provenance, independence groups, lifecycle states (reported, corroborated, primary-confirmed, disputed, corrected, resolved), identifier-backed entity linking, watch topics. → verify: rev 1 exit criteria.

### N6 Analysis engine (4–6 weeks, ordered)
- [x] (6.1–6.10 built as `news/extract.py`, `novelty.py`, `impact.py`, `threads.py`, `framing.py`, `claims.py`, `market.py`, `themes.py`, `thesis_link.py`, `brief.py`, run by the jobs `news_analysis` and `news_brief` (`analysis_job.py`), results appended to `story_analysis`; see "Implementation gaps in N6 and N7" for everything that is not done or not measured. **Not done: 6.11 bounded sentiment, the `news_triage` stage, every golden-set eval and the 100-output human adjudication this phase's verify step asks for. No technique has run against a live model.** The four code-only techniques (novelty, thread, market, themes) are on by default; the model techniques are off until listed in `analysis_techniques`.) 6.1 extraction schema + span verification → 6.2 novelty → 6.3 impact chains (needs exposure graph) → 6.4 threads and diffs → 6.5 framing comparison → 6.6 claim check → 6.7 market context → 6.8 themes → 6.9 thesis linkage. Each ships behind its own flag, as a `LLM.complete` call site with a registered prompt, pydantic schema, job type, `ResultCache` key and golden-set eval (section 6.13), starting on luna and moving to flash only where the eval requires it; 6.3 calls the existing `event_reach` exposure path instead of new graph code. → verify: per-technique eval set (see section 11 metrics) run with `news/eval.py --live --model <id>` per provider, human-adjudicated sample of 100 outputs, zero uncited claims in saved output.

### N7 Surface (3–4 weeks, parallel with N6 once 6.1 exists)
- [x] (Pages `/news/wire`, `/news/stories` and `/news/stories/[id]`, `/news/brief`, `/news/ask`, `/news/sources` under one tab row, with mark controls writing to `news_feedback`. **Not done: the Playwright run and screenshot per page this phase's verify step asks for (the pages are type-checked and their helpers unit-tested, but never rendered in a browser); inbox alerts from briefs.**) Wire, Stories, Brief, Ask, Sources pages, feedback controls, inbox alerts through the existing alert routing. → verify: Playwright run of each page once, one screenshot per page per the repo guardrails.

### N8 Evaluation and operations (ongoing)
- [x] (Tooling built; the process around it is not. `news/gate.py` and `eval.py --gate FILE --save-baseline FILE` fail a run when a language, region or event-type segment of 10 or more items is clearly worse than a saved baseline (top of its 95 % Wilson interval below the baseline rate) or the pooled accuracy is under 85 %. `news/eval_analysis.py` runs 22 hand-written golden cases for the code techniques (span verification, thread claim diffs, novelty), offline. `GET /news/ops?days=30` (`news/ops.py`) reports news job cost by job type, cost per useful event, the owner's marks, and incidents: severity-3 stories with no alert sent or queued, and alerts marked not useful. **Not done:** no baseline file exists, because a baseline needs a live model run (`eval.py --set benchmark --live --save-baseline`), so the gate only checks the pooled target today; the gate is not wired into CI or a scheduled job; golden sets for the model techniques (extraction, impact notes, framing, claim checks, thesis links, briefs) are not written; "useful event" is the owner's useful mark or an alert nobody marked not useful, so it is empty until alerts are sent or marks are made; "missed" covers only stories the system saw, not events it never ingested, which need a hand-kept list of misses; no web page shows the ops report; the incident review is a list, with no workflow for closing an incident; there is no scheduled cost or accuracy report.) Benchmark on every change to sources, prompts, models, clustering, severity; segment release gates (language, region, event type); incident review for missed high-severity events; monthly cost per useful event. 

### Implementation gaps in N0–N5 (recorded 2026-10-10)

What is built is code with tests against fakes and recorded fixtures. The items below are what the checkboxes above do not cover. Nothing here was verified against a live model or, unless stated, a live source.

**Verification never run**
- The full backend regression has not completed once (a run was killed for memory; another stopped on a Windows temp-dir permission error). Only the news, wire, N3, N4, N5 and jobs test files were run together.
- `tests/test_news_n3_db.py` had no confirmed clean run in its final state before commit 82f4fa5.
- Live runs needing keys or network: the classifier benchmark against a real model (`news/eval.py --set benchmark --live`); embedding-threshold tuning (`python -m steward.news.eval_cluster --live`, Google key); AI-search cost per call, URL validity and false-lead rate; trafilatura accuracy on 30 real articles per outlet; per-country filing latency against the official sites.
- Web: type-check and vitest for the N4/N5 form changes were started after the code was written; the evidence endpoints have no UI, so no Playwright check exists.

**Data that is synthetic or provisional**
- The 150-event benchmark (`tests/news/news_benchmark.json`) and the JP/ZH/DE pair set (`crosslang_pairs.json`) were written by us, not labelled from real history. The "+20 points recall" target in section 11 cannot be judged until a real labelled set exists.
- `embedding_duplicate_threshold` 0.90 is untuned; the embedding price ($0.15/Mtok) and token counts are estimates, so the cost table in section 8 is still unmeasured.
- The source catalog (`news_source_catalog.md`) comes from desk research, not live probes, except the feeds probed on 2026-10-10.

**N0–N3 not done**
- Job types `news_triage`, `news_analysis`, `news_brief` are not registered (no call sites until N6). There is no `switch_models.py` in this repo, so the plan's "add to switch_models.py" step did not apply.
- Rung-1 feed text is parsed but not persisted to the full-text store; rung 3 (browser render) works only through the render hook.
- The cost dashboard for tokens and dollars per stage per day exists as SQL view data only; `GET /costs/summary` per-stage spend was not checked.
- Cluster merge is deliberately conservative (single-item new cluster, no alert, shared ticker); merged-away duplicates across languages that fail those conditions stay separate.

**N4 not done or unverified**
- SEDAR+ and Bundesanzeiger (the reviewed governed-fetch path) are not built. The FCA National Storage Mechanism was not built at first and is now (see the fourth pass).
- DART (no key), The Gazette (site in maintenance), EDINET and Companies House (no keys) were not exercised live; DART and Gazette parsers follow documentation and generic Atom.
- Holdings are matched to exchange filings by stock code taken from listing tickers; that this works for real Japanese, Hong Kong, Chinese and Korean holdings is unproven. The Gazette matches by whole normalised company name only.
- Existing saved `event_rules` configs keep their old rule list, so the per-market rules apply only to new configs or after the owner adds them.
- The EDINET document link stored by the disclosure job is an API URL that needs a key, so an EDINET item's link is not clickable for reading.
- All six new sources ship disabled; the "within an agreed latency" criterion was never defined or measured. TDnet reads up to 20 pages for each of two days per run; the polling load has not been reviewed.

**N5 not done**
- Entity linking by executives, brands and fund look-through; counterparties beyond the existing competitor, supplier, customer fields.
- ~~No UI for the evidence view or the close/reopen action~~ The Story page (N7) shows the evidence and has Close and Reopen; this line was stale.
- Items stored before migration `20261029110000` have null provenance and count as no primary evidence; no backfill.
- "Resolved" is set only by the owner; there is no automatic resolution.
- `indirect_alert_min_severity` defaults to 2, a behaviour change for stories reached only through competitor, supplier, customer or topic links; its effect on alert volume was not measured.
- Portfolio watch topics tag wire items only; they do not feed stories, ranking or briefs yet (N6/N7).
- The exit criterion "improved benchmark recall without material loss of alert precision" was not measured.

**Housekeeping**
- No rollback (`.down.sql`) files exist for migrations `20261028100000`, `20261028110000`, `20261029100000`, `20261029110000`, unlike earlier migrations.
- The repository moved to `https://github.com/PepaBene/StewardAI.git`; `origin` still points at the old URL.

### Implementation gaps in N6 and N7 (recorded 2026-10-10)

What is built is tested against hand-written fake model answers and a real Postgres. No technique has run against a live model, and no page has been opened in a browser.

**Verification never run**
- Golden sets now exist for extraction, framing, claim checks, sentiment, impact chains and notes (14 cases: path confidence, upstream and downstream reach, attribute chains, hypotheses), thesis links (7: supports, review, breach, owner confirmation, dropped refs) and briefs (7: ranking and its bounded nudges, the checks on a written line, headline fallback, regional order); `python -m steward.news.eval_analysis` runs all 73 with no key. They fix a model's answer and test the code that verifies it, not the model's quality, and were written by the coding assistant, not a person. `news/eval.py --live --model` was not extended; the 100-output human adjudication is not done. The prompts are first drafts.
- No Playwright run or screenshot of the Wire, Stories, Story, Brief, Ask or Sources pages. The pages compile and their helpers are unit-tested, nothing more.
- The migration `20261030100000` has no `.down.sql`.
- Per-stage spend of `news_analysis` and `news_brief` is recorded per job step, but the `GET /costs/summary` view was not checked, and no `per_job_monthly_cap_usd` is set for the two new job types (the default config has none).

**N6 not done**
- 6.11 bounded sentiment (aspect-level direction as a ranking input) and the `news_triage` stage are not built.
- 6.1 extraction runs per story, not per batch: no `BatchCollector`, so no batch pricing.
- 6.2 novelty compares embeddings of stories on the same holding only (not by topic); with no embedding the answer is `new` with the basis `no_embedding`. Surprise is read from the expectation stated in the text; the company-guidance data is not consulted.
- 6.3 chains come from the supply-chain graph (`events/paths.py`, the code behind `event_reach`), so they cover supplier and customer links with the weakest-link confidence. Sectors, countries of revenue, currencies, commodities and rates are not graph edges here; ETF look-through counts only through the node weights. The model writes the sentences and hypotheses only. It needs `OWNER_USER_ID` to build the graph.
- 6.4 the thread diff is code (token overlap and number changes between extracted claims), not a model call, so no `news_thread` prompt exists; "changed" is a heuristic. Thread membership by embedding uses the update-similarity setting, and by headline overlap when there is no embedding.
- 6.5 a framing `conflict` is shown with the story but does not change its corroboration status.
- 6.6 checks only severity-3 stories against primary items already in the story, not against stored filings, IR documents, central-bank data or prices; `contradicted` rests on the model's quote.
- 6.7 the benchmark is the configured index of the holding's country (^spx, ^dax, ^ukx) or the first configured one, not a sector index. Earnings proximity uses `instrument.next_report_date` only. The event study uses the 3-day abnormal move, needs price bars for the holding, and shows groups only from five stories.
- 6.8 themes are the five seeded watch topics, not 20 to 40; no GDELT tone or attention; no weekly narrative summary per theme; the only regime flags are spike and spread.
- 6.9 thesis links read the thesis config (KPIs, exit conditions, why owned) only. Nothing marks a breach from an owner-confirmed story; `breach` needs a primary-confirmed one.
- 6.10 the per-holding brief is written on Mondays for holdings with stories in the week; there is no separate weekly regional digest. Ask uses the new chat tools `search_wire` and `get_story_thread` over full-text search; there is no embedding retrieval and no live `web_news_search` tool, and the chat system prompt was not changed to mention them.
- `analysis_techniques` is a list in the Jobs config; the web form edits it as comma-separated words.
- The job `news_analysis` is on by default (its default techniques cost nothing); `news_brief` is off.

**N7 not done**
- The one-line AI gloss on materiality per wire item is not built; a wire row shows the lifecycle of the story it belongs to, if any.
- Translation shows the English headline with the original beneath it; there is no per-item or global toggle.
- Marks (`news_feedback`) are stored and listed through the API but nothing feeds them into the benchmark yet.
- Brief lines do not raise inbox alerts through the alert routing.
- The Sources page shows what `GET /news/sources/health` returns; per-outlet fetch rung, latency and parser drift are not shown.
- No UI for the theme counts beyond the flagged themes listed on the Brief page.

**Shortcuts taken in the web code (found in the typecheck and lint pass)**
- The API schema marks every response field that has a default as optional. `lib/news/analysis.ts` strips that with a `Sent<T>` type, and two call sites cast API data to it (`story-view.tsx` for the analysis, `stories-view.tsx` for a thread). The cast is true only while the backend keeps sending defaulted fields. The better fix is to emit separate input and output schemas, or required response fields, in `export_openapi`; that was not done.
- The two news feedback schemas and the verify-feedback ones share the name `FeedbackIn`/`FeedbackOut`, so the generated types carry the module prefix (`steward__news__read_analysis__FeedbackIn`). A rename on the news side would remove the long names.
- Three effects (Wire, Story, Brief) set "loading" inside an effect and carry an `eslint-disable react-hooks/set-state-in-effect` comment, as other views in the repo do. They were not moved to a data hook.
- Route types for `/news/stories/[id]` only exist after `next typegen`; `npm run typecheck` does this, a bare `tsc --noEmit` does not.
- Web checks run: `tsc`, eslint on the news folders, and vitest on `src/lib/news` and `src/lib/config`. The full web suite and `npm run lint:colors` were not run.

**Not run at all**
- The full backend regression (only the news tests ran).
- Nothing was exercised against a live model, a live database with real stories, or a browser.

### Gap closure pass (2026-10-10)

A second pass closed the gaps that need only code, tests against fakes and a local Postgres. Nothing in it ran against a live model or live source.

**Closed (code, tests, rollback proven):**
- Rollback files for the five earlier news migrations and for `20261031100000_news_gap_closure`; `test_lifecycle_phase8` applies and rolls back each.
- Monthly caps for `news_analysis` (4 USD), `news_brief` (1.5) and `news_ai_search` (2.5) by default; `news_triage` is a budget job type.
- `news_triage` job (off by default): relevance, topic, entities and a checked one-line gloss per wire item; relevance decides full text for unlinked items. The gloss is stored in `news_article.triage` but no page shows it yet.
- 6.11 bounded sentiment: span-verified aspects, a bounded nudge on brief rank (`sentiment_weight`), and a panel on the Story page.
- 6.1 extraction can go through `BatchCollector` (`batch_extraction`, off by default).
- 6.3 attribute chains (shared sector, country, currency from the owner's classifications); fund constituents link stories to held funds.
- 6.2 novelty scoped by event type as well as holding. 6.7 sector benchmark from `sector_benchmarks`.
- 6.8 about 30 watch topics; GDELT daily tone and attention (`gdelt_themes`, off by default); weekly cited narrative per theme (`theme_narratives`); topic count nudges brief rank.
- 6.9 an owner "confirmed" mark counts as a primary source for a thesis breach. 6.10 regional digest (`region` brief) and theme briefs; morning brief lines can alert (`brief_alert`, off).
- N5 executives, brands and fund constituents as link kinds; rung-1 feed text is stored; stories confirmed or corroborated and quiet for `auto_resolve_days` are resolved.
- Feedback schemas renamed `NewsFeedbackIn`/`NewsFeedbackOut`, removing the long generated names.
- Web: Brief page has region and theme digests and theme tone; Story page shows attribute reach and sentiment; a "confirmed" mark; job form fields for the new settings.

**Still open:**
- Everything that needs a live model, key or network: the benchmark baseline and gate threshold, embedding tuning, AI-search cost, trafilatura accuracy, per-country filing latency and the live checks of DART, The Gazette, EDINET and Companies House; the 100-output human adjudication; a real labelled benchmark.
- SEDAR+ and Bundesanzeiger (FCA NSM is now built); matching holdings to exchange filings by stock code is unproven.
- Chat: embedding retrieval for `search_wire` and a live `web_news_search` tool. The system prompt was not changed; the tools are described in their own schemas, as `search_news` is.
- Ops: an incident workflow, a scheduled report, CI wiring of the gate, golden sets for the model techniques. The alert volume figure is now reported, but whether `indirect_alert_min_severity` 2 is the right value needs weeks of real alerts.
- N4: the EDINET document link still needs a key (no keyless public URL was verified).
- N5: no UI for the evidence view or close/reopen.
- Web: no per-item translation toggle, no wire gloss, no Sources-page fetch-rung or latency columns, effects not moved into a data hook, no browser check of any page.

**Closed in the third pass (2026-10-10):**
- Saved `event_rules` configs: `with_default_services` adds the default announcement rules of any service the saved list has none for (before the catch-all) when the news config loads. Rules the owner already has are untouched and the stored version is not rewritten.
- Provenance backfill: each news run classifies up to 500 stored items that have no evidence kind, with the same rules as new items. An item whose source is gone is classified only if it is tier 2; tier 1 and 3 are left unclassified rather than guessed.
- Ops report: alert latency (median and 95th percentile, minutes from first article to alert) and alert volume split into direct and indirect reach; a Review page under News shows them with cost and incidents.
- Marks as benchmark cases: `GET /news/marks/candidates` lists items and stories marked wrong, in the benchmark's shape with `expected` empty for the owner to fill. Nothing feeds a score or model automatically.
- Framing `conflict` does not change corroboration status. **Decided (delegated by the owner, 2026-10-10):** a framing conflict is one model's reading of how outlets word a story, and only its quotes and numbers are checked against the text, so it must not flip an evidence state that drives alerts. The evidence-backed path already exists: a claim check or web corroboration that finds a contradiction with a qualifying source sets `disputed` (`corroborate.py`). Revisit only if the framing benchmark shows conflicts that match real disputes.
- The model jobs `news_triage`, `news_brief` and `news_ai_search` are now on by default (the owner's choice, 2026-10-10); they run inside the budget guard. A deployment whose stored `jobs` config has no entry for them picks the new defaults up on deploy; one that saved them as off must switch them on.
- The full backend regression was not completed.

### Live checks run 2026-10-10

| Check | Result |
|---|---|
| Companies House | Works; transport and parsing confirmed. |
| The Gazette | Transport and parsing work, but matches are generic supplement pages with no event type. Low value; keep it off the alert path. |
| DART, EDINET | **Not run**: `DART_API_KEY` and `EDINET_API_KEY` are not in `.env`. |
| Classifier benchmark | Live run on `gpt-6-luna`: 99.3 % event-type accuracy. The set is synthetic, so this is a regression baseline (`tests/news/baseline_benchmark.json`), not evidence of real-world accuracy. Anthropic credit is exhausted, so no Claude run. |
| Embedding threshold | No clean separating cosine: at 0.90 recall was 50 % with 18.5 % false merges on hard distractors. Merge threshold stays conservative. |
| Extraction and sentiment, 2 stories | Spans verified. One `status: official` on a media-only story is questionable. Sentiment returned several rows for one entity; fixed by keeping the strongest per entity. |
| Full-text extraction, 14 RSS sources | BBC, Guardian, Nikkei, SCMP extract well (350–6200 chars, bylines). FT and Bloomberg return only a ~400–800 char teaser (paywall) and add little over the feed summary. Sky News and Japan Times return HTTP 403 to the fetcher. |
| Semantic wire search (chat) | Falls back to embeddings when full-text finds fewer than 8; 2 DB tests pass. |

Needs from the owner: `DART_API_KEY`, `EDINET_API_KEY`; Anthropic credit; the repo secret `OPENAI_API_KEY` and variable `NEWS_EVAL_ENABLED=true` for `.github/workflows/news-eval.yml`. Independent labelling and adjudication of a real-history benchmark, and browser verification, need a person.

### Fourth pass (2026-10-10): Claude removed, AI-labelled real benchmark, ops, sources columns

**Claude removed.** No Anthropic client, key, setting, price row, model id or doc remains; providers are OpenAI (`gpt-*`) and Google (`gemini-*`). Migration `20261101100000_remove_claude_models` writes new config versions that replace saved `claude-*` ids (haiku to `gpt-6-luna`, others to `gpt-6.1-sol`), drop their price rows and strip `anthropic` from prompt overrides (not yet applied to the owner's database). `evals/baseline.json` still names old models and must be re-recorded with a live run.

**"Human work" done by AI, and labelled as such.** A person was asked for; the owner chose to have it done as AI. `tests/news/real_benchmark.json` holds 253 real headlines (the owner's stored news plus the latest items of the enabled feeds; English, German, Czech). Each was labelled blind by three AI models (the coding assistant, `gemini-3.8-flash`, `gpt-6.1-sol`); the stored label is the majority, and 13 three-way event-type splits were settled by the coding assistant. Agreement on event type: all three agree on 169 of 253 (67 %); pairs 71 %, 74 % and 83 %. On relevance all three agree on 164 of 253 (65 %), so the relevance label is the weakest. Most items are `other` (161 of 253) because the stored news is mostly EDGAR full-text hits and the feeds are general. **No human reviewed any label**; the set is a regression gate, not proof of accuracy. Live result of the production classifier (`gemini-3.8-flash`) on it: **event type 223/253 = 88.1 %**, rumour 251/253, affected holding 247/253, **relevance 111/253 = 44 %** (re-recorded after a second AI review pass that re-read the disputed items against one written rule and changed 10 event types and 41 relevance flags; the earlier 88.5 % and 69 % are superseded. Relevance fell because the stricter rule counts an item relevant only if it names a held company or is a US or euro-area central-bank or inflation item, and the classifier still marks more items relevant than that). The weak segments are `regulatory_legal` (14/21), `product_business` (14/19), `management_change` (2/4) and GB (11/16); the baseline is `tests/news/real_baseline.json`, set `--set real`. The relevance gap is a real finding: the classifier marks more items relevant than the labellers did, or the labellers were stricter than the owner would be; only the owner can say which.

**Browser verification** of the pages was not done in this pass.

**FCA NSM: built (reverses the decision below).** The NSM web page calls `POST https://api.data.fca.org.uk/search?index=nsm-search` (no key; it needs an `Origin: https://data.fca.org.uk` header); the earlier `Invalid index` was the wrong index name. Checked live 2026-10-10: a `publication_date` range returns about 1,100 disclosures a day with `company`, `lei`, `headline`, `type`, `category_group`, `publication_date` and `download_link` (document at `https://data.fca.org.uk/artefacts/<download_link>`). A company or LEI criterion is rejected by the API, so `collect_fca_nsm` (in `news/primary_sources.py`) reads the last two days newest first (500 per page, at most 12 pages) and keeps disclosures whose issuer name equals a UK holding's whole normalised name. Source type `fca_nsm`, announcement-rule service `fca_nsm`, tier 1, evidence kind `regulator`, migration `20261103100000_source_type_fca_nsm.sql`. Terms of use accepted by the owner. Tests use a fixture shaped like the live response. Not checked: a held UK issuer's real disclosure through the whole pipeline (needs the source enabled), and name equality will miss an issuer whose NSM name differs from the FIRDS name.

**Automation audit (2026-10-10).** Every scheduled data job is in `JobsConfig.worker_schedules()` and every schedule has a handler. Not scheduled by design: `chat`, `research` (on demand), `earnings_check` (enqueued by the earnings calendar), `thesis_verdicts` and `event_escalation` (enqueued by their triggers). Two gaps were found and closed: (1) a saved `news_sources` config never received source types added later, so the N4 sources and the FCA NSM would have needed a manual add; `with_default_sources` now merges the default official-disclosure source of any type the saved list lacks (the stored version is unchanged), and those defaults are on, except DART which needs `DART_API_KEY`; (2) the FCA collector runs inside the scheduled `news_ingest`, no manual step. **Still off by default and therefore manual to switch on, by the owner's cost design:** `news_ai_search`, `news_triage`, `news_brief` (each spends on a model), `pdmr_uk`, `sec_fsds_backfill`, `shadow_snapshot`, `shadow_report`. Eval baselines (`evals/baseline.json`, `tests/news/real_baseline.json`) are re-recorded by hand on purpose: they gate changes and must not move on their own.

**SEDAR+ and Bundesanzeiger: decided not to build (FCA NSM above reversed).**
- SEDAR+ (Canada): no public API; the search is a script-driven session behind a terms-of-use page. Canadian holdings that also file with the SEC (Shopify, Royal Bank of Canada file 40-F and 6-K) are already read through the EDGAR collectors, and the Canadian outlets are RSS sources. Revisit only if the owner holds a Canadian issuer that does not file with the SEC.
- Bundesanzeiger (Germany): the search is captcha-gated and has no feed. German issuer news comes from the Handelsblatt, FAZ and tagesschau feeds and from each company's own investor-relations releases (`ir_releases`). EQS-News (ad-hoc releases) has no RSS at `/feed/` (it returns the HTML page); not built.
- SEDAR+ stays unbuilt: only the paid DDS (SFTP) feed exists. Bundesanzeiger has no official API and its search is captcha-gated; paid third parties only.

**Built in this pass:**
- Sources page: per source, the fetch method of the stored article texts in the last week (feed text, page fetch, browser, official API, with cut-off and failed counts), the median and 95th-percentile delay between publication and first sight, and the average answer time.
- Incident workflow: an incident on the Review report can be acknowledged or resolved with a note (`PUT /news/ops/incidents/{story_id}`, table `news_incident_review`); resolved ones leave the report.
- Scheduled report: the first `news_analysis` run of each ISO week sends the ops report (cost, alerts, latency, AI-search cost, open incidents) through the normal alert routing. It rides the analysis job so no new job type was added; a failure there never fails the analysis.
- AI-search cost: the ops report shows searches, cost, leads, new items stored, cost per search and cost per new item for `news_ai_search`. The lane is off by default and has not been run live, so there is no measured figure yet.
- Golden sets for the model techniques: 14 cases for what the code keeps of an extraction and a sentiment answer (`tests/news/model_golden.json`, run by `python -m steward.news.eval_analysis`, no key needed). Impact notes, framing, claim checks, thesis links and briefs still have none.

**Still open after this pass:** DART and EDINET live checks (no keys); the EDINET document link (no keyless URL verified); the framing-conflict status decision (the owner's); browser verification; a human review of the benchmark labels; re-recording `evals/baseline.json`; applying the Claude-removal migration; the relevance gap above.

Suggested order if time is short: N0 → N1 → N3 → N2 (rungs 1–4) → N6.1–6.4 → N7 Wire/Brief → rest.

## 11. Success measures and owner decisions

| Measure | Pilot target |
|---|---|
| Named-outlet coverage | Bloomberg, FT, Reuters, BBC, Nikkei, SCMP, DW, Handelsblatt, Globe and Mail all ingesting with health green ≥ 98% of polls |
| Material-event recall (benchmark) | +20 points over N0 baseline, no segment (region/language) regressing |
| Alert precision | ≥ 80% before widening alert volume; wire items alone never alert |
| Time from publication to visible | p50 < 5 min for feed/sitemap sources, p95 < 20 min |
| Cited-claim integrity | 100% of saved claims have a verified span; unsupported claims blocked |
| Impact-chain precision | ≥ 85% of "direct/second-order" chains accepted by owner review; hypotheses labelled as such |
| Duplicate burden | −50% items per event; syndication counted once |
| Cost | Cap set after N0 from measured volume; monthly ceiling and warning via `budget_guard` |

**Owner decisions needed:**

- **D1 (decided 2026-10-10)** Budget is $10/month for news, no existing subscriptions: free sources only, spend goes to model inference (section 8).
- **D2 (closed)** Subscription-session fetching dropped for lack of subscriptions.
- **D3** Are state-media sources (Xinhua, CGTN) wanted at all, labelled and capped? Default: on, labelled.
- **D4** Languages to read natively beyond English (Japanese, Chinese, German first?) versus English-language editions only in v1. Default: English editions plus German/Japanese/Chinese via translation in N3.
- **D5** Retention of extracted full text (default 90 days; linked stories longer).

## 12. Risks and controls

| Risk | Control |
|---|---|
| Publisher blocks, layout changes, 402/403s (already seen: Caixin, Telegraph) | feed health + parser drift alerts, per-source kill switch, Google News per-outlet query as fallback, never evade blocks |
| Paywall stub analysed as an article | truncation detector; analysis refuses stub text, uses summary only and says so |
| LLM misreads or invents | span-verified claims, fact/interpretation/speculation fields, eval sets, human-adjudicated sample before each feature ships |
| Over-alerting from a wider wire | wire lane cannot alert; stories need linkage and corroboration; tier-3 and discovery never alert alone |
| Translation errors on JP/ZH/DE | keep originals, show "machine translated", confidence on entity names, native-language entity aliases in linking |
| Cost creep | funnel, caps, per-stage cost telemetry, batch for backfill |
| Source bias or state-media framing | metadata shown to reader, cross-country comparison rather than a hidden score |
| Terms and legal exposure of automated access | personal local use, low rates, no redistribution, per-source policy record, owner decisions D1–D2 recorded |
| Sentiment treated as a forecast | bounded to ranking and annotation (6.11) |

## 13. What stays from rev 1

Source hierarchy (primary first), independence-aware corroboration, event lifecycle, identifier-backed entity linking, watch topics, benchmark with segment gates, provenance per source, user feedback into the benchmark, governed scraping rules for sites without feeds, and the success-measure discipline. Rev 1's licence-gating of every source is relaxed to the personal-use register above; its "no full-text mirror" rule becomes "full text is stored locally for analysis under per-source retention, displayed per `FetchPolicy`, never redistributed".

## 14. References

- [SEC EDGAR APIs](https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-edgar-application-programming-interfaces-apis) · [SEC RSS](https://www.sec.gov/about/rss-feeds)
- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) · [GDELT 2.0 and translation](https://blog.gdeltproject.org/?p=3976) · [GDELT API guide](https://docs.gdeltcloud.com/API_DOCUMENTATION_GUIDE)
- [Reuters RSS ended (2020) and alternatives](https://www.fivefilters.org/category/feed-control/page/2/) · [Reuters Connect / API provider overview](https://providers.apievangelist.com/providers/reuters/) · [LSEG News](https://developers.lseg.com/en/product/news/overview)
- [Bloomberg real-time news feeds (enterprise)](https://bloomberg.com/company/press/bloomberg-launches-customizable-real-time-news-feeds-for-enhanced-systematic-workflows)
- [Trafilatura evaluation](https://trafilatura.readthedocs.io/en/latest/_sources/evaluation.rst.txt) · [Scrapinghub article extraction benchmark](https://www.sourcepulse.org/projects/25501607)
- [Lopez-Lira and Tang, ChatGPT and stock-return predictability](https://arxiv.org/abs/2304.07619)
- [EDINET API catalogue](https://api-catalog.e-gov.go.jp/info/en/apicatalog/view/33) · [Companies House developer specs](https://developer-specs.company-information.service.gov.uk/)
- [Finnhub](https://finnhub.io/docs/api/quote) · [NewsAPI terms](https://newsapi.org/terms) · [Event Registry](https://research.eventregistry.org/) · [Le Monde RSS terms example](https://www.lemonde.fr/en/rss/article/2022/06/23/the-rss-feeds-of-monde-fr_5987755_165.html)
- Live feed probe, section 2: run 2026-10-10 from this machine.

**Unverified in this revision:** EDINET/DART/Companies House/TDnet access details and the Guardian Open Platform terms come from a research pass that checked few sources; confirm each when its N4 task starts. Wire volume per day, model costs and the cited per-subscription Reuters prices are estimates until N0 and the owner's own check.
