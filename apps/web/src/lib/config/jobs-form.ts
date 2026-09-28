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
  [K in keyof JobsConfig]-?: NonNullable<JobsConfig[K]> extends JobSchedule ? K : never;
}[keyof JobsConfig];

export type SettingsKey =
  | "earnings_check"
  | "earnings_calendar_settings"
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
  | "ownership";
type Settings<S extends SettingsKey> = NonNullable<JobsConfig[S]>;

/** Labels and the backend's default cron per known schedule (apps/backend/src/steward/config/jobs.py). */
export const SCHEDULES: Record<ScheduleKey, { label: string; description: string; cron: string }> = {
  portfolio_sync: {
    label: "Portfolio sync",
    description: "Trading212 positions and cash, FX rates and drift.",
    cron: "30 22 * * *",
  },
  earnings_calendar: {
    label: "Earnings calendar (soon)",
    description: "Refresh the report date of holdings reporting soon, and of those with no date.",
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
    description: "Check stored results figures against the issuers' 10-Q/10-K XBRL.",
    cron: "0 9 * * *",
  },
  reference_data: {
    label: "Reference data",
    description: "Identity, listings, legal entities and group structure (OpenFIGI, ESMA, GLEIF, Wikidata).",
    cron: "30 4 * * 1",
  },
  price_history: {
    label: "Price history",
    description: "Daily prices (Stooq, Yahoo as the fallback), the PX close and FX history after the close.",
    cron: "15 23 * * 1-5",
  },
  holiday_refresh: {
    label: "Holiday calendar",
    description: "Public holidays for last, this and next year (Nager.Date).",
    cron: "0 4 2 1,7 *",
  },
  macro_collect: {
    label: "Macro data",
    description: "FRED, ECB and Eurostat macro series; macro surprises on release days.",
    cron: "30 11 * * *",
  },
  monitor: {
    label: "Monitoring",
    description: "Alert on failed jobs, missed runs and holdings with no news coverage.",
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
  monthly_review: {
    label: "Monthly review",
    description:
      "Strategy compliance, thesis KPIs and proposed actions for the month. A scheduled run does nothing unless it is the first weekday of the month, so the default fires on the 1st to 3rd and only one of them writes the review.",
    cron: "0 7 1-3 * *",
  },
  ownership_13f: {
    label: "Institutional ownership (13F)",
    description: "Load new SEC Form 13F data sets (published quarterly, about 100 MB each).",
    cron: "0 5 5 * *",
  },
};
export const SCHEDULE_KEYS = Object.keys(SCHEDULES) as ScheduleKey[];
export const DEFAULT_TIMEZONE = "Europe/Prague";

/** The backend's defaults, for when nothing is saved yet (a 404). */
export const DEFAULT_JOBS: JobsConfig = {
  schema_version: 1,
  catch_up_on_start: true,
  ...(Object.fromEntries(
    SCHEDULE_KEYS.map((k) => [k, { enabled: true, cron: SCHEDULES[k].cron, timezone: DEFAULT_TIMEZONE }]),
  ) as Record<ScheduleKey, JobSchedule>),
  earnings_check: {
    extraction_model: "claude-sonnet-5",
    locate_model: "claude-sonnet-5",
    effort: "high",
    locate_effort: null,
    max_steps: 40,
    max_searches: 20,
    check_days_after_report: [1, 2, 4],
    max_document_mb: 20,
    kpi_history_periods: 8,
  },
  earnings_calendar_settings: { model: "claude-haiku-4-5", effort: null, lookahead_days: 120, refresh_within_days: 14 },
  news: {
    classification_model: "claude-haiku-4-5",
    classification_effort: null,
    classification_batch_size: 50,
    corroboration_model: "claude-haiku-4-5",
    corroboration_effort: null,
    corroboration_max_searches: 10,
    corroboration_rounds: 3,
    recheck_hours: 48,
    max_items_per_run: 10_000,
    edgar_fts_max_hits: 100,
  },
  digest: {
    model: "claude-sonnet-5",
    effort: null,
    use_batch: true,
    batch_wait_minutes: 60,
    min_severity_summarised: 2,
    min_severity_listed: 1,
    daily_window_hours: 24,
    weekly_window_days: 7,
  },
  escalation: {
    enabled: true,
    model: "claude-sonnet-5",
    effort: null,
    max_steps: 30,
    max_searches: 15,
    min_severity: 2,
    source_tiers: [1, 2],
    price_move_news_days: 3,
    price_move_news_items: 6,
  },
  macro: {
    history_years: 10,
    geos: ["CZ", "DE", "EA21"],
    eurostat: [
      { key: "hicp", label: "HICP inflation, annual rate", dataset: "prc_hicp_minr", filters: { unit: "RCH_A", coicop18: "TOTAL" }, unit: "%" },
      { key: "unemployment", label: "Unemployment rate", dataset: "une_rt_m", filters: { s_adj: "SA", age: "TOTAL", sex: "T", unit: "PC_ACT" }, unit: "%" },
      { key: "gdp", label: "GDP, change on previous quarter", dataset: "namq_10_gdp", filters: { unit: "CLV_PCH_PRE", s_adj: "SCA", na_item: "B1GQ" }, unit: "%" },
      {
        key: "industrial_production",
        label: "Industrial production, change on previous month",
        dataset: "sts_inpr_m",
        filters: { indic_bt: "PRD", nace_r2: "B-D", s_adj: "SCA", unit: "PCH_PRE" },
        unit: "%",
      },
      { key: "sentiment", label: "Economic sentiment indicator", dataset: "ei_bssi_m_r2", filters: { indic: "BS-ESI-I", s_adj: "SA" }, unit: "index" },
    ],
    fred: [
      { key: "us_cpi", label: "US CPI inflation, annual rate", series_id: "CPIAUCSL", units: "pc1", unit: "%", policy_rate: false },
      { key: "us_unemployment", label: "US unemployment rate", series_id: "UNRATE", units: "lin", unit: "%", policy_rate: false },
      { key: "fed_funds", label: "Fed funds effective rate", series_id: "DFF", units: "lin", unit: "%", policy_rate: true },
    ],
    ecb: [
      { key: "ecb_deposit_rate", label: "ECB deposit facility rate", flow_key: "FM/D.U2.EUR.4F.KR.DFR.LEV", unit: "%", policy_rate: true },
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
    history_years: 2,
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
  calendar: { holiday_countries: ["CZ", "US", "DE", "GB"], exchange_closures: [] },
  chat: { model: "claude-sonnet-5", effort: null, max_steps: 30, max_searches: 15, history_messages: 100, source_tiers: [1, 2] },
  review: {
    model: "claude-opus-5-5",
    effort: "high",
    max_steps: 10,
    use_batch: true,
    batch_wait_minutes: 60,
    liquidity_days: 1,
    holder_drop_flag_pct: 10,
  },
  ownership: {
    enabled: true,
    index_url: "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
    datasets_per_run: 2,
    history_datasets: 8,
    tracked_managers: [],
    top_holders: 10,
  },
};

/** Model ids offered as suggestions; any `claude-…` id is accepted. */
export const MODEL_SUGGESTIONS = [
  "claude-fable-5-1",
  "claude-opus-5-5",
  "claude-opus-5",
  "claude-opus-4-8",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export const EFFORT_OPTIONAL_HINT =
  "Model default: effort is not sent. On Haiku 4 models the level sets an extended-thinking budget (low 2k to max 32k tokens).";
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
   * model's default). `tiers`: outlet tiers 1-3, edited as checkboxes.
   */
  kind:
    | "model"
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
};

export const SETTINGS: { [S in SettingsKey]: { title: string; description: string; fields: SettingField<S>[] } } = {
  earnings_check: {
    title: "Earnings check",
    description: "Find the release, extract the figures, validate them and judge each KPI.",
    fields: [
      { key: "extraction_model", label: "Extraction model", kind: "model" },
      { key: "locate_model", label: "Locate model", kind: "model", hint: "Searches for the release when EDGAR has none." },
      { key: "effort", label: "Extraction effort", kind: "effort" },
      { key: "locate_effort", label: "Locate effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "max_steps", label: "Step cap per run", kind: "int", min: 2, max: 200, hint: "Claude calls plus document fetches." },
      { key: "max_searches", label: "Web searches per run", kind: "int", min: 0, max: 100 },
      { key: "check_days_after_report", label: "Days after the report date to check", kind: "int-list", hint: "Comma separated, e.g. 1, 2, 4 (retries until the release is found)." },
      { key: "max_document_mb", label: "Largest document (MB)", kind: "int", min: 1, max: 32 },
      { key: "kpi_history_periods", label: "KPI history (periods)", kind: "int", min: 1, max: 40, hint: "Past quarters (or years, for annual KPIs) judged for each KPI." },
    ],
  },
  earnings_calendar_settings: {
    title: "Earnings calendar",
    description: "Next report dates: Finnhub for US issuers, the IR calendar page otherwise.",
    fields: [
      { key: "model", label: "Model", kind: "model", hint: "Reads report dates off IR calendar pages." },
      { key: "effort", label: "Effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "lookahead_days", label: "Look ahead (days)", kind: "int", min: 7, max: 400 },
      { key: "refresh_within_days", label: "Weekly run: holdings reporting within (days)", kind: "int", min: 1, max: 120, hint: "The quarterly run refreshes every holding." },
    ],
  },
  news: {
    title: "News pipeline",
    description: "Classification and corroboration after each news check.",
    fields: [
      { key: "classification_model", label: "Classification model", kind: "model" },
      { key: "classification_effort", label: "Classification effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "classification_batch_size", label: "Items per classification call", kind: "int", min: 5, max: 100 },
      { key: "corroboration_model", label: "Corroboration model", kind: "model" },
      { key: "corroboration_effort", label: "Corroboration effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "corroboration_max_searches", label: "Searches per corroboration call", kind: "int", min: 0, max: 50 },
      { key: "corroboration_rounds", label: "Corroboration rounds per story", kind: "int", min: 0, max: 20 },
      { key: "recheck_hours", label: "Re-check unconfirmed stories for (hours)", kind: "int", min: 1, max: 168 },
      { key: "max_items_per_run", label: "Classification cap per run", kind: "int", min: 10, max: 100_000, hint: "Cost guard." },
      { key: "edgar_fts_max_hits", label: "EDGAR full-text hits per query", kind: "int", min: 10, max: 10_000, hint: "Read 100 per request; EDGAR serves at most 10,000. A query with more is noted in the collect step." },
    ],
  },
  digest: {
    title: "Digests",
    description: "How the daily and weekly digests are written.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      { key: "effort", label: "Effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "use_batch", label: "Write through the Batch API (half price)", kind: "bool" },
      { key: "batch_wait_minutes", label: "Wait for the batch (minutes)", kind: "int", min: 1, max: 720, hint: "Then the summary is written directly." },
      { key: "min_severity_summarised", label: "Summarise stories from severity", kind: "severity", hint: "Lower-severity stories are listed by headline." },
      { key: "min_severity_listed", label: "List stories from severity", kind: "severity", hint: "Lower-severity stories are left out. At most the summarised severity." },
      { key: "daily_window_hours", label: "Daily digest covers (hours)", kind: "int", min: 1, max: 168 },
      { key: "weekly_window_days", label: "Weekly digest covers (days)", kind: "int", min: 1, max: 31 },
    ],
  },
  escalation: {
    title: "Event escalation",
    description: "An impact note linked to the thesis for each confirmed story at or above the severity below, and for each large price move (threshold in Config: Event rules).",
    fields: [
      { key: "enabled", label: "Write impact notes", kind: "bool", hint: "Off: no escalation is queued." },
      { key: "model", label: "Model", kind: "model" },
      { key: "effort", label: "Effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "max_steps", label: "Step cap per note", kind: "int", min: 1, max: 100, hint: "Claude calls plus web searches." },
      { key: "max_searches", label: "Web searches per note", kind: "int", min: 0, max: 50, hint: "Restricted to the outlet tiers below." },
      { key: "source_tiers", label: "Outlet tiers web search may use", kind: "tiers", hint: "Tiers are set per domain in Config: News sources." },
      { key: "min_severity", label: "Escalate stories from severity", kind: "severity" },
      { key: "price_move_news_days", label: "Price-move note: news from (days before)", kind: "int", min: 0, max: 30 },
      { key: "price_move_news_items", label: "Price-move note: most recent stories read", kind: "int", min: 0, max: 30 },
    ],
  },
  macro: {
    title: "Macro",
    description: "Macro series collected into the macro store: every macro number in a digest comes from here, with its release date and source.",
    fields: [
      { key: "history_years", label: "Years of history on a series' first run", kind: "int", min: 1, max: 30, hint: "Later runs fetch only the recent observations, and fill any gap since the last stored one." },
      { key: "geos", label: "Eurostat areas", kind: "codes", hint: "Countries and the euro area, e.g. CZ, DE, EA21 (the euro area from 2026)." },
    ],
  },
  monitoring: {
    title: "Monitoring",
    description: "Failed jobs, missed scheduled runs and holdings without news coverage raise a system alert (its severity is set in Config: Notifications).",
    fields: [
      { key: "alert_failed_jobs", label: "Alert when these jobs fail", kind: "words", hint: "Job types separated by commas, e.g. portfolio_sync, news_ingest, earnings_check, macro_collect. One alert per failed run." },
      { key: "missed_run_grace_minutes", label: "A scheduled run is missed after (minutes)", kind: "int", min: 5, max: 1440 },
      { key: "news_coverage_hours", label: "Coverage alert after no news run for (hours)", kind: "int", min: 6, max: 336 },
    ],
  },
  reference: {
    title: "Reference data",
    description: "Identity and group structure: OpenFIGI, ESMA FIRDS/FITRS, GLEIF and Wikidata.",
    fields: [
      { key: "openfigi_refresh_days", label: "Re-map each ISIN through OpenFIGI every (days)", kind: "int", min: 1, max: 365 },
      { key: "wikidata_enabled", label: "Read aliases and suggested relationships from Wikidata", kind: "bool", hint: "Tier 3: its relationships stay suggestions until you confirm them." },
      { key: "wikidata_refresh_days", label: "Refresh Wikidata every (days)", kind: "int", min: 1, max: 365 },
      { key: "max_subsidiary_aliases", label: "Most subsidiary names added to news aliases", kind: "int", min: 0, max: 500, hint: "From GLEIF Level 2." },
      { key: "wikidata_languages", label: "Wikidata alias languages", kind: "words", hint: "Two- or three-letter lower-case codes separated by commas, e.g. en, de, cs." },
    ],
  },
  prices: {
    title: "Prices",
    description:
      "Daily closes from Stooq (Yahoo Finance as the fallback) and the risk figures computed from them; intraday prices from Yahoo Finance (unofficial).",
    fields: [
      { key: "benchmark_symbols", label: "Benchmark symbols", kind: "lines", hint: "Stooq symbols, one per line: ^spx S&P 500, ^dax DAX, ^ukx FTSE 100. The first is used for beta and the holding charts." },
      { key: "history_years", label: "Years of history", kind: "int", min: 1, max: 10 },
      { key: "plausibility_tolerance_pct", label: "Plausibility tolerance (%)", kind: "number", hint: "Largest gap between Stooq's close and Trading212's price on a sync day before it is flagged. Above 0, up to 20." },
      { key: "market_strip", label: "Market strip indices", kind: "symbol-lines", hint: "One per line: the Stooq symbol, a space, the label shown in the header, and optionally \" | \" and the Yahoo symbol of the same index, e.g. ^spx S&P 500 | ^GSPC. Stooq's close first, else Yahoo's (intraday while the market is open); PX (^px) falls back to the Prague Stock Exchange's page, read once a day. An index no source has shows as empty." },
      { key: "yahoo_daily_fallback", label: "Daily closes from Yahoo Finance when Stooq has none", kind: "bool", hint: "Unofficial source, used only for a symbol Stooq refuses, cannot be reached for or does not know. Stooq stays the source of record." },
      { key: "intraday_enabled", label: "Intraday prices (Yahoo Finance, unofficial)", kind: "bool", hint: "5-minute bars, about 15 minutes delayed, polled only while each listing's market is open and fetched when a holding page opens. Unofficial: it may rate-limit or break without notice; pages then fall back to daily closes with a note." },
      { key: "intraday_interval_minutes", label: "Intraday poll interval (minutes)", kind: "int", min: 5, max: 60, hint: "While a market is open: 5, 10, 15, 20, 30 or 60. Every 15 minutes is about 800 requests a day." },
      { key: "intraday_retention_days", label: "Intraday bars kept (days)", kind: "int", min: 1, max: 14 },
      { key: "intraday_cache_seconds", label: "Holding page refetch after (seconds)", kind: "int", min: 30, max: 900, hint: "During trading hours, a holding page asks Yahoo again once its stored prices are this old." },
    ],
  },
  calendar: {
    title: "Holiday calendar",
    description: "Days a source has nothing to publish: public holidays (Nager.Date) and confirmed exchange closures.",
    fields: [
      { key: "holiday_countries", label: "Public-holiday countries", kind: "codes", hint: "Two-letter codes, e.g. CZ, US, DE, GB." },
    ],
  },
  chat: {
    title: "Chat",
    description: "The chat panel's assistant: reads the app's records, searches the outlet tiers below, and cites every sentence.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      { key: "effort", label: "Effort", kind: "effort-optional", hint: EFFORT_OPTIONAL_HINT },
      { key: "max_steps", label: "Step cap per answer", kind: "int", min: 1, max: 100, hint: "Claude calls plus tool rounds." },
      { key: "max_searches", label: "Web searches per answer", kind: "int", min: 0, max: 50 },
      { key: "history_messages", label: "Earlier messages sent along", kind: "int", min: 2, max: 500 },
      { key: "source_tiers", label: "Outlet tiers web search may use and answers may cite", kind: "tiers", hint: "Tiers are set per domain in Config: News sources." },
    ],
  },
  review: {
    title: "Monthly review",
    description: "Compliance and figures are computed in code; the model only proposes actions, each citing a strategy rule or thesis KPI.",
    fields: [
      { key: "model", label: "Model", kind: "model" },
      { key: "effort", label: "Effort", kind: "effort" },
      { key: "max_steps", label: "Claude calls per review", kind: "int", min: 1, max: 50 },
      { key: "use_batch", label: "Propose through the Batch API (half price)", kind: "bool" },
      { key: "batch_wait_minutes", label: "Wait for the batch (minutes)", kind: "int", min: 1, max: 720, hint: "Then the actions are proposed directly." },
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
    description: "SEC Form 13F data sets: holders of US-listed holdings (managers above USD 100 M; filed up to 45 days after quarter end).",
    fields: [
      { key: "enabled", label: "Load 13F data sets", kind: "bool", hint: "Off: the scheduled run does nothing." },
      { key: "index_url", label: "Data set index page", kind: "text", hint: "The SEC page listing the quarterly zip files." },
      { key: "datasets_per_run", label: "Data sets per run", kind: "int", min: 1, max: 8, hint: "About 100 MB each." },
      { key: "history_datasets", label: "Data sets kept (history)", kind: "int", min: 1, max: 60, hint: "The newest this many quarterly sets (8 = two years). An empty database fills over successive runs." },
      { key: "top_holders", label: "Largest holders shown", kind: "int", min: 1, max: 50 },
    ],
  },
};
export const SETTINGS_KEYS = Object.keys(SETTINGS) as SettingsKey[];

/** The macro series lists (`macro.eurostat`, `macro.fred`, `macro.ecb`), edited as rows. */
export type MacroSource = "eurostat" | "fred" | "ecb";
export const MACRO_SOURCES: readonly { value: MacroSource; label: string; ref: string; refHint: string }[] = [
  { value: "eurostat", label: "Eurostat", ref: "Dataset", refHint: "Dataset code, e.g. prc_hicp_minr." },
  { value: "fred", label: "FRED (US)", ref: "Series id", refHint: "FRED series id, e.g. CPIAUCSL." },
  { value: "ecb", label: "ECB Data Portal", ref: "Flow/key", refHint: "Flow and key, e.g. FM/D.U2.EUR.4F.KR.DFR.LEV." },
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
  /** Eurostat dataset, FRED series id or ECB flow/key. */
  ref: string;
  /** Eurostat only: "dimension=value" per line. */
  filters: string;
  /** FRED only. */
  units: FredUnits;
  unit: string;
  /** FRED and ECB: a policy rate (any change is a surprise). */
  policy_rate: boolean;
  rest: Record<string, unknown>;
};
const MACRO_REF: Record<MacroSource, string> = { eurostat: "dataset", fred: "series_id", ecb: "flow_key" };

export type ScheduleForm = { name: string; enabled: boolean; cron: string; timezone: string; rest: Record<string, unknown> };
export type SettingsForm = { values: Record<string, string | boolean>; rest: Record<string, unknown> };
/** One confirmed exchange closure (`calendar.exchange_closures`), edited as a row. */
export type ClosureForm = { key: string; mic: string; date: string; name: string; rest: Record<string, unknown> };
/** One tracked 13F manager (`ownership.tracked_managers`), edited as a row. */
export type ManagerForm = { key: string; cik: string; name: string; rest: Record<string, unknown> };
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
        return at < 0 ? [line, ""] : [line.slice(0, at).trim(), line.slice(at + 1).trim()];
      }),
  );
}

export function formatFilters(filters: Record<string, string>): string {
  return Object.entries(filters)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function macroSeriesFromBody(source: MacroSource, raw: unknown): MacroSeriesForm[] {
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
      filters: filters && typeof filters === "object" ? formatFilters(filters as Record<string, string>) : "",
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
  return typeof value === "object" && value !== null && typeof (value as { cron?: unknown }).cron === "string";
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
  const names = [...SCHEDULE_KEYS, ...Object.keys(b).filter((k) => !(k in SCHEDULES) && isSchedule(b[k]))];
  const schedules = names.map((name) => {
    const { enabled, cron, timezone, ...rest } = b[name] as JobSchedule;
    return { name, enabled: enabled ?? true, cron, timezone: timezone ?? DEFAULT_TIMEZONE, rest };
  });
  const settings = Object.fromEntries(
    SETTINGS_KEYS.map((s) => {
      const loaded = { ...(DEFAULT_JOBS[s] as Record<string, unknown>), ...((b[s] as Record<string, unknown>) ?? {}) };
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
  const closures = (Array.isArray(loadedClosures) ? loadedClosures : []).map((c: Record<string, unknown>) => {
    const { mic, date, name, ...extra } = c;
    return { ...newClosure(), mic: String(mic ?? ""), date: String(date ?? ""), name: String(name ?? ""), rest: extra };
  });
  // Tracked 13F managers are edited as rows.
  const loadedManagers = settings.ownership.rest.tracked_managers;
  delete settings.ownership.rest.tracked_managers;
  const managers = (Array.isArray(loadedManagers) ? loadedManagers : []).map((m: Record<string, unknown>) => {
    const { cik, name, ...extra } = m;
    return { ...newManager(), cik: String(cik ?? ""), name: String(name ?? ""), rest: extra };
  });
  // Macro series are edited as rows per source.
  const macroSeries = Object.fromEntries(
    MACRO_SOURCES.map(({ value }) => {
      const rows = macroSeriesFromBody(value, settings.macro.rest[value]);
      delete settings.macro.rest[value];
      return [value, rows];
    }),
  ) as Record<MacroSource, MacroSeriesForm[]>;
  const known = new Set<string>(["schema_version", "catch_up_on_start", ...names, ...SETTINGS_KEYS]);
  const rest = Object.fromEntries(Object.entries(b).filter(([k]) => !known.has(k)));
  const catchUpOnStart = b.catch_up_on_start !== false;
  return { catchUpOnStart, schedules, settings, closures, managers, macroSeries, rest };
}

/** `[{symbol, label, yahoo_symbol}]` as editable lines: "^spx S&P 500 | ^GSPC". */
function symbolLinesText(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value
    .map((item: { symbol?: unknown; label?: unknown; yahoo_symbol?: unknown }) => {
      const line = `${String(item.symbol ?? "")} ${String(item.label ?? "")}`.trim();
      return item.yahoo_symbol ? `${line} | ${String(item.yahoo_symbol)}` : line;
    })
    .join("\n");
}

/**
 * Lines of "symbol label[ | yahoo symbol]" back to `[{symbol, label, yahoo_symbol?}]`; a bare
 * symbol is its own label. Without " | " no Yahoo symbol is sent (the backend then uses the
 * known counterpart of the Stooq symbol, if any).
 */
export function parseSymbolLines(text: string): { symbol: string; label: string; yahoo_symbol?: string }[] {
  return list(text, /\n/).map((line) => {
    const [main, yahoo] = line.split("|", 2).map((part) => part.trim());
    const [symbol, ...label] = main.split(/\s+/);
    const item: { symbol: string; label: string; yahoo_symbol?: string } = { symbol, label: label.join(" ") || symbol };
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
  const body: Record<string, unknown> = { ...form.rest, schema_version: 1, catch_up_on_start: form.catchUpOnStart };
  for (const s of form.schedules) {
    body[s.name] = { ...s.rest, enabled: s.enabled, cron: s.cron.trim().replace(/\s+/g, " "), timezone: s.timezone.trim() };
  }
  for (const key of SETTINGS_KEYS) {
    const { values, rest } = form.settings[key];
    const out: Record<string, unknown> = { ...rest };
    for (const field of SETTINGS[key].fields as SettingField<SettingsKey>[]) {
      const v = values[field.key];
      if (field.kind === "bool") out[field.key] = Boolean(v);
      else if (field.kind === "int" || field.kind === "severity") out[field.key] = numberOrText(String(v));
      else if (field.kind === "number") out[field.key] = numberOrText(String(v).replace(",", "."));
      else if (field.kind === "lines") out[field.key] = list(String(v), /\n/);
      else if (field.kind === "symbol-lines") out[field.key] = parseSymbolLines(String(v));
      else if (field.kind === "codes") out[field.key] = list(String(v), /[\s,;]+/).map((c) => c.toUpperCase());
      else if (field.kind === "words") out[field.key] = list(String(v), /[\s,;]+/);
      else if (field.kind === "effort-optional") out[field.key] = String(v).trim() || null;
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
      out.tracked_managers = form.managers.map((m) => ({ ...m.rest, cik: m.cik.trim(), name: m.name.trim() }));
    }
    if (key === "macro") {
      for (const { value } of MACRO_SOURCES) out[value] = form.macroSeries[value].map(macroSeriesToBody);
    }
    body[key] = out;
  }
  return body as JobsConfig;
}
