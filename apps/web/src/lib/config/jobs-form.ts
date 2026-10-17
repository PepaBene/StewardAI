import { normaliseSeparators } from "@/lib/number-input";
import type { JobsConfig, JobSchedule } from "@/lib/api/client";

/*
 * Form state for the jobs config (`JobsConfig`, generated from the backend's Pydantic
 * model): one entry per schedule, plus the nested settings of the jobs that call a model.
 *
 * Schedules are found by shape, not by name: every property of the body that looks like a
 * `JobSchedule` (an object with a `cron` string) is listed, so a schedule the backend adds
 * later shows up and round-trips even before this file names it. Settings keep the loaded
 * object and override only the fields the form edits, so a field added later is not lost.
 */

export type ScheduleKey = {
  [K in keyof JobsConfig]-?: NonNullable<JobsConfig[K]> extends JobSchedule
    ? K
    : never;
}[keyof JobsConfig];

export type SettingsKey =
  | "earnings_check"
  | "earnings_calendar_settings"
  | "ai_approval"
  | "ir_discovery_settings"
  | "gap_research_settings"
  | "exposure_build_settings"
  | "inbox_settings"
  | "document_index_settings"
  | "news"
  | "digest"
  | "escalation"
  | "macro"
  | "monitoring"
  | "reference"
  | "prices"
  | "calendar"
  | "chat"
  | "review"
  | "ownership"
  | "valuation"
  | "look_through"
  | "xbrl"
  | "fsds";
type Settings<S extends SettingsKey> = NonNullable<JobsConfig[S]>;

/** Labels and the backend's default cron per known schedule (apps/backend/src/steward/config/jobs.py). */
export const SCHEDULES: Record<
  ScheduleKey,
  { label: string; description: string; cron: string }
> = {
  portfolio_sync: {
    label: "Portfolio sync",
    description: "Trading212 positions and cash, FX rates and drift.",
    cron: "30 22 * * *",
  },
  earnings_calendar: {
    label: "Earnings calendar (soon)",
    description:
      "Refresh the report date of holdings reporting soon, and of those with no date.",
    cron: "0 6 * * 0",
  },
  earnings_calendar_full: {
    label: "Earnings calendar (all)",
    description: "Refresh every holding's report date from its IR calendar.",
    cron: "0 5 1 1,4,7,10 *",
  },
  earnings_due: {
    label: "Earnings due",
    description: "Queue an earnings check for holdings that have reported.",
    cron: "0 8 * * *",
  },
  fact_reconcile: {
    label: "Fact reconciliation",
    description:
      "Check stored results figures against the issuers' 10-Q/10-K XBRL.",
    cron: "0 9 * * *",
  },
  reference_data: {
    label: "Reference data",
    description:
      "Identity, listings, legal entities and group structure (OpenFIGI, ESMA, GLEIF, Wikidata).",
    cron: "30 4 * * 1",
  },
  price_history: {
    label: "Price history",
    description:
      "Daily prices (Stooq, Yahoo as the fallback), the PX close and FX history after the close.",
    cron: "15 23 * * 1-5",
  },
  holiday_refresh: {
    label: "Holiday calendar",
    description: "Public holidays for last, this and next year (Nager.Date).",
    cron: "0 4 2 1,7 *",
  },
  macro_collect: {
    label: "Macro data",
    description:
      "FRED, ECB, Eurostat and ČNB macro series (CZEONIA is the Sharpe ratio’s risk-free rate); macro surprises on release days.",
    cron: "30 11 * * *",
  },
  monitor: {
    label: "Monitoring",
    description:
      "Alert on failed jobs, missed runs and holdings with no news coverage.",
    cron: "*/15 * * * *",
  },
  news_ingest: {
    label: "News check",
    description: "Ingest, classify, corroborate and alert.",
    cron: "0 6-22/2 * * *",
  },
  daily_digest: {
    label: "Daily digest",
    description: "Digest of the last 24 hours.",
    cron: "0 7 * * 1-5",
  },
  weekly_digest: {
    label: "Weekly digest",
    description: "Digest of the last 7 days.",
    cron: "0 18 * * 0",
  },
  monthly_digest: {
    label: "Monthly digest",
    description: "Digest of the last 30 days, with where the portfolio stands against the strategy.",
    cron: "30 7 1 * *",
  },
  quarterly_digest: {
    label: "Quarterly digest",
    description: "Digest of the last 91 days, with where the portfolio stands against the strategy.",
    cron: "0 8 1 1,4,7,10 *",
  },
  monthly_review: {
    label: "Monthly review",
    description:
      "Strategy compliance, thesis KPIs and proposed actions for the month. A scheduled run does nothing unless it is the first weekday of the month, so the default fires on the 1st to 3rd and only one of them writes the review.",
    cron: "0 7 1-3 * *",
  },
  ownership_13f: {
    label: "Institutional ownership (13F)",
    description:
      "Load new SEC Form 13F data sets (published quarterly, about 100 MB each).",
    cron: "0 5 5 * *",
  },
  insider_trades: {
    label: "Insider transactions",
    description: "Load structured SEC Forms 3, 4 and 5 for portfolio issuers.",
    cron: "30 6 * * *",
  },
  valuation_refresh: {
    label: "Valuation fundamentals",
    description:
      "EPS, book value and dividends per share from SEC XBRL and ESEF for holdings that filed since the last read (all of them weekly), and the ETF providers' fund P/E and P/B.",
    cron: "45 7 * * *",
  },
  etf_look_through: {
    label: "ETF look-through",
    description:
      "The funds' holdings from iShares and Vanguard (skipped when the as-of date has not moved).",
    cron: "40 6 * * 6",
  },
  sec_xbrl: {
    label: "SEC XBRL fundamentals",
    description:
      "Shares outstanding (the 13F % denominator) and revenue by region / segment from the holdings' latest 10-K, 10-Q and 20-F XBRL. Only filings not parsed yet are downloaded.",
    cron: "40 5 * * 2",
  },
  public_data_collect: {
    label: "Public macro and trade data",
    description:
      "World Bank, OECD, BIS and UN Comtrade observations. Off until the provider audit gate passes.",
    cron: "0 4 * * 0",
  },
  source_audit: {
    label: "Source access audit",
    description:
      "Repeat the endpoint-level free-access audit. Off until test-account keys are set.",
    cron: "0 3 1 * *",
  },
  control_stakes: {
    label: "Control stakes (13D/G)",
    description:
      "Load Schedule 13D/G beneficial-ownership filings for portfolio issuers.",
    cron: "45 6 * * *",
  },
  nport_holdings: {
    label: "Fund holdings (N-PORT)",
    description: "Load public SEC N-PORT fund holdings for held US funds.",
    cron: "15 5 * * 6",
  },
  fund_probes: {
    label: "Fund endpoint probes",
    description:
      "Probe optional Finnhub and FMP fund endpoints for free access. Off by default.",
    cron: "0 4 * * 0",
  },
  fund_characteristics_audit: {
    label: "Fund figures drift check",
    description:
      "Check the iShares and Vanguard fund valuation figures for parser drift.",
    cron: "30 7 * * *",
  },
  fund_issuer_facts: {
    label: "Fund issuer facts",
    description:
      "Read each held fund's replication, launch date, domicile, benchmark and published NAV from its issuer.",
    cron: "15 8 * * 1-5",
  },
  change_alerts: {
    label: "Deal and patent alerts",
    description:
      "An alert for a company-sourced deal story or a new patent family at a holding, once each.",
    cron: "55 7 * * *",
  },
  free_source_alerts: {
    label: "Free-source alerts",
    description:
      "Credit stress, trade exposure, patent status and confirmed company-graph alerts.",
    cron: "5 12 * * *",
  },
  shadow_snapshot: {
    label: "Shadow-run snapshot",
    description:
      "Record the daily free-source shadow-run snapshot. Off by default.",
    cron: "50 23 * * *",
  },
  shadow_report: {
    label: "Shadow-run report",
    description: "Generate the free-source shadow-run report. Off by default.",
    cron: "0 8 * * 1",
  },
  raw_retention: {
    label: "Raw payload retention",
    description: "Prune old raw provider payloads; normalized data stays.",
    cron: "15 3 * * *",
  },
  research_trace_retention: {
    label: "Research trace retention",
    description:
      "Delete checkpoints and tool traces of old finished research and chat jobs.",
    cron: "40 3 * * *",
  },
  zero_cost_gate: {
    label: "Zero-cost gate",
    description: "Verify that no paid endpoint or overage is reachable.",
    cron: "20 3 * * *",
  },
  returns_refresh: {
    label: "Returns refresh",
    description:
      "Compute and store price and total returns for held securities.",
    cron: "45 23 * * 1-5",
  },
  exchange_probes: {
    label: "Exchange probes",
    description:
      "Probe free price endpoints per exchange, one held symbol each. Off by default.",
    cron: "0 5 2 * *",
  },
  ir_releases: {
    label: "Issuer IR releases",
    description:
      "Fetch and version registered issuer IR pages (guidance, consensus, capital return).",
    cron: "20 7 * * *",
  },
  sec_fsds_backfill: {
    label: "SEC statements backfill",
    description:
      "Reads older statements and notes from the SEC Financial Statement and Notes data sets, a couple of sets a day, newest first, until the history set under SEC statement history is in. After that it reads each new month's set.",
    cron: "0 4 * * *",
  },
  pdmr_uk: {
    label: "UK director dealings",
    description:
      "Parse UK PDMR notifications from the RNS stream. Off by default (retrieval unverified).",
    cron: "10 8 * * 1-5",
  },
  graph_review: {
    label: "Relationship and deal review",
    description:
      "Review SEC relationship candidates, extract deal events and reconcile parent changes.",
    cron: "40 7 * * *",
  },
  fmp_company: {
    label: "FMP company profile",
    description:
      "Sector, industry and annual analyst estimates from the Financial Modeling Prep free tier, for holdings with an FMP symbol. Off by default; also needs the FMP provider enabled and a passed endpoint probe.",
    cron: "20 9 * * 6",
  },
  alpha_vantage_statements: {
    label: "Alpha Vantage statements",
    description:
      "Annual statements for holdings with a confirmed listing and an Alpha Vantage symbol, a few per run so the shared daily allowance keeps its reserve. Off by default; also needs the Alpha Vantage provider enabled.",
    cron: "40 9 * * 6",
  },
  epo_patents: {
    label: "EPO patent families",
    description:
      "Patent-family refresh from EPO Open Patent Services for the stalest holdings. Off by default; also needs the EPO provider enabled and its client credentials.",
    cron: "0 10 1 * *",
  },
  companies_house_filings: {
    label: "Companies House filings",
    description:
      "Company profile and filing history of UK holdings that have a Companies House number. Off by default; also needs the Companies House provider enabled and its API key.",
    cron: "10 10 * * 6",
  },
  edinet_filings: {
    label: "EDINET filings",
    description:
      "Japan FSA (EDINET) document list for holdings that have an EDINET code: list and links only, no figures parsed. Off by default; also needs the EDINET provider enabled and its API key.",
    cron: "30 10 * * *",
  },
  twelve_data_corporate_actions: {
    label: "Twelve Data corporate actions",
    description:
      "Dividends and splits of holdings with a confirmed listing, stored as suggestions for you to confirm. Off by default; also needs the Twelve Data provider enabled.",
    cron: "50 10 * * 6",
  },
  yahoo_dividends: {
    label: "Yahoo dividends",
    description:
      "Ten years of dividend events from Yahoo for holdings with a confirmed listing, stored as suggestions and cross-checked against Twelve Data where your plan has it. Keyless; runs daily, a few holdings each time, so every holding is refreshed about weekly.",
    cron: "20 11 * * *",
  },
  dividend_forecast: {
    label: "Dividend forecast",
    description:
      "Confirms dividend events a Trading212 payment matches (when your Checks rule allows it), records each holding's forward schedule and lists events still to confirm under Needs you.",
    cron: "30 8 * * *",
  },
  consensus_snapshot: {
    label: "Consensus snapshot",
    description:
      "Records analyst price targets and stored estimates per holding from the endpoints that passed the access test, daily so the target chart fills in weeks. An endpoint you cannot reach is recorded as such, never as zero.",
    cron: "40 11 * * *",
  },
  insight_access_probe: {
    label: "Insight access test",
    description:
      "Tests the dividend and price-target endpoints with one request each and records the verdict with its date.",
    cron: "0 11 * * 0",
  },
  fair_value_snapshot: {
    label: "Fair-value history",
    description:
      "Records today's fair value of every holding and watchlist name on each basis you have (DCF, multiple path, thesis, peers, consensus). History starts on the first run.",
    cron: "15 9 * * *",
  },
  checks_refresh: {
    label: "Company checks",
    description:
      "Recomputes the five-axis checks and risk checks of every holding and watchlist name, so the scorecard, dividend tiers and screener agree with the company page.",
    cron: "45 9 * * *",
  },
  ai_approvals: {
    label: "AI approvals",
    description:
      "Approves suggested listings and peers with AI (and refreshes Finnhub peer candidates first). Every decision is recorded and you can overturn it.",
    cron: "10 7 * * *",
  },
  autopilot: {
    label: "Autopilot",
    description:
      "Settles the review queues (held facts, graph edges, disclosure reviews and more) with rules and models, logs every decision and escalates what it cannot settle. Every decision can be undone from the Autopilot page.",
    cron: "20 */6 * * *",
  },
  autopilot_calibration: {
    label: "Autopilot calibration",
    description:
      "Checks the autopilot's decisions against your own calls and a stronger model's audit, then lets a queue act on its own once it is right often enough, or pulls it back when it is not.",
    cron: "40 7 * * 0",
  },
  watchdog: {
    label: "Watchdog",
    description:
      "Retries a failed collector, re-runs the refresh behind a data-quality issue, lifts old provider blocks, and tells you which API key is missing or when the AI budget will run out. What it cannot fix goes to Needs you.",
    cron: "10 * * * *",
  },
  ir_discovery: {
    label: "Investor page discovery",
    description:
      "Finds investor-relations pages for holdings that have none, by web search; checks them and has AI approve them. Pages you set yourself are never changed.",
    cron: "30 7 * * 1",
  },
  news_ai_search: {
    label: "AI news search",
    description:
      "Asks a search-capable model for recent news on holdings the wire covers thinly. Results are leads at the lowest tier and never alert on their own. Off by default because it spends on web searches.",
    cron: "20 6 * * *",
  },
  news_triage: {
    label: "News triage",
    description:
      "Reads new wire items on a cheap model and records relevance, topic, named entities and a one-line gloss. Decides which unlinked items are fetched in full. Off by default because it spends on a model.",
    cron: "10 * * * *",
  },
  news_analysis: {
    label: "News analysis",
    description:
      "Analyses new stories: novelty, threads, market context and themes in code, plus the model techniques chosen in the news settings (event extraction, impact chains, framing, claim checks, thesis links).",
    cron: "25 */3 * * *",
  },
  news_brief: {
    label: "News brief",
    description:
      "Writes the morning brief, and on Mondays the weekly and per-holding briefs, from stored stories. Every line cites its items. Off by default because it spends on a model.",
    cron: "30 6 * * *",
  },
  gap_research: {
    label: "Gap research",
    description:
      "Last resort for what the data sources left empty: searches reliable web pages for the next report date, shares outstanding and a description. AI approves each fact against source tiers; you can overturn it.",
    cron: "0 8 * * 3",
  },
  exposure_build: {
    label: "Exposure builder",
    description:
      "Builds each holding's revenue-by-country exposure from its latest annual SEC filing. Only single countries are mapped; your own exposure entries are never changed, and a row you delete is not rebuilt.",
    cron: "50 5 * * 2",
  },
  inbox_scan: {
    label: "Agent inbox scan",
    description:
      "Looks for new escalation notes and thesis-breach alerts and starts an agent run for each, which leaves an inbox item. Nothing it proposes starts until you confirm it.",
    cron: "10 * * * *",
  },
  document_index: {
    label: "Document index",
    description:
      "Splits new filings, reports, theses, notes and news into passages and embeds them for document search in chat. Embedding costs a little per run; the passages embedded per run are capped in the job settings.",
    cron: "20 3 * * *",
  },
  factor_returns: {
    label: "Factor returns",
    description:
      "Downloads the Ken French Data Library daily factor returns (market, size, value, profitability, investment, momentum) for the Developed, Europe and US markets. The factor exposures on the risk pages use them. The library adds a month at a time.",
    cron: "30 5 * * 1",
  },
  disclosure_sources: {
    label: "Disclosure source tests",
    description:
      "Re-tests every disclosure source each day and stores what it answered. A source that stops answering is switched off; the SEC sources are switched on again when they pass.",
    cron: "0 6 * * *",
  },
  disclosure_ingest: {
    label: "Filing ingestion",
    description:
      "Fetches, stores and parses the newest annual and quarterly SEC filings of your holdings. Switch it on after the SEC sources pass their access test; a filing that fails is reported, not skipped silently.",
    cron: "40 6 * * 1-5",
  },
  disclosure_identifiers: {
    label: "Identifier suggestions",
    description:
      "Looks up missing LEIs and FIGIs and stores them as suggestions. Nothing is confirmed until you decide it.",
    cron: "20 6 * * 6",
  },
  disclosure_deals: {
    label: "Deal filings",
    description:
      "Reads recent 8-K, S-4, proxy and tender-offer filings of your holdings and stores the deals, their state and the terms they state. A filing that does not name two parties and a deal type is counted, never guessed.",
    cron: "50 6 * * 1-5",
  },
  disclosure_esef: {
    label: "European annual reports",
    description:
      "Checks filings.xbrl.org for new ESEF reports of holdings with a confirmed LEI and stores each with the date it was published. It reads no values; statements come through the statement pipeline.",
    cron: "0 7 * * 1-5",
  },
  disclosure_estimates: {
    label: "Consensus files",
    description:
      "Fetches each issuer consensus file you have enabled and stores a new version only when it changed. A file it cannot read is reported with the reason, never stored half-read.",
    cron: "30 6 * * 1-5",
  },
  disclosure_alerts: {
    label: "Disclosure alerts",
    description:
      "Scans verified filings for new insider trades, major-holder changes and relationship changes, and e-mails the new ones grouped by ticker when e-mail is configured.",
    cron: "15 13 * * *",
  },
};
export const SCHEDULE_KEYS = Object.keys(SCHEDULES) as ScheduleKey[];

/** Schedules that start off: they spend a provider's free allowance or need a key, so you switch them on yourself. */
const OFF_BY_DEFAULT: readonly ScheduleKey[] = [
  "fmp_company",
  "alpha_vantage_statements",
  "epo_patents",
  "companies_house_filings",
  "edinet_filings",
  "twelve_data_corporate_actions",
];
export const DEFAULT_TIMEZONE = "Europe/Prague";

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_JOBS: JobsConfig = {
  schema_version: 1,
  catch_up_on_start: true,
  ...(Object.fromEntries(
    SCHEDULE_KEYS.map((k) => [
      k,
      {
        enabled: !OFF_BY_DEFAULT.includes(k),
        cron: SCHEDULES[k].cron,
        timezone: DEFAULT_TIMEZONE,
      },
    ]),
  ) as Record<ScheduleKey, JobSchedule>),
  earnings_check: {
    extraction_model: "gpt-6.1-sol",
    locate_model: "gpt-6.1-sol",
    effort: "high",
    locate_effort: null,
    result_cache_days: 0,
    max_steps: 40,
    max_searches: 20,
    check_days_after_report: [1, 2, 4],
    max_document_mb: 20,
    xbrl_for_filings: true,
    kpi_history_periods: 8,
  },
  earnings_calendar_settings: {
    model: "gpt-6-luna",
    effort: null,
    lookahead_days: 120,
    refresh_within_days: 14,
    use_batch: true,
    batch_wait_minutes: 60,
  },
  ai_approval: {
    model: "gpt-6.1-sol",
    effort: null,
    listings: true,
    peers: true,
    refresh_peers: true,
    peer_refresh_days: 30,
    peer_refresh_per_run: 8,
    min_confidence: 0.8,
    max_confirmed_peers: 8,
    max_calls_per_run: 40,
    use_sic: true,
    rules_first: true,
    use_batch: true,
    batch_wait_minutes: 60,
  },
  ir_discovery_settings: {
    model: "gpt-6.1-sol",
    effort: null,
    max_searches: 4,
    holdings_per_run: 6,
    use_batch: true,
    batch_wait_minutes: 60,
    probe_first: true,
    browser_fallback: false,
    cooldown_days: 30,
    min_confidence: 0.8,
    denied_domains: [
      "wikipedia.org",
      "linkedin.com",
      "facebook.com",
      "x.com",
      "twitter.com",
      "youtube.com",
      "reddit.com",
      "seekingalpha.com",
      "finance.yahoo.com",
      "marketwatch.com",
      "investing.com",
      "stocktwits.com",
    ],
    robots_exempt_domains: [],
  },
  exposure_build_settings: {
    model: "gpt-6.1-sol",
    effort: null,
    filings_per_run: 6,
    extract_inputs: true,
    extract_dependencies: true,
    map_products: true,
    use_batch: false,
    batch_wait_minutes: 60,
  },
  inbox_settings: {
    enabled: true,
    model: "gpt-6-luna",
    effort: "low",
    max_steps: 8,
    lookback_days: 7,
    max_per_scan: 5,
    min_severity: 2,
  },
  document_index_settings: {
    embedding_model: "gemini-embedding-2",
    embedding_dimensions: 768,
    embed_per_run: 1500,
    filings_per_run: 20,
    reports_per_run: 200,
    index_disclosures: true,
    disclosure_alert_days: 365,
    index_agent_notes: true,
    news_per_run: 600,
    news_days: 365,
  },
  gap_research_settings: {
    model: "gpt-6.1-sol",
    effort: null,
    slots: ["next_report_date", "shares_outstanding", "company_description"],
    max_searches: 3,
    slots_per_run: 8,
    use_batch: true,
    batch_wait_minutes: 60,
    browser_fallback: false,
    cooldown_days: 30,
    min_confidence: 0.8,
    t1_domains: [
      "sec.gov",
      "edinet-fsa.go.jp",
      "esma.europa.eu",
      "londonstockexchange.com",
      "jpx.co.jp",
      "nasdaq.com",
      "nyse.com",
      "euronext.com",
      "deutsche-boerse.com",
      "pse.cz",
      "ecb.europa.eu",
      "federalreserve.gov",
    ],
    t2_domains: [
      "reuters.com",
      "bloomberg.com",
      "ft.com",
      "wsj.com",
      "barrons.com",
      "morningstar.com",
      "marketscreener.com",
      "globenewswire.com",
      "prnewswire.com",
      "businesswire.com",
    ],
    t3_domains: ["wikipedia.org", "wikidata.org"],
    denied_domains: [
      "linkedin.com",
      "facebook.com",
      "x.com",
      "twitter.com",
      "youtube.com",
      "reddit.com",
      "stocktwits.com",
      "seekingalpha.com",
      "fool.com",
      "investing.com",
    ],
  },
  news: {
    classification_model: "gpt-6-luna",
    classification_effort: null,
    classification_batch_size: 50,
    classification_use_batch: true,
    classification_batch_wait_minutes: 30,
    rules_first_classification: true,
    corroboration_model: "gpt-6-luna",
    corroboration_effort: null,
    corroboration_max_searches: 10,
    corroboration_rounds: 3,
    corroboration_search_via_api: false,
    recheck_hours: 48,
    max_items_per_run: 10_000,
    edgar_fts_max_hits: 100,
    translation_model: "gpt-6-luna",
    translation_effort: null,
    translation_per_run: 300,
    search_lead_model: "gpt-6-luna",
    search_lead_effort: null,
    ai_search_provider: "model",
    ai_search_max_searches: 3,
    ai_search_max_holdings: 10,
    ai_search_thin_below: 3,
    ai_search_days: 3,
    analysis_techniques: ["novelty", "thread", "market", "themes"],
    extraction_model: "gpt-6-luna",
    extraction_effort: null,
    analysis_model: "gemini-3.8-flash",
    analysis_effort: null,
    brief_model: "gemini-3.8-flash",
    brief_effort: null,
    analysis_per_run: 25,
    analysis_min_severity: 1,
    framing_min_outlets: 3,
    novelty_window_days: 30,
    novelty_repeat_similarity: 0.88,
    novelty_update_similarity: 0.72,
    thread_window_days: 21,
    brief_max_lines: 12,
    brief_alert: false,
    gdelt_themes: false,
    gdelt_max_themes: 6,
    theme_narratives: true,
    triage_model: "gpt-6-luna",
    triage_effort: null,
    triage_per_run: 200,
    triage_fulltext_relevance: 2,
    batch_extraction: false,
    batch_analysis: false,
    reuse_repeat_analysis: false,
    sector_benchmarks: {},
    auto_resolve_days: 14,
    sentiment_weight: 0.25,
    indirect_alert_min_severity: 2,
    embedding_model: "gemini-embedding-2",
    embedding_dimensions: 768,
    embedding_per_run: 600,
    embedding_duplicate_threshold: 0.9,
    duplicate_window_hours: 36,
    fulltext_per_run: 40,
    fulltext_browser_fallback: false,
  },
  digest: {
    model: "gpt-6.1-sol",
    effort: null,
    use_batch: true,
    batch_wait_minutes: 60,
    min_severity_summarised: 2,
    min_severity_listed: 1,
    daily_window_hours: 24,
    weekly_window_days: 7,
    monthly_window_days: 30,
    quarterly_window_days: 91,
    long_digest_min_severity_step: 1,
    monthly_max_material_stories: 40,
    quarterly_max_material_stories: 60,
  },
  escalation: {
    enabled: true,
    model: "gpt-6.1-sol",
    effort: null,
    max_steps: 30,
    max_searches: 15,
    search_via_api: false,
    min_severity: 2,
    source_tiers: [1, 2],
    price_move_news_days: 3,
    price_move_news_items: 6,
  },
  macro: {
    history_years: 10,
    geos: ["CZ", "DE", "EA21"],
    eurostat: [
      {
        key: "hicp",
        label: "HICP inflation, annual rate",
        dataset: "prc_hicp_minr",
        filters: { unit: "RCH_A", coicop18: "TOTAL" },
        unit: "%",
      },
      {
        key: "unemployment",
        label: "Unemployment rate",
        dataset: "une_rt_m",
        filters: { s_adj: "SA", age: "TOTAL", sex: "T", unit: "PC_ACT" },
        unit: "%",
      },
      {
        key: "gdp",
        label: "GDP, change on previous quarter",
        dataset: "namq_10_gdp",
        filters: { unit: "CLV_PCH_PRE", s_adj: "SCA", na_item: "B1GQ" },
        unit: "%",
      },
      {
        key: "industrial_production",
        label: "Industrial production, change on previous month",
        dataset: "sts_inpr_m",
        filters: {
          indic_bt: "PRD",
          nace_r2: "B-D",
          s_adj: "SCA",
          unit: "PCH_PRE",
        },
        unit: "%",
      },
      {
        key: "sentiment",
        label: "Economic sentiment indicator",
        dataset: "ei_bssi_m_r2",
        filters: { indic: "BS-ESI-I", s_adj: "SA" },
        unit: "index",
      },
    ],
    fred: [
      {
        key: "us_cpi",
        label: "US CPI inflation, annual rate",
        series_id: "CPIAUCSL",
        units: "pc1",
        unit: "%",
        policy_rate: false,
      },
      {
        key: "us_unemployment",
        label: "US unemployment rate",
        series_id: "UNRATE",
        units: "lin",
        unit: "%",
        policy_rate: false,
      },
      {
        key: "fed_funds",
        label: "Fed funds effective rate",
        series_id: "DFF",
        units: "lin",
        unit: "%",
        policy_rate: true,
      },
      {
        key: "us_10y",
        label: "US 10-year Treasury yield (the rates factor of the risk model)",
        series_id: "DGS10",
        units: "lin",
        unit: "%",
        policy_rate: false,
      },
    ],
    ecb: [
      {
        key: "ecb_deposit_rate",
        label: "ECB deposit facility rate",
        flow_key: "FM/D.U2.EUR.4F.KR.DFR.LEV",
        unit: "%",
        policy_rate: true,
      },
    ],
    cnb: [
      {
        key: "czeonia",
        label: "CZEONIA (CZK overnight rate)",
        series: "czeonia",
        unit: "%",
        policy_rate: false,
      },
      {
        key: "cnb_repo_2w",
        label: "ČNB 2-week repo rate",
        series: "repo_2w",
        unit: "%",
        policy_rate: true,
      },
    ],
  },
  monitoring: {
    alert_failed_jobs: [
      "portfolio_sync",
      "news_ingest",
      "earnings_check",
      "daily_digest",
      "weekly_digest",
      "reference_data",
      "price_history",
      "macro_collect",
    ],
    stuck_run_minutes: 240,
    missed_run_grace_minutes: 90,
    news_coverage_hours: 48,
  },
  reference: {
    openfigi_refresh_days: 30,
    wikidata_enabled: true,
    wikidata_refresh_days: 30,
    max_subsidiary_aliases: 50,
    wikidata_languages: ["en", "de", "cs"],
  },
  prices: {
    benchmark_symbols: ["^spx"],
    history_years: 10,
    reference_history_years: 20,
    plausibility_tolerance_pct: 1,
    market_strip: [
      { symbol: "^spx", label: "S&P 500", yahoo_symbol: "^GSPC" },
      { symbol: "^stoxx", label: "STOXX Europe 600", yahoo_symbol: "^STOXX" },
      { symbol: "^px", label: "PX" },
    ],
    yahoo_daily_fallback: true,
    intraday_enabled: true,
    intraday_interval_minutes: 15,
    intraday_retention_days: 5,
    intraday_cache_seconds: 120,
  },
  calendar: {
    holiday_countries: ["CZ", "US", "DE", "GB"],
    exchange_closures: [],
  },
  chat: {
    model: "gpt-6.1-sol",
    effort: null,
    escalation_model: null,
    escalation_effort: null,
    max_steps: 30,
    max_searches: 15,
    search_via_api: false,
    search_cache_days: 3,
    cache_retention: "off",
    history_messages: 100,
    source_tiers: [1, 2],
    rules_first: true,
  },
  review: {
    model: "gpt-6.1-sol",
    effort: "high",
    max_steps: 10,
    use_batch: true,
    batch_wait_minutes: 60,
    liquidity_days: 1,
    holder_drop_flag_pct: 10,
  },
  ownership: {
    enabled: true,
    index_url:
      "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
    datasets_per_run: 2,
    history_datasets: 8,
    tracked_managers: [],
    top_holders: 10,
  },
  valuation: {
    enabled: true,
    full_refresh_days: 7,
    esef_max_age_months: 18,
    finnhub_cross_check: true,
    alpha_vantage_fallback: true,
  },
  // Funds, benchmark proxies and issuer groups: the backend's defaults (CSPX 253743, VWCE 9679).
  look_through: {
    enabled: true,
    ishares_api_url:
      "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data",
    ishares_site: "ishares-uk",
    ishares_locale: "en_GB",
    ishares_spreadsheet_fallback: true,
    vanguard_graphql_url: "https://www.ie.vanguard/gpx/graphql",
    vanguard_consumer_id: "ie0",
    cached_fallback_days: 45,
    stale_after_days: 60,
    keep_snapshots: 12,
  },
  xbrl: {
    enabled: true,
    quarterly: true,
    read_ads_ratio: true,
    max_document_mb: 40,
  },
  fsds: {
    enabled: true,
    history_months: 60,
    datasets_per_run: 2,
  },
};

/** Model ids offered as suggestions; any `gpt-…` or `gemini-…` id is accepted. */
export const MODEL_SUGGESTIONS = [
  "gpt-6.1-sol",
  "gpt-6-luna",
  "gemini-3.8-flash",
];
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export const EFFORT_OPTIONAL_HINT = "Model default: effort is not sent.";
export const TIERS = [1, 2, 3] as const;

/** "2, 1, 2" -> [1, 2]: outlet tiers, ascending and unique; anything else is dropped. */
export function parseTiers(text: string): number[] {
  const picked = new Set(text.split(/[\s,;]+/).map(Number));
  return TIERS.filter((t) => picked.has(t));
}

export type SettingField<S extends SettingsKey> = {
  key: keyof Settings<S> & string;
  label: string;
  /**
   * `lines`: a string list, one per line. `codes`: a list of upper-case codes, separated by
   * commas or spaces. `words`: a list of identifiers as typed, separated by commas or spaces.
   * `number`: a decimal. `effort-optional`: an effort or "" for unset (null: not sent, the
   * model's default). `tiers`: outlet tiers 1-3, edited as checkboxes. `model-optional`: a
   * model id or "" for unset (null: the feature is off). `choice`: one of `options`.
   */
  kind:
    | "model"
    | "model-optional"
    | "choice"
    | "text"
    | "int"
    | "number"
    | "bool"
    | "effort"
    | "effort-optional"
    | "int-list"
    | "severity"
    | "lines"
    | "symbol-lines"
    | "codes"
    | "words"
    | "tiers";
  hint?: string;
  min?: number;
  max?: number;
  options?: readonly { value: string; label: string }[];
};

export const SETTINGS: {
  [S in SettingsKey]: {
    title: string;
    description: string;
    fields: SettingField<S>[];
  };
} = {
  earnings_check: {
    title: "Earnings check",
    description:
      "Find the release, extract the figures, validate them and judge each KPI.",
    fields: [
      { key: "extraction_model", label: "Extraction model", kind: "model" },
      {
        key: "locate_model",
        label: "Locate model",
        kind: "model",
        hint: "Searches for the release when EDGAR has none.",
      },
      { key: "effort", label: "Extraction effort", kind: "effort" },
      {
        key: "locate_effort",
        label: "Locate effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "result_cache_days",
        label: "Days an identical search or read is kept",
        kind: "int",
        min: 0,
        max: 30,
        hint: "0: every check is fresh. Above 0, re-checking the same issuer within these days costs nothing.",
      },
      {
        key: "max_steps",
        label: "Step cap per run",
        kind: "int",
        min: 2,
        max: 200,
        hint: "Model calls plus document fetches.",
      },
      {
        key: "max_searches",
        label: "Web searches per run",
        kind: "int",
        min: 0,
        max: 100,
      },
      {
        key: "check_days_after_report",
        label: "Days after the report date to check",
        kind: "int-list",
        hint: "Comma separated, e.g. 1, 2, 4 (retries until the release is found).",
      },
      {
        key: "max_document_mb",
        label: "Largest document (MB)",
        kind: "int",
        min: 1,
        max: 32,
      },
      {
        key: "kpi_history_periods",
        label: "KPI history (periods)",
        kind: "int",
        min: 1,
        max: 40,
        hint: "Past quarters (or years, for annual KPIs) judged for each KPI.",
      },
    ],
  },
  earnings_calendar_settings: {
    title: "Earnings calendar",
    description:
      "Next report dates: Finnhub for US issuers, the IR calendar page otherwise.",
    fields: [
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Reads report dates off IR calendar pages.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "lookahead_days",
        label: "Look ahead (days)",
        kind: "int",
        min: 7,
        max: 400,
      },
      {
        key: "refresh_within_days",
        label: "Weekly run: holdings reporting within (days)",
        kind: "int",
        min: 1,
        max: 120,
        hint: "The quarterly run refreshes every holding.",
      },
      {
        key: "use_batch",
        label: "Read the IR pages through a provider batch (half price)",
        kind: "bool",
        hint: "Only for OpenAI and Google models; the run waits for the batch.",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 1440,
        hint: "After this the batch is cancelled and the pages are read directly.",
      },
    ],
  },
  ai_approval: {
    title: "AI approvals",
    description:
      "Suggested listings and peers are approved by AI; you can overturn any decision, and your decision is final.",
    fields: [
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Judges each suggestion from the evidence given.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      { key: "listings", label: "Approve suggested listings", kind: "bool" },
      {
        key: "peers",
        label: "Approve or reject suggested peers",
        kind: "bool",
      },
      {
        key: "refresh_peers",
        label: "Refresh Finnhub peer candidates first",
        kind: "bool",
      },
      {
        key: "peer_refresh_days",
        label: "Refresh peers older than (days)",
        kind: "int",
        min: 1,
        max: 365,
      },
      {
        key: "peer_refresh_per_run",
        label: "Holdings refreshed per run",
        kind: "int",
        min: 1,
        max: 40,
      },
      {
        key: "min_confidence",
        label: "Minimum confidence",
        kind: "number",
        hint: "Below this a suggestion stays as it is, for the next run or for you. 0.5 to 1.",
      },
      {
        key: "max_confirmed_peers",
        label: "Peers confirmed per holding",
        kind: "int",
        min: 1,
        max: 20,
      },
      {
        key: "max_calls_per_run",
        label: "AI calls per run",
        kind: "int",
        min: 1,
        max: 200,
        hint: "Cost guard.",
      },
      {
        key: "rules_first",
        label: "Settle clear listings in code first",
        kind: "bool",
        hint: "Exact matches are confirmed and clear mismatches rejected without the AI; each decision shows whether a rule or the model made it.",
      },
      {
        key: "use_sic",
        label: "Use SEC industry codes for peers",
        kind: "bool",
        hint: "Same code and named as a competitor in the profile: confirmed in code. The AI sees every code as evidence.",
      },
      {
        key: "use_batch",
        label: "Judge through a provider batch (half price)",
        kind: "bool",
        hint: "Only for OpenAI and Google models; the run waits for the batch.",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 1440,
      },
    ],
  },
  ir_discovery_settings: {
    title: "Investor page discovery",
    description:
      "Finds each holding's investor-relations pages by web search; the deny list of domains is edited in the config JSON.",
    fields: [
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Searches for the pages and approves them.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_searches",
        label: "Web searches per holding",
        kind: "int",
        min: 1,
        max: 10,
        hint: "The expensive part.",
      },
      {
        key: "holdings_per_run",
        label: "Holdings per run",
        kind: "int",
        min: 1,
        max: 40,
      },
      {
        key: "cooldown_days",
        label: "Retry a holding after (days)",
        kind: "int",
        min: 1,
        max: 365,
      },
      {
        key: "min_confidence",
        label: "Minimum confidence",
        kind: "number",
        hint: "Below this a page is not applied. 0.5 to 1.",
      },
      {
        key: "probe_first",
        label: "Try common investor-page paths before searching",
        kind: "bool",
        hint: "Saves the web search when a probe loads a page naming the company.",
      },
      {
        key: "use_batch",
        label: "Run through a provider batch (half price)",
        kind: "bool",
        hint: "Only for OpenAI and Google models; the run waits for the batch.",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 1440,
      },
      {
        key: "browser_fallback",
        label: "Retry refused pages in a browser",
        kind: "bool",
        hint: "For a 403/429 or a JavaScript-only page. Obeys robots.txt (except sites in robots_exempt_domains, edited in the config JSON), never logs in or passes a challenge.",
      },
    ],
  },
  exposure_build_settings: {
    title: "Exposure builder",
    description:
      "The AI steps of the exposure build. The country split from XBRL needs no AI. A figure is stored only when a verbatim quote in the filing prints it.",
    fields: [
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Reads annual reports and maps product lines.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "filings_per_run",
        label: "Reports per run",
        kind: "int",
        min: 1,
        max: 40,
        hint: "Cost guard. Each report is read once.",
      },
      {
        key: "extract_inputs",
        label: "Read currency and commodity inputs",
        kind: "bool",
      },
      {
        key: "extract_dependencies",
        label: "Read customers and suppliers",
        kind: "bool",
        hint: "The AI approves each one; you can overturn it on the AI decisions page.",
      },
      {
        key: "map_products",
        label: "Map product lines to trade codes",
        kind: "bool",
        hint: "Stored as an estimate.",
      },
      {
        key: "use_batch",
        label: "Read the filings through a provider batch (half price)",
        kind: "bool",
        hint: "Only for OpenAI and Google models; the run waits for the batch.",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 5,
        max: 240,
        hint: "After this the filings are read directly.",
      },
    ],
  },
  inbox_settings: {
    title: "Inbox agents",
    description:
      "Each new escalation note, thesis alert, disclosure alert, portfolio breach or KPI breach starts one bounded, read-only run that leaves an item in the Inbox. A run never trades or edits a thesis; a proposed job starts only when you confirm it.",
    fields: [
      { key: "enabled", label: "Look into new events", kind: "bool" },
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Reads the records and writes the item.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_steps",
        label: "Model calls per run",
        kind: "int",
        min: 2,
        max: 30,
        hint: "Cost guard.",
      },
      {
        key: "lookback_days",
        label: "Look back (days)",
        kind: "int",
        min: 1,
        max: 60,
        hint: "Older events are listed in the Inbox for you to run by hand.",
      },
      {
        key: "max_per_scan",
        label: "Runs per hourly scan",
        kind: "int",
        min: 1,
        max: 50,
        hint: "Cost guard. Newest events first.",
      },
      {
        key: "min_severity",
        label: "Lowest severity that starts a run",
        kind: "int",
        min: 0,
        max: 3,
        hint: "Applies to thesis alerts and portfolio breaches.",
      },
    ],
  },
  document_index_settings: {
    title: "Document search index",
    description:
      "The passages behind search_documents: filings, your reports, theses and notes, news, the disclosure register's prose and your agent notes. Text is searchable at once; embedding is capped per run, newest first.",
    fields: [
      {
        key: "embedding_model",
        label: "Embedding model",
        kind: "model",
        hint: "Changing it re-embeds every passage over the following runs.",
      },
      {
        key: "embedding_dimensions",
        label: "Embedding dimensions",
        kind: "int",
        min: 128,
        max: 3072,
        hint: "768 matches the vector index; others are searched without it.",
      },
      {
        key: "embed_per_run",
        label: "Passages embedded per run",
        kind: "int",
        min: 0,
        max: 20000,
        hint: "Cost guard. 0 turns embedding off; keyword search still works.",
      },
      {
        key: "filings_per_run",
        label: "Filings split per run",
        kind: "int",
        min: 0,
        max: 200,
      },
      {
        key: "reports_per_run",
        label: "Reports indexed per run",
        kind: "int",
        min: 0,
        max: 2000,
      },
      {
        key: "news_per_run",
        label: "News items indexed per run",
        kind: "int",
        min: 0,
        max: 5000,
      },
      {
        key: "news_days",
        label: "News looked back (days)",
        kind: "int",
        min: 7,
        max: 730,
      },
      {
        key: "index_disclosures",
        label: "Index the disclosure register's prose",
        kind: "bool",
        hint: "Relationship passages and disclosure alerts.",
      },
      {
        key: "disclosure_alert_days",
        label: "Disclosure alerts looked back (days)",
        kind: "int",
        min: 7,
        max: 1825,
      },
      {
        key: "index_agent_notes",
        label: "Index your agent notes",
        kind: "bool",
      },
    ],
  },
  gap_research_settings: {
    title: "Gap research",
    description:
      "Web research for empty slots only; the domain tiers (company site and regulators, press, reference, denied) are edited in the config JSON.",
    fields: [
      {
        key: "model",
        label: "Model",
        kind: "model",
        hint: "Searches for facts and approves them.",
      },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_searches",
        label: "Web searches per slot",
        kind: "int",
        min: 1,
        max: 10,
        hint: "The expensive part.",
      },
      {
        key: "slots_per_run",
        label: "Slots per run",
        kind: "int",
        min: 1,
        max: 60,
        hint: "Cost guard.",
      },
      {
        key: "cooldown_days",
        label: "Retry an unfilled slot after (days)",
        kind: "int",
        min: 1,
        max: 365,
      },
      {
        key: "min_confidence",
        label: "Minimum confidence",
        kind: "number",
        hint: "Below this a fact waits for review. 0.5 to 1.",
      },
      {
        key: "use_batch",
        label: "Run through a provider batch (half price)",
        kind: "bool",
        hint: "Only for OpenAI and Google models; the run waits for the batch.",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 1440,
      },
      {
        key: "browser_fallback",
        label: "Retry refused pages in a browser",
        kind: "bool",
        hint: "For a 403/429 or a JavaScript-only page. Obeys robots.txt, never logs in or passes a challenge.",
      },
    ],
  },
  news: {
    title: "News pipeline",
    description: "Classification and corroboration after each news check.",
    fields: [
      {
        key: "classification_model",
        label: "Classification model",
        kind: "model",
      },
      {
        key: "classification_effort",
        label: "Classification effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "classification_batch_size",
        label: "Items per classification call",
        kind: "int",
        min: 5,
        max: 100,
      },
      {
        key: "rules_first_classification",
        label: "Classify routine items by rule first",
        kind: "bool",
        hint: "Price-data notices, repeat headlines and known filing forms are labelled in code; only the rest goes to the model. Off: the model reads everything.",
      },
      {
        key: "corroboration_model",
        label: "Corroboration model",
        kind: "model",
      },
      {
        key: "corroboration_effort",
        label: "Corroboration effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "corroboration_max_searches",
        label: "Searches per corroboration call",
        kind: "int",
        min: 0,
        max: 50,
      },
      {
        key: "corroboration_rounds",
        label: "Corroboration rounds per story",
        kind: "int",
        min: 0,
        max: 20,
      },
      {
        key: "recheck_hours",
        label: "Re-check unconfirmed stories for (hours)",
        kind: "int",
        min: 1,
        max: 168,
      },
      {
        key: "max_items_per_run",
        label: "Classification cap per run",
        kind: "int",
        min: 10,
        max: 100_000,
        hint: "Cost guard.",
      },
      {
        key: "edgar_fts_max_hits",
        label: "EDGAR full-text hits per query",
        kind: "int",
        min: 10,
        max: 10_000,
        hint: "Read 100 per request; EDGAR serves at most 10,000. A query with more is noted in the collect step.",
      },
      { key: "translation_model", label: "Translation model", kind: "model" },
      {
        key: "translation_effort",
        label: "Translation effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "translation_per_run",
        label: "Items translated per run",
        kind: "int",
        min: 0,
        max: 5000,
        hint: "0 turns translation off.",
      },
      {
        key: "embedding_model",
        label: "Embedding model",
        kind: "model",
        hint: "Groups the same event across languages.",
      },
      {
        key: "embedding_dimensions",
        label: "Embedding dimensions",
        kind: "int",
        min: 128,
        max: 3072,
      },
      {
        key: "embedding_per_run",
        label: "Items embedded per run",
        kind: "int",
        min: 0,
        max: 5000,
        hint: "0 turns embedding off.",
      },
      {
        key: "embedding_duplicate_threshold",
        label: "Duplicate similarity threshold",
        kind: "number",
        hint: "Cosine similarity at or above which two reports are one event. Set from the cross-language evaluation.",
      },
      {
        key: "duplicate_window_hours",
        label: "Duplicate window (hours)",
        kind: "int",
        min: 1,
        max: 168,
      },
      {
        key: "fulltext_per_run",
        label: "Articles read in full per run",
        kind: "int",
        min: 0,
        max: 1000,
      },
      {
        key: "fulltext_browser_fallback",
        label: "Browser fallback for article pages",
        kind: "bool",
        hint: "Needs Playwright. Never logs in or bypasses a paywall.",
      },
      { key: "search_lead_model", label: "AI search model", kind: "model" },
      {
        key: "search_lead_effort",
        label: "AI search effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "ai_search_provider",
        label: "Who finds the leads",
        kind: "choice",
        options: [
          { value: "model", label: "The search model's own web search" },
          { value: "brave", label: "A plain search API (Brave)" },
        ],
        hint: "The plain search API costs a fraction of a model search and needs BRAVE_API_KEY; the model then only drops pages that are not about the company. Switch after comparing the leads it finds.",
      },
      {
        key: "ai_search_max_searches",
        label: "Web searches per holding",
        kind: "int",
        min: 1,
        max: 20,
      },
      {
        key: "ai_search_max_holdings",
        label: "Holdings searched per run",
        kind: "int",
        min: 0,
        max: 100,
      },
      {
        key: "ai_search_thin_below",
        label: "Search holdings with fewer items than",
        kind: "int",
        min: 1,
        max: 100,
      },
      {
        key: "ai_search_days",
        label: "AI search window (days)",
        kind: "int",
        min: 1,
        max: 14,
      },
      {
        key: "indirect_alert_min_severity",
        label: "Lowest severity that alerts for an indirect link",
        kind: "int",
        min: 0,
        max: 3,
      },
      {
        key: "triage_model",
        label: "Triage model",
        kind: "model",
        hint: "Cheap model that scores each wire item.",
      },
      {
        key: "triage_effort",
        label: "Triage effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "triage_per_run",
        label: "Wire items triaged per run",
        kind: "int",
        min: 0,
        max: 2000,
        hint: "0 turns triage off.",
      },
      {
        key: "triage_fulltext_relevance",
        label: "Relevance that earns full text for an unlinked item",
        kind: "int",
        min: 1,
        max: 3,
      },
      {
        key: "batch_extraction",
        label: "Send extraction through the batch endpoint",
        kind: "bool",
        hint: "Half price, but a run waits up to an hour.",
      },
      {
        key: "batch_analysis",
        label: "Send the analysis of minor stories through the batch endpoint",
        kind: "bool",
        hint: "Half price. Stories of severity 3 or more are always analysed at once.",
      },
      {
        key: "reuse_repeat_analysis",
        label: "Reuse the analysis of a repeated story",
        kind: "bool",
        hint: "A minor story the novelty check calls a repeat copies the notes of the story it repeats instead of asking the model again.",
      },
      {
        key: "auto_resolve_days",
        label: "Days of quiet before a confirmed story is resolved",
        kind: "int",
        min: 0,
        max: 180,
        hint: "0: only you resolve stories.",
      },
      {
        key: "sentiment_weight",
        label: "Sentiment weight in brief ranking",
        kind: "number",
        hint: "0 to 1. How much a strong verified sentiment can raise a story's rank.",
      },
      {
        key: "brief_alert",
        label: "Alert the top lines of the morning brief",
        kind: "bool",
      },
      {
        key: "theme_narratives",
        label: "Write a weekly narrative per theme",
        kind: "bool",
      },
      {
        key: "gdelt_themes",
        label: "Fetch GDELT tone and attention for themes",
        kind: "bool",
        hint: "Free, no key. Shown next to theme counts; never used for ranking or alerts.",
      },
      {
        key: "gdelt_max_themes",
        label: "Themes fetched from GDELT per run",
        kind: "int",
        min: 1,
        max: 40,
      },
      {
        key: "analysis_techniques",
        label: "Analysis techniques",
        kind: "words",
        hint: "Comma-separated: novelty, thread, market, themes (free, computed in code) and extraction, impact, framing, claims, thesis, sentiment (call a model; impact, claims and thesis need extraction).",
      },
      {
        key: "extraction_model",
        label: "Event extraction model",
        kind: "model",
      },
      {
        key: "extraction_effort",
        label: "Event extraction effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "analysis_model",
        label: "Analysis model",
        kind: "model",
        hint: "Impact explanations, framing, claim checks and thesis links.",
      },
      {
        key: "analysis_effort",
        label: "Analysis effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      { key: "brief_model", label: "Brief model", kind: "model" },
      {
        key: "brief_effort",
        label: "Brief effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "analysis_per_run",
        label: "Stories analysed by a model per run",
        kind: "int",
        min: 0,
        max: 500,
        hint: "0 turns the model techniques off. The most severe and newest go first.",
      },
      {
        key: "analysis_min_severity",
        label: "Lowest severity analysed by a model",
        kind: "int",
        min: 0,
        max: 3,
      },
      {
        key: "framing_min_outlets",
        label: "Outlets needed for a framing comparison",
        kind: "int",
        min: 2,
        max: 20,
      },
      {
        key: "novelty_window_days",
        label: "Novelty window (days)",
        kind: "int",
        min: 1,
        max: 120,
      },
      {
        key: "novelty_repeat_similarity",
        label: "Repeat similarity",
        kind: "number",
        hint: "Cosine similarity at or above which a story repeats an earlier one.",
      },
      {
        key: "novelty_update_similarity",
        label: "Update similarity",
        kind: "number",
        hint: "Below the repeat similarity.",
      },
      {
        key: "thread_window_days",
        label: "Thread window (days)",
        kind: "int",
        min: 1,
        max: 120,
      },
      {
        key: "brief_max_lines",
        label: "Lines per brief",
        kind: "int",
        min: 3,
        max: 40,
      },
    ],
  },
  digest: {
    title: "Digests",
    description: "How the daily and weekly digests are written.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "use_batch",
        label: "Write through the Batch API (half price)",
        kind: "bool",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 720,
        hint: "Then the summary is written directly.",
      },
      {
        key: "min_severity_summarised",
        label: "Summarise stories from severity",
        kind: "severity",
        hint: "Lower-severity stories are listed by headline.",
      },
      {
        key: "min_severity_listed",
        label: "List stories from severity",
        kind: "severity",
        hint: "Lower-severity stories are left out. At most the summarised severity.",
      },
      {
        key: "daily_window_hours",
        label: "Daily digest covers (hours)",
        kind: "int",
        min: 1,
        max: 168,
      },
      {
        key: "weekly_window_days",
        label: "Weekly digest covers (days)",
        kind: "int",
        min: 1,
        max: 31,
      },
      { key: "monthly_window_days", label: "Monthly digest covers (days)", kind: "int", min: 7, max: 62 },
      { key: "quarterly_window_days", label: "Quarterly digest covers (days)", kind: "int", min: 28, max: 190 },
      {
        key: "long_digest_min_severity_step",
        label: "Monthly and quarterly: raise the summary severity by",
        kind: "int",
        min: 0,
        max: 2,
        hint: "A month holds many more stories; only the more severe ones get a written summary.",
      },
      { key: "monthly_max_material_stories", label: "Monthly digest: stories summarised at most", kind: "int", min: 5, max: 200 },
      { key: "quarterly_max_material_stories", label: "Quarterly digest: stories summarised at most", kind: "int", min: 5, max: 300 },
    ],
  },
  escalation: {
    title: "Event escalation",
    description:
      "An impact note linked to the thesis for each confirmed story at or above the severity below, and for each large price move (threshold in Config: Event rules).",
    fields: [
      {
        key: "enabled",
        label: "Write impact notes",
        kind: "bool",
        hint: "Off: no escalation is queued.",
      },
      { key: "model", label: "Model", kind: "model" },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_steps",
        label: "Step cap per note",
        kind: "int",
        min: 1,
        max: 100,
        hint: "Model calls plus web searches.",
      },
      {
        key: "max_searches",
        label: "Web searches per note",
        kind: "int",
        min: 0,
        max: 50,
        hint: "Restricted to the outlet tiers below.",
      },
      {
        key: "source_tiers",
        label: "Outlet tiers web search may use",
        kind: "tiers",
        hint: "Tiers are set per domain in Config: News sources.",
      },
      {
        key: "min_severity",
        label: "Escalate stories from severity",
        kind: "severity",
      },
      {
        key: "price_move_news_days",
        label: "Price-move note: news from (days before)",
        kind: "int",
        min: 0,
        max: 30,
      },
      {
        key: "price_move_news_items",
        label: "Price-move note: most recent stories read",
        kind: "int",
        min: 0,
        max: 30,
      },
    ],
  },
  macro: {
    title: "Macro",
    description:
      "Macro series collected into the macro store: every macro number in a digest comes from here, with its release date and source.",
    fields: [
      {
        key: "history_years",
        label: "Years of history on a series' first run",
        kind: "int",
        min: 1,
        max: 30,
        hint: "Later runs fetch only the recent observations, and fill any gap since the last stored one.",
      },
      {
        key: "geos",
        label: "Eurostat areas",
        kind: "codes",
        hint: "Countries and the euro area, e.g. CZ, DE, EA21 (the euro area from 2026).",
      },
    ],
  },
  monitoring: {
    title: "Monitoring",
    description:
      "Failed jobs, missed scheduled runs and holdings without news coverage raise a system alert (its severity is set in Config: Notifications).",
    fields: [
      {
        key: "alert_failed_jobs",
        label: "Alert when these jobs fail",
        kind: "words",
        hint: "Job types separated by commas, e.g. portfolio_sync, news_ingest, earnings_check, macro_collect. One alert per failed run.",
      },
      {
        key: "stuck_run_minutes",
        label: "Alert when a run is still going after (minutes)",
        kind: "int",
        min: 0,
        max: 2880,
        hint: "0 turns it off. Keep it above the longest job (13F loads take an hour or more).",
      },
      {
        key: "missed_run_grace_minutes",
        label: "A scheduled run is missed after (minutes)",
        kind: "int",
        min: 5,
        max: 1440,
      },
      {
        key: "news_coverage_hours",
        label: "Coverage alert after no news run for (hours)",
        kind: "int",
        min: 6,
        max: 336,
      },
    ],
  },
  reference: {
    title: "Reference data",
    description:
      "Identity and group structure: OpenFIGI, ESMA FIRDS/FITRS, GLEIF and Wikidata.",
    fields: [
      {
        key: "openfigi_refresh_days",
        label: "Re-map each ISIN through OpenFIGI every (days)",
        kind: "int",
        min: 1,
        max: 365,
      },
      {
        key: "wikidata_enabled",
        label: "Read aliases and suggested relationships from Wikidata",
        kind: "bool",
        hint: "Tier 3: its relationships stay suggestions until you confirm them.",
      },
      {
        key: "wikidata_refresh_days",
        label: "Refresh Wikidata every (days)",
        kind: "int",
        min: 1,
        max: 365,
      },
      {
        key: "max_subsidiary_aliases",
        label: "Most subsidiary names added to news aliases",
        kind: "int",
        min: 0,
        max: 500,
        hint: "From GLEIF Level 2.",
      },
      {
        key: "wikidata_languages",
        label: "Wikidata alias languages",
        kind: "words",
        hint: "Two- or three-letter lower-case codes separated by commas, e.g. en, de, cs.",
      },
    ],
  },
  prices: {
    title: "Prices",
    description:
      "Daily closes from Stooq (Yahoo Finance as the fallback) and the risk figures computed from them; intraday prices from Yahoo Finance (unofficial).",
    fields: [
      {
        key: "benchmark_symbols",
        label: "Benchmark symbols",
        kind: "lines",
        hint: "Stooq symbols, one per line: ^spx S&P 500, ^dax DAX, ^ukx FTSE 100. The first is used for beta and the holding charts.",
      },
      {
        key: "history_years",
        label: "Years of history for holdings",
        kind: "int",
        min: 1,
        max: 10,
        hint: "The risk model needs at least three years of weekly returns.",
      },
      {
        key: "reference_history_years",
        label: "Years of history for benchmarks, proxies and FX",
        kind: "int",
        min: 1,
        max: 30,
        hint: "Longer than the holdings' history so past-episode replays can reach 2008 and 2011 through proxies.",
      },
      {
        key: "plausibility_tolerance_pct",
        label: "Plausibility tolerance (%)",
        kind: "number",
        hint: "Largest gap between Stooq's close and Trading212's price on a sync day before it is flagged. Above 0, up to 20.",
      },
      {
        key: "market_strip",
        label: "Market strip indices",
        kind: "symbol-lines",
        hint: "One per line: the Stooq symbol, a space, the label shown in the header, and optionally \" | \" and the Yahoo symbol of the same index, e.g. ^spx S&P 500 | ^GSPC. Stooq's close first, else Yahoo's (intraday while the market is open); PX (^px) falls back to the Prague Stock Exchange's page, read once a day. An index no source has shows as empty.",
      },
      {
        key: "yahoo_daily_fallback",
        label: "Daily closes from Yahoo Finance when Stooq has none",
        kind: "bool",
        hint: "Unofficial source, used only for a symbol Stooq refuses, cannot be reached for or does not know. Stooq stays the source of record.",
      },
      {
        key: "intraday_enabled",
        label: "Intraday prices (Yahoo Finance, unofficial)",
        kind: "bool",
        hint: "5-minute bars, about 15 minutes delayed, polled only while each listing's market is open and fetched when a holding page opens. Unofficial: it may rate-limit or break without notice; pages then fall back to daily closes with a note.",
      },
      {
        key: "intraday_interval_minutes",
        label: "Intraday poll interval (minutes)",
        kind: "int",
        min: 5,
        max: 60,
        hint: "While a market is open: 5, 10, 15, 20, 30 or 60. Every 15 minutes is about 800 requests a day.",
      },
      {
        key: "intraday_retention_days",
        label: "Intraday bars kept (days)",
        kind: "int",
        min: 1,
        max: 14,
      },
      {
        key: "intraday_cache_seconds",
        label: "Holding page refetch after (seconds)",
        kind: "int",
        min: 30,
        max: 900,
        hint: "During trading hours, a holding page asks Yahoo again once its stored prices are this old.",
      },
    ],
  },
  calendar: {
    title: "Holiday calendar",
    description:
      "Days a source has nothing to publish: public holidays (Nager.Date) and confirmed exchange closures.",
    fields: [
      {
        key: "holiday_countries",
        label: "Public-holiday countries",
        kind: "codes",
        hint: "Two-letter codes, e.g. CZ, US, DE, GB.",
      },
    ],
  },
  chat: {
    title: "Chat",
    description:
      "The chat panel's assistant: reads the app's records, searches the outlet tiers below, and cites every sentence.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      {
        key: "effort",
        label: "Effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_steps",
        label: "Step cap per answer",
        kind: "int",
        min: 1,
        max: 100,
        hint: "Model calls plus tool rounds.",
      },
      {
        key: "escalation_model",
        label: "Stronger model for a failed turn",
        kind: "model-optional",
        hint: "Empty: off. When set, a question the model above cannot answer (nothing parseable, a refusal, the step cap, too little evidence, or no sources) is asked again on this one. The first attempt is still paid for, so turn it on only once the model above holds quality on your questions.",
      },
      {
        key: "escalation_effort",
        label: "Stronger model effort",
        kind: "effort-optional",
        hint: EFFORT_OPTIONAL_HINT,
      },
      {
        key: "max_searches",
        label: "Web searches per answer",
        kind: "int",
        min: 0,
        max: 50,
      },
      {
        key: "search_via_api",
        label: "Search through a plain search API (Brave)",
        kind: "bool",
        hint: "A fraction of the model provider's search fee; the model gets titles and snippets from the allowed outlets only. Needs BRAVE_API_KEY, otherwise the provider's search is used.",
      },
      {
        key: "search_cache_days",
        label: "Days an identical API search is kept",
        kind: "int",
        min: 0,
        max: 14,
      },
      {
        key: "cache_retention",
        label: "Keep the chat prompt cached",
        kind: "choice",
        options: [
          { value: "off", label: "Minutes (the provider's default)" },
          { value: "24h", label: "24 hours (OpenAI)" },
        ],
        hint: "Unverified for each model. A model that rejects the option is asked again without it.",
      },
      {
        key: "history_messages",
        label: "Earlier messages sent along",
        kind: "int",
        min: 2,
        max: 500,
      },
      {
        key: "rules_first",
        label: "Answer portfolio value and cash questions from the database",
        kind: "bool",
        hint: "Short, closed questions only; no model call. Everything else goes to the assistant.",
      },
      {
        key: "source_tiers",
        label: "Outlet tiers web search may use and answers may cite",
        kind: "tiers",
        hint: "Tiers are set per domain in Config: News sources.",
      },
    ],
  },
  review: {
    title: "Monthly review",
    description:
      "Compliance and figures are computed in code; the model only proposes actions, each citing a strategy rule or thesis KPI.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      { key: "effort", label: "Effort", kind: "effort" },
      {
        key: "max_steps",
        label: "Model calls per review",
        kind: "int",
        min: 1,
        max: 50,
      },
      {
        key: "use_batch",
        label: "Propose through the Batch API (half price)",
        kind: "bool",
      },
      {
        key: "batch_wait_minutes",
        label: "Wait for the batch (minutes)",
        kind: "int",
        min: 1,
        max: 720,
        hint: "Then the actions are proposed directly.",
      },
      {
        key: "liquidity_days",
        label: "Flag positions larger than (days of turnover)",
        kind: "number",
        hint: "A position above this many days of its home market's average daily turnover is flagged as hard to exit. Above 0, up to 100.",
      },
      {
        key: "holder_drop_flag_pct",
        label: "Flag a fall in 13F holders of (%)",
        kind: "number",
        hint: "Quarter on quarter. Above 0, up to 100.",
      },
    ],
  },
  ownership: {
    title: "Institutional ownership (13F)",
    description:
      "SEC Form 13F data sets: holders of US-listed holdings (managers above USD 100 M; filed up to 45 days after quarter end).",
    fields: [
      {
        key: "enabled",
        label: "Load 13F data sets",
        kind: "bool",
        hint: "Off: the scheduled run does nothing.",
      },
      {
        key: "index_url",
        label: "Data set index page",
        kind: "text",
        hint: "The SEC page listing the quarterly zip files.",
      },
      {
        key: "datasets_per_run",
        label: "Data sets per run",
        kind: "int",
        min: 1,
        max: 8,
        hint: "About 100 MB each.",
      },
      {
        key: "history_datasets",
        label: "Data sets kept (history)",
        kind: "int",
        min: 1,
        max: 60,
        hint: "The newest this many quarterly sets (8 = two years). An empty database fills over successive runs.",
      },
      {
        key: "top_holders",
        label: "Largest holders shown",
        kind: "int",
        min: 1,
        max: 50,
      },
    ],
  },
  valuation: {
    title: "Valuation",
    description:
      "P/E, P/B and dividend yield: fundamentals from SEC XBRL (with the filing's own instance when companyfacts lags) and ESEF reports on filings.xbrl.org; fund-level figures from the ETF providers (the funds list is kept as saved).",
    fields: [
      {
        key: "enabled",
        label: "Refresh valuation fundamentals",
        kind: "bool",
        hint: "Off: the scheduled run does nothing.",
      },
      {
        key: "full_refresh_days",
        label: "Re-read every holding every (days)",
        kind: "int",
        min: 1,
        max: 90,
        hint: "A holding that filed a new report is re-read on the next run anyway.",
      },
      {
        key: "esef_max_age_months",
        label: "Ignore ESEF reports older than (months)",
        kind: "int",
        min: 3,
        max: 60,
      },
      {
        key: "finnhub_cross_check",
        label: "Finnhub cross-check (tier 2)",
        kind: "bool",
        hint: "Only with FINNHUB_API_KEY; never required.",
      },
      {
        key: "alpha_vantage_fallback",
        label: "Alpha Vantage fallback",
        kind: "bool",
        hint: "Only with ALPHA_VANTAGE_API_KEY; fills missing ratios, up to 25 lookups per UTC day.",
      },
    ],
  },
  look_through: {
    title: "ETF look-through",
    description:
      "Holdings of the funds you hold, from the issuers' own undocumented endpoints (iShares product-data JSON, Vanguard GraphQL), stored weekly as dated snapshots. Funds, benchmark proxies (^spx → CSPX holdings) and issuer groups are kept as saved; edit them through Config export/import.",
    fields: [
      {
        key: "enabled",
        label: "Fetch from the issuers",
        kind: "bool",
        hint: "Off: stored snapshots are still shown.",
      },
      {
        key: "ishares_spreadsheet_fallback",
        label: "iShares: fall back to the full-fund download",
        kind: "bool",
        hint: "When the JSON fails and no recent snapshot is stored. It has no country or ISIN column.",
      },
      {
        key: "cached_fallback_days",
        label: "Keep using a stored snapshot for (days)",
        kind: "int",
        min: 1,
        max: 400,
        hint: "After a failed fetch, before trying the spreadsheet fallback.",
      },
      {
        key: "stale_after_days",
        label: "Label a snapshot stale after (days)",
        kind: "int",
        min: 7,
        max: 400,
        hint: "Vanguard publishes month-end data a few weeks late.",
      },
      {
        key: "keep_snapshots",
        label: "Snapshots kept per fund",
        kind: "int",
        min: 1,
        max: 200,
      },
      {
        key: "vanguard_consumer_id",
        label: "Vanguard X-Consumer-ID",
        kind: "text",
        hint: "ie0 for www.ie.vanguard (uk0 for vanguard.co.uk, de0 for de.vanguard).",
      },
    ],
  },
  xbrl: {
    title: "SEC XBRL fundamentals",
    description:
      "Shares outstanding and revenue by region / segment / product from each holding's latest XBRL filing (SEC EDGAR, official).",
    fields: [
      {
        key: "enabled",
        label: "Read SEC XBRL filings",
        kind: "bool",
        hint: "Off: the scheduled run does nothing.",
      },
      {
        key: "quarterly",
        label: "Also parse the latest 10-Q",
        kind: "bool",
        hint: "US issuers; 20-F filers publish annual XBRL only.",
      },
      {
        key: "read_ads_ratio",
        label: "Read the ADS ratio from 20-F text",
        kind: "bool",
        hint: "Checks the share basis against what the annual report states (one download per new 20-F).",
      },
      {
        key: "max_document_mb",
        label: "Largest document (MB)",
        kind: "int",
        min: 1,
        max: 200,
      },
    ],
  },
  fsds: {
    title: "SEC statement history",
    description:
      "Older statements of US filers from the SEC Financial Statement and Notes data sets (official). The XBRL job reads only the latest annual and quarterly report, about three fiscal years; this fills in the years before.",
    fields: [
      {
        key: "enabled",
        label: "Read the SEC data sets",
        kind: "bool",
        hint: "Off: the scheduled run does nothing.",
      },
      {
        key: "history_months",
        label: "History to read (months)",
        kind: "int",
        min: 12,
        max: 180,
        hint: "Each 10-K carries three years of comparatives: 60 months give about seven fiscal years.",
      },
      {
        key: "datasets_per_run",
        label: "Data sets per run",
        kind: "int",
        min: 1,
        max: 6,
        hint: "A quarter's set is 450-650 MB, a month's about 50 MB.",
      },
    ],
  },
};
export const SETTINGS_KEYS = Object.keys(SETTINGS) as SettingsKey[];

/** The macro series lists (`macro.eurostat`, `macro.fred`, `macro.ecb`, `macro.cnb`), edited as rows. */
export type MacroSource = "eurostat" | "fred" | "ecb" | "cnb";
export const MACRO_SOURCES: readonly {
  value: MacroSource;
  label: string;
  ref: string;
  refHint: string;
}[] = [
  {
    value: "eurostat",
    label: "Eurostat",
    ref: "Dataset",
    refHint: "Dataset code, e.g. prc_hicp_minr.",
  },
  {
    value: "fred",
    label: "FRED (US)",
    ref: "Series id",
    refHint: "FRED series id, e.g. CPIAUCSL.",
  },
  {
    value: "ecb",
    label: "ECB Data Portal",
    ref: "Flow/key",
    refHint: "Flow and key, e.g. FM/D.U2.EUR.4F.KR.DFR.LEV.",
  },
  {
    value: "cnb",
    label: "ČNB (Czech National Bank)",
    ref: "Series",
    refHint:
      "czeonia (CZK overnight rate, daily; key it “czeonia” for the Sharpe ratio) or repo_2w (2-week repo rate changes).",
  },
];
export type FredUnits = "lin" | "pch" | "pc1" | "chg";
export const FRED_UNITS: readonly { value: FredUnits; label: string }[] = [
  { value: "lin", label: "Level" },
  { value: "chg", label: "Change" },
  { value: "pch", label: "% change" },
  { value: "pc1", label: "% change from a year ago" },
];
export type MacroSeriesForm = {
  rowKey: string;
  source: MacroSource;
  /** The series key used in digests and surprise bands (e.g. "hicp"). */
  key: string;
  label: string;
  /** Eurostat dataset, FRED series id, ECB flow/key or ČNB series (czeonia | repo_2w). */
  ref: string;
  /** Eurostat only: "dimension=value" per line. */
  filters: string;
  /** FRED only. */
  units: FredUnits;
  unit: string;
  /** FRED, ECB and ČNB: a policy rate (any change is a surprise). */
  policy_rate: boolean;
  rest: Record<string, unknown>;
};
export const MACRO_REF: Record<MacroSource, string> = {
  eurostat: "dataset",
  fred: "series_id",
  ecb: "flow_key",
  cnb: "series",
};

export type ScheduleForm = {
  name: string;
  enabled: boolean;
  cron: string;
  timezone: string;
  rest: Record<string, unknown>;
};
export type SettingsForm = {
  values: Record<string, string | boolean>;
  rest: Record<string, unknown>;
};
/** One confirmed exchange closure (`calendar.exchange_closures`), edited as a row. */
export type ClosureForm = {
  key: string;
  mic: string;
  date: string;
  name: string;
  rest: Record<string, unknown>;
};
/** One tracked 13F manager (`ownership.tracked_managers`), edited as a row. */
export type ManagerForm = {
  key: string;
  cik: string;
  name: string;
  rest: Record<string, unknown>;
};
export type JobsForm = {
  /** On worker start, run the data jobs that never succeeded or missed their last run. */
  catchUpOnStart: boolean;
  schedules: ScheduleForm[];
  settings: Record<SettingsKey, SettingsForm>;
  closures: ClosureForm[];
  managers: ManagerForm[];
  macroSeries: Record<MacroSource, MacroSeriesForm[]>;
  /** Top-level properties this form does not know (kept as loaded). */
  rest: Record<string, unknown>;
};

let closureCounter = 0;

export function newClosure(): ClosureForm {
  return { key: `c${++closureCounter}`, mic: "", date: "", name: "", rest: {} };
}

export function newManager(): ManagerForm {
  return { key: `t${++closureCounter}`, cik: "", name: "", rest: {} };
}

export function newMacroSeries(source: MacroSource): MacroSeriesForm {
  return {
    rowKey: `m${++closureCounter}`,
    source,
    key: "",
    label: "",
    ref: "",
    filters: "",
    units: "lin",
    unit: "%",
    policy_rate: false,
    rest: {},
  };
}

/** Eurostat dimension filters: "unit=RCH_A" per line <-> {unit: "RCH_A"}. */
export function parseFilters(text: string): Record<string, string> {
  return Object.fromEntries(
    text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const at = line.indexOf("=");
        return at < 0
          ? [line, ""]
          : [line.slice(0, at).trim(), line.slice(at + 1).trim()];
      }),
  );
}

export function formatFilters(filters: Record<string, string>): string {
  return Object.entries(filters)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function macroSeriesFromBody(
  source: MacroSource,
  raw: unknown,
): MacroSeriesForm[] {
  return (Array.isArray(raw) ? raw : []).map((row: Record<string, unknown>) => {
    const { key, label, filters, units, unit, policy_rate, ...extra } = row;
    const refField = MACRO_REF[source];
    const ref = extra[refField];
    delete extra[refField];
    return {
      ...newMacroSeries(source),
      key: String(key ?? ""),
      label: String(label ?? ""),
      ref: String(ref ?? ""),
      filters:
        filters && typeof filters === "object"
          ? formatFilters(filters as Record<string, string>)
          : "",
      units: (units as FredUnits | undefined) ?? "lin",
      unit: unit == null ? "%" : String(unit),
      policy_rate: Boolean(policy_rate),
      rest: extra,
    };
  });
}

function macroSeriesToBody(row: MacroSeriesForm): Record<string, unknown> {
  const out: Record<string, unknown> = {
    ...row.rest,
    key: row.key.trim(),
    label: row.label.trim(),
    [MACRO_REF[row.source]]: row.ref.trim(),
    unit: row.unit.trim(),
  };
  if (row.source === "eurostat") out.filters = parseFilters(row.filters);
  if (row.source === "fred") out.units = row.units;
  if (row.source !== "eurostat") out.policy_rate = row.policy_rate;
  return out;
}

function isSchedule(value: unknown): value is JobSchedule {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { cron?: unknown }).cron === "string"
  );
}

export function scheduleLabel(name: string): string {
  const known = SCHEDULES[name as ScheduleKey];
  if (known) return known.label;
  const words = name.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function numberOrText(text: string): number | string {
  const trimmed = text.trim();
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : trimmed;
}

export function jobsFromBody(body: JobsConfig | null): JobsForm {
  const b = { ...DEFAULT_JOBS, ...(body ?? {}) } as Record<string, unknown>;
  const names = [
    ...SCHEDULE_KEYS,
    ...Object.keys(b).filter((k) => !(k in SCHEDULES) && isSchedule(b[k])),
  ];
  const schedules = names.map((name) => {
    const { enabled, cron, timezone, ...rest } = b[name] as JobSchedule;
    return {
      name,
      enabled: enabled ?? true,
      cron,
      timezone: timezone ?? DEFAULT_TIMEZONE,
      rest,
    };
  });
  const settings = Object.fromEntries(
    SETTINGS_KEYS.map((s) => {
      const loaded = {
        ...(DEFAULT_JOBS[s] as Record<string, unknown>),
        ...((b[s] as Record<string, unknown>) ?? {}),
      };
      const values: Record<string, string | boolean> = {};
      const rest = { ...loaded };
      for (const field of SETTINGS[s].fields as SettingField<SettingsKey>[]) {
        const v = loaded[field.key];
        delete rest[field.key];
        values[field.key] =
          field.kind === "bool"
            ? Boolean(v)
            : field.kind === "symbol-lines"
              ? symbolLinesText(v)
              : Array.isArray(v)
                ? v.join(field.kind === "lines" ? "\n" : ", ")
                : v == null
                  ? ""
                  : String(v);
      }
      return [s, { values, rest }];
    }),
  ) as Record<SettingsKey, SettingsForm>;
  // Closures are edited as rows, not as a settings field.
  const loadedClosures = settings.calendar.rest.exchange_closures;
  delete settings.calendar.rest.exchange_closures;
  const closures = (Array.isArray(loadedClosures) ? loadedClosures : []).map(
    (c: Record<string, unknown>) => {
      const { mic, date, name, ...extra } = c;
      return {
        ...newClosure(),
        mic: String(mic ?? ""),
        date: String(date ?? ""),
        name: String(name ?? ""),
        rest: extra,
      };
    },
  );
  // Tracked 13F managers are edited as rows.
  const loadedManagers = settings.ownership.rest.tracked_managers;
  delete settings.ownership.rest.tracked_managers;
  const managers = (Array.isArray(loadedManagers) ? loadedManagers : []).map(
    (m: Record<string, unknown>) => {
      const { cik, name, ...extra } = m;
      return {
        ...newManager(),
        cik: String(cik ?? ""),
        name: String(name ?? ""),
        rest: extra,
      };
    },
  );
  // Macro series are edited as rows per source.
  const macroSeries = Object.fromEntries(
    MACRO_SOURCES.map(({ value }) => {
      const rows = macroSeriesFromBody(value, settings.macro.rest[value]);
      delete settings.macro.rest[value];
      return [value, rows];
    }),
  ) as Record<MacroSource, MacroSeriesForm[]>;
  const known = new Set<string>([
    "schema_version",
    "catch_up_on_start",
    ...names,
    ...SETTINGS_KEYS,
  ]);
  const rest = Object.fromEntries(
    Object.entries(b).filter(([k]) => !known.has(k)),
  );
  const catchUpOnStart = b.catch_up_on_start !== false;
  return {
    catchUpOnStart,
    schedules,
    settings,
    closures,
    managers,
    macroSeries,
    rest,
  };
}

/** `[{symbol, label, yahoo_symbol}]` as editable lines: "^spx S&P 500 | ^GSPC". */
function symbolLinesText(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .map(
      (item: { symbol?: unknown; label?: unknown; yahoo_symbol?: unknown }) => {
        const line =
          `${String(item.symbol ?? "")} ${String(item.label ?? "")}`.trim();
        return item.yahoo_symbol
          ? `${line} | ${String(item.yahoo_symbol)}`
          : line;
      },
    )
    .join("\n");
}

/**
 * Lines of "symbol label[ | yahoo symbol]" back to `[{symbol, label, yahoo_symbol?}]`; a bare
 * symbol is its own label. Without " | " no Yahoo symbol is sent (the backend then uses the
 * known counterpart of the Stooq symbol, if any).
 */
export function parseSymbolLines(
  text: string,
): { symbol: string; label: string; yahoo_symbol?: string }[] {
  return list(text, /\n/).map((line) => {
    const [main, yahoo] = line.split("|", 2).map((part) => part.trim());
    const [symbol, ...label] = main.split(/\s+/);
    const item: { symbol: string; label: string; yahoo_symbol?: string } = {
      symbol,
      label: label.join(" ") || symbol,
    };
    if (yahoo) item.yahoo_symbol = yahoo.toUpperCase();
    return item;
  });
}

function list(text: string, separator: RegExp): string[] {
  return text
    .split(separator)
    .map((v) => v.trim())
    .filter(Boolean);
}

export function jobsToBody(form: JobsForm): JobsConfig {
  const body: Record<string, unknown> = {
    ...form.rest,
    schema_version: 1,
    catch_up_on_start: form.catchUpOnStart,
  };
  for (const s of form.schedules) {
    body[s.name] = {
      ...s.rest,
      enabled: s.enabled,
      cron: s.cron.trim().replace(/\s+/g, " "),
      timezone: s.timezone.trim(),
    };
  }
  for (const key of SETTINGS_KEYS) {
    const { values, rest } = form.settings[key];
    const out: Record<string, unknown> = { ...rest };
    for (const field of SETTINGS[key].fields as SettingField<SettingsKey>[]) {
      const v = values[field.key];
      if (field.kind === "bool") out[field.key] = Boolean(v);
      else if (field.kind === "int" || field.kind === "severity")
        out[field.key] = numberOrText(String(v));
      else if (field.kind === "number")
        out[field.key] = numberOrText(normaliseSeparators(String(v)));
      else if (field.kind === "lines") out[field.key] = list(String(v), /\n/);
      else if (field.kind === "symbol-lines")
        out[field.key] = parseSymbolLines(String(v));
      else if (field.kind === "codes")
        out[field.key] = list(String(v), /[\s,;]+/).map((c) => c.toUpperCase());
      else if (field.kind === "words") {
        const words = list(String(v), /[\s,;]+/);
        // The backend matches technique names exactly, so "Novelty" must not silently switch one off.
        out[field.key] =
          field.key === "analysis_techniques"
            ? words.map((w) => w.toLowerCase())
            : words;
      } else if (
        field.kind === "effort-optional" ||
        field.kind === "model-optional"
      )
        out[field.key] = String(v).trim() || null;
      else if (field.kind === "tiers") out[field.key] = parseTiers(String(v));
      else if (field.kind === "int-list")
        out[field.key] = String(v)
          .split(/[\s,;]+/)
          .filter(Boolean)
          .map(numberOrText);
      else out[field.key] = String(v).trim();
    }
    if (key === "calendar") {
      out.exchange_closures = form.closures.map((c) => ({
        ...c.rest,
        mic: c.mic.trim().toUpperCase(),
        date: c.date.trim(),
        name: c.name.trim(),
      }));
    }
    if (key === "ownership") {
      // Leading zeros are kept as typed: the CIK is matched as the SEC writes it.
      out.tracked_managers = form.managers.map((m) => ({
        ...m.rest,
        cik: m.cik.trim(),
        name: m.name.trim(),
      }));
    }
    if (key === "macro") {
      for (const { value } of MACRO_SOURCES)
        out[value] = form.macroSeries[value].map(macroSeriesToBody);
    }
    body[key] = out;
  }
  return body as JobsConfig;
}
