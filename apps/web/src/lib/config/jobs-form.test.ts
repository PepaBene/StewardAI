import { describe, expect, it } from "vitest";

import type { JobsConfig } from "@/lib/api/client";

import { describeCron } from "./cron";
import {
  DEFAULT_JOBS,
  jobsFromBody,
  jobsToBody,
  newClosure,
  newMacroSeries,
  newManager,
  parseFilters,
  parseTiers,
  SCHEDULE_KEYS,
  SCHEDULES,
  scheduleLabel,
  SETTINGS,
} from "./jobs-form";

const FULL: JobsConfig = {
  schema_version: 1,
  catch_up_on_start: false,
  portfolio_sync: { enabled: true, cron: "15 23 * * *", timezone: "Europe/Prague" },
  earnings_calendar: { enabled: false, cron: "0 6 * * sun", timezone: "UTC" },
  earnings_calendar_full: { enabled: true, cron: "0 5 1 1,4,7,10 *", timezone: "Europe/Prague" },
  earnings_due: { enabled: true, cron: "0 8 * * mon-fri", timezone: "America/New_York" },
  fact_reconcile: { enabled: false, cron: "30 9 * * *", timezone: "UTC" },
  reference_data: { enabled: true, cron: "30 4 * * mon", timezone: "Europe/Prague" },
  price_history: { enabled: false, cron: "0 23 * * mon-fri", timezone: "Europe/London" },
  holiday_refresh: { enabled: true, cron: "0 4 2 1 *", timezone: "UTC" },
  macro_collect: { enabled: false, cron: "0 12 * * mon-fri", timezone: "Europe/Prague" },
  monitor: { enabled: true, cron: "*/30 * * * *", timezone: "UTC" },
  news_ingest: { enabled: true, cron: "0 6-22/2 * * *", timezone: "Europe/Prague" },
  daily_digest: { enabled: true, cron: "0 7 * * mon-fri", timezone: "Europe/Prague" },
  weekly_digest: { enabled: false, cron: "0 18 * * sun", timezone: "Europe/London" },
  monthly_review: { enabled: true, cron: "0 8 1-3 * *", timezone: "Europe/Prague" },
  ownership_13f: { enabled: false, cron: "0 6 10 * *", timezone: "America/New_York" },
  valuation_refresh: { enabled: false, cron: "30 7 * * mon", timezone: "UTC" },
  etf_look_through: { enabled: true, cron: "0 7 * * 0", timezone: "UTC" },
  sec_xbrl: { enabled: false, cron: "0 6 * * wed", timezone: "UTC" },
  insider_trades: { enabled: true, cron: "0 5 * * mon", timezone: "UTC" },
  public_data_collect: { enabled: false, cron: "7 6 * * mon", timezone: "UTC" },
  source_audit: { enabled: true, cron: "14 7 * * mon", timezone: "UTC" },
  control_stakes: { enabled: false, cron: "21 8 * * mon", timezone: "UTC" },
  nport_holdings: { enabled: true, cron: "28 9 * * mon", timezone: "UTC" },
  fund_probes: { enabled: false, cron: "35 10 * * mon", timezone: "UTC" },
  fund_characteristics_audit: { enabled: true, cron: "42 11 * * mon", timezone: "UTC" },
  free_source_alerts: { enabled: false, cron: "49 12 * * mon", timezone: "UTC" },
  shadow_snapshot: { enabled: true, cron: "56 13 * * mon", timezone: "UTC" },
  shadow_report: { enabled: false, cron: "3 14 * * mon", timezone: "UTC" },
  raw_retention: { enabled: true, cron: "10 5 * * mon", timezone: "UTC" },
  zero_cost_gate: { enabled: false, cron: "17 6 * * mon", timezone: "UTC" },
  returns_refresh: { enabled: true, cron: "24 7 * * mon", timezone: "UTC" },
  exchange_probes: { enabled: false, cron: "31 8 * * mon", timezone: "UTC" },
  ir_releases: { enabled: true, cron: "38 9 * * mon", timezone: "UTC" },
  sec_fsds_backfill: { enabled: false, cron: "45 10 * * mon", timezone: "UTC" },
  pdmr_uk: { enabled: true, cron: "52 11 * * mon", timezone: "UTC" },
  graph_review: { enabled: false, cron: "59 12 * * mon", timezone: "UTC" },
  fmp_company: { enabled: false, cron: "20 9 * * 6", timezone: "UTC" },
  alpha_vantage_statements: { enabled: false, cron: "40 9 * * 6", timezone: "UTC" },
  epo_patents: { enabled: false, cron: "0 10 1 * *", timezone: "UTC" },
  companies_house_filings: { enabled: false, cron: "10 10 * * 6", timezone: "UTC" },
  edinet_filings: { enabled: false, cron: "30 10 * * *", timezone: "UTC" },
  twelve_data_corporate_actions: { enabled: false, cron: "50 10 * * 6", timezone: "UTC" },
  ai_approvals: { enabled: true, cron: "10 7 * * *", timezone: "UTC" },
  ir_discovery: { enabled: true, cron: "30 7 * * 1", timezone: "UTC" },
  gap_research: { enabled: true, cron: "0 8 * * 3", timezone: "UTC" },
  exposure_build: { enabled: true, cron: "50 5 * * 2", timezone: "UTC" },
  disclosure_sources: { enabled: true, cron: "0 6 * * *", timezone: "UTC" },
  disclosure_ingest: { enabled: true, cron: "40 6 * * 1-5", timezone: "UTC" },
  disclosure_identifiers: { enabled: true, cron: "20 6 * * 6", timezone: "UTC" },
  disclosure_deals: { enabled: true, cron: "50 6 * * 1-5", timezone: "UTC" },
  disclosure_esef: { enabled: true, cron: "0 7 * * 1-5", timezone: "UTC" },
  disclosure_estimates: { enabled: true, cron: "30 6 * * 1-5", timezone: "UTC" },
  disclosure_alerts: { enabled: true, cron: "15 13 * * *", timezone: "UTC" },
  earnings_check: {
    extraction_model: "claude-opus-5-5",
    locate_model: "claude-sonnet-5",
    effort: "xhigh",
    locate_effort: "medium",
    max_steps: 12,
    max_searches: 0,
    check_days_after_report: [0, 1, 3, 7],
    max_document_mb: 32,
    xbrl_for_filings: true,
    kpi_history_periods: 12,
  },
  earnings_calendar_settings: { model: "claude-haiku-4-5", effort: null, lookahead_days: 90, refresh_within_days: 21, use_batch: true, batch_wait_minutes: 60 },
  ai_approval: { model: "claude-sonnet-5", effort: null, listings: true, peers: true, refresh_peers: true, peer_refresh_days: 30, peer_refresh_per_run: 8, min_confidence: 0.8, max_confirmed_peers: 8, max_calls_per_run: 40, use_sic: true, rules_first: true, use_batch: true, batch_wait_minutes: 60 },
  exposure_build_settings: { model: "claude-sonnet-5", effort: null, filings_per_run: 6, extract_inputs: true, extract_dependencies: true, map_products: true },
  gap_research_settings: { model: "claude-sonnet-5", effort: null, slots: ["next_report_date"], max_searches: 3, slots_per_run: 8, use_batch: true, batch_wait_minutes: 60, browser_fallback: false, cooldown_days: 30, min_confidence: 0.8, t1_domains: ["sec.gov"], t2_domains: ["reuters.com"], t3_domains: ["wikipedia.org"], denied_domains: ["reddit.com"] },
  ir_discovery_settings: { model: "claude-sonnet-5", effort: null, max_searches: 4, holdings_per_run: 6, use_batch: true, batch_wait_minutes: 60, probe_first: true, browser_fallback: false, cooldown_days: 30, min_confidence: 0.8, denied_domains: ["wikipedia.org"], robots_exempt_domains: [] },
  news: {
    classification_model: "claude-haiku-4-5",
    classification_effort: null,
    classification_batch_size: 25,
    classification_use_batch: true,
    classification_batch_wait_minutes: 30,
    corroboration_model: "claude-sonnet-5",
    corroboration_effort: "low",
    corroboration_max_searches: 5,
    corroboration_rounds: 0,
    recheck_hours: 72,
    max_items_per_run: 1000,
    edgar_fts_max_hits: 500,
  },
  digest: {
    model: "claude-sonnet-5",
    effort: "high",
    use_batch: true,
    batch_wait_minutes: 30,
    min_severity_summarised: 3,
    min_severity_listed: 2,
    daily_window_hours: 36,
    weekly_window_days: 14,
  },
  escalation: {
    enabled: false,
    model: "claude-opus-5-5",
    effort: "max",
    max_steps: 9,
    max_searches: 0,
    min_severity: 3,
    source_tiers: [1],
    price_move_news_days: 5,
    price_move_news_items: 10,
  },
  macro: {
    history_years: 15,
    geos: ["CZ", "EA21"],
    eurostat: [{ key: "hicp", label: "HICP", dataset: "prc_hicp_minr", filters: { unit: "RCH_A", coicop18: "TOTAL" }, unit: "%" }],
    fred: [{ key: "fed_funds", label: "Fed funds", series_id: "DFF", units: "chg", unit: "pp", policy_rate: true }],
    ecb: [{ key: "ecb_deposit_rate", label: "ECB deposit rate", flow_key: "FM/D.U2.EUR.4F.KR.DFR.LEV", unit: "%", policy_rate: false }],
    cnb: [{ key: "czeonia", label: "CZEONIA", series: "czeonia", unit: "%", policy_rate: false }],
  },
  monitoring: { alert_failed_jobs: ["portfolio_sync", "macro_collect"], stuck_run_minutes: 240, missed_run_grace_minutes: 120, news_coverage_hours: 72 },
  reference: {
    openfigi_refresh_days: 14,
    wikidata_enabled: false,
    wikidata_refresh_days: 60,
    max_subsidiary_aliases: 120,
    wikidata_languages: ["en", "fr"],
  },
  prices: {
    benchmark_symbols: ["^spx", "^dax", "^ukx"],
    history_years: 5,
    plausibility_tolerance_pct: 1.5,
    market_strip: [{ symbol: "^spx", label: "S&P 500", yahoo_symbol: "^GSPC" }, { symbol: "^ndx", label: "Nasdaq 100" }],
    yahoo_daily_fallback: false,
    intraday_enabled: false,
    intraday_interval_minutes: 30,
    intraday_retention_days: 3,
    intraday_cache_seconds: 300,
  },
  calendar: {
    holiday_countries: ["CZ", "US", "DE"],
    exchange_closures: [
      { mic: "XNYS", date: "2026-04-03", name: "Good Friday" },
      { mic: "XETR", date: "2026-12-24", name: "Christmas Eve" },
    ],
  },
  chat: { model: "claude-haiku-4-5", effort: null, max_steps: 9, max_searches: 0, history_messages: 20, source_tiers: [1, 2, 3], rules_first: true },
  review: {
    model: "claude-sonnet-5",
    effort: "max",
    max_steps: 5,
    use_batch: true,
    batch_wait_minutes: 90,
    liquidity_days: 2.5,
    holder_drop_flag_pct: 20,
  },
  ownership: {
    enabled: false,
    index_url: "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets?x=1",
    datasets_per_run: 4,
    history_datasets: 12,
    tracked_managers: [
      { cik: "1067983", name: "Berkshire Hathaway Inc" },
      { cik: "0001350694", name: "Bridgewater Associates, LP" },
    ],
    top_holders: 25,
  },
  valuation: {
    enabled: false,
    full_refresh_days: 3,
    esef_max_age_months: 12,
    finnhub_cross_check: false,
    alpha_vantage_fallback: true,
    funds: [
      {
        key: "CSPX",
        label: "iShares Core S&P 500 UCITS ETF (Acc)",
        provider: "ishares",
        url: "https://www.ishares.com/uk/individual/en/products/253743/x",
        port_id: null,
        consumer_id: "uk0",
        tickers: ["CSPXl_EQ"],
        index_label: "S&P 500",
        benchmark_symbols: ["^spx"],
      },
    ],
  },
  look_through: {
    enabled: false,
    funds: [{ isin: "IE00B5BMR087", label: "CSPX", provider: "ishares", portfolio_id: "253743" }],
    benchmark_proxies: [{ symbol: "^spx", fund_isin: "IE00B5BMR087", label: "S&P 500" }],
    issuer_groups: [],
    direct_sectors: { TSM_US_EQ: "Information Technology" },
    direct_countries: {},
    ishares_api_url: "https://www.blackrock.com/varnish-api/uk-retail01-product-data/product-data",
    ishares_site: "ishares-uk",
    ishares_locale: "en_GB",
    ishares_spreadsheet_fallback: false,
    vanguard_graphql_url: "https://www.vanguard.co.uk/gpx/graphql",
    vanguard_consumer_id: "uk0",
    cached_fallback_days: 30,
    stale_after_days: 90,
    keep_snapshots: 4,
  },
  xbrl: { enabled: false, quarterly: false, read_ads_ratio: false, max_document_mb: 20 },
};

describe("jobs form", () => {
  it("round-trips every field of a fully populated body", () => {
    expect(jobsToBody(jobsFromBody(FULL))).toEqual(FULL);
  });

  it("starts from the backend defaults when nothing is saved", () => {
    const body = jobsToBody(jobsFromBody(null));
    expect(body).toEqual(DEFAULT_JOBS);
    expect(body.portfolio_sync).toEqual({ enabled: true, cron: "30 22 * * *", timezone: "Europe/Prague" });
    expect(Object.keys(body)).toEqual(expect.arrayContaining([...SCHEDULE_KEYS]));
  });

  it("lists and keeps a schedule and a setting it does not know yet", () => {
    const future = { ...FULL, quarterly_audit: { enabled: true, cron: "0 9 1 * *", timezone: "UTC" }, digest: { ...FULL.digest!, language: "cs" } };
    const form = jobsFromBody(future as JobsConfig);
    expect(form.schedules.map((s) => s.name)).toContain("quarterly_audit");
    expect(scheduleLabel("quarterly_audit")).toBe("Quarterly audit");
    expect(jobsToBody(form)).toEqual(future);
  });

  it("edits catching up on worker start, on unless saved off", () => {
    expect(DEFAULT_JOBS.catch_up_on_start).toBe(true);
    expect(jobsFromBody(FULL).catchUpOnStart).toBe(false);
    const older: Record<string, unknown> = { ...FULL };
    delete older.catch_up_on_start;
    const form = jobsFromBody(older as JobsConfig);
    expect(form.catchUpOnStart).toBe(true);
    expect(form.rest).not.toHaveProperty("catch_up_on_start");
    form.catchUpOnStart = false;
    expect(jobsToBody(form).catch_up_on_start).toBe(false);
  });

  it("parses edits: list of days, numbers and cron spacing", () => {
    const form = jobsFromBody(FULL);
    form.settings.earnings_check.values.check_days_after_report = "1; 2 ,5";
    form.settings.news.values.recheck_hours = "4x";
    form.schedules[0].cron = "  0   22 * * * ";
    const body = jobsToBody(form);
    expect(body.earnings_check!.check_days_after_report).toEqual([1, 2, 5]);
    expect(body.news!.recheck_hours).toBe("4x");
    expect(body.portfolio_sync!.cron).toBe("0 22 * * *");
  });
});

describe("jobs form: reference, prices and calendar", () => {
  it("edits benchmarks one per line and holiday countries as codes", () => {
    const form = jobsFromBody(FULL);
    expect(form.settings.prices.values.benchmark_symbols).toBe("^spx\n^dax\n^ukx");
    expect(form.settings.calendar.values.holiday_countries).toBe("CZ, US, DE");
    form.settings.prices.values.benchmark_symbols = " ^spx \n\n^ndx\n";
    form.settings.prices.values.plausibility_tolerance_pct = "2,5";
    form.settings.calendar.values.holiday_countries = "cz gb;at";
    const body = jobsToBody(form);
    expect(body.prices!.benchmark_symbols).toEqual(["^spx", "^ndx"]);
    expect(body.prices!.plausibility_tolerance_pct).toBe(2.5);
    expect(body.calendar!.holiday_countries).toEqual(["CZ", "GB", "AT"]);
  });

  it("edits exchange closures as rows", () => {
    const form = jobsFromBody(FULL);
    expect(form.closures.map((c) => [c.mic, c.date, c.name])).toEqual([
      ["XNYS", "2026-04-03", "Good Friday"],
      ["XETR", "2026-12-24", "Christmas Eve"],
    ]);
    form.closures = [
      form.closures[1],
      { ...newClosure(), mic: " xlon ", date: "2026-12-28", name: " Boxing Day (substitute) " },
    ];
    expect(jobsToBody(form).calendar!.exchange_closures).toEqual([
      { mic: "XETR", date: "2026-12-24", name: "Christmas Eve" },
      { mic: "XLON", date: "2026-12-28", name: "Boxing Day (substitute)" },
    ]);
  });

  it("defaults to no closures", () => {
    expect(jobsFromBody(null).closures).toEqual([]);
    expect(jobsToBody(jobsFromBody(null)).calendar).toEqual({ holiday_countries: ["CZ", "US", "DE", "GB"], exchange_closures: [] });
  });
});

describe("jobs form: phase 5 schedules and settings", () => {
  it("lists the monthly review and 13F schedules with the backend defaults", () => {
    expect(SCHEDULES.monthly_review.cron).toBe("0 7 1-3 * *");
    expect(SCHEDULES.monthly_review.description).toMatch(/first weekday/);
    expect(SCHEDULES.ownership_13f.cron).toBe("0 5 5 * *");
    const names = jobsFromBody(null).schedules.map((s) => s.name);
    expect(names).toEqual(expect.arrayContaining(["monthly_review", "ownership_13f"]));
  });

  it("edits the valuation settings and keeps the funds list", () => {
    expect(SCHEDULES.valuation_refresh.cron).toBe("45 7 * * *");
    const form = jobsFromBody(FULL);
    expect(form.settings.valuation.values.full_refresh_days).toBe("3");
    form.settings.valuation.values.full_refresh_days = "14";
    form.settings.valuation.values.finnhub_cross_check = true;
    const body = jobsToBody(form);
    expect(body.valuation!.full_refresh_days).toBe(14);
    expect(body.valuation!.finnhub_cross_check).toBe(true);
    expect(body.valuation!.funds).toEqual(FULL.valuation!.funds);
  });

  it("edits the chat, review and ownership settings", () => {
    const form = jobsFromBody(FULL);
    expect(form.settings.chat.values.history_messages).toBe("20");
    expect(form.settings.review.values.effort).toBe("max");
    expect(form.settings.ownership.values.enabled).toBe(false);
    form.settings.review.values.liquidity_days = "0,5";
    form.settings.chat.values.model = " claude-sonnet-5 ";
    form.settings.ownership.values.index_url = " https://example.test/13f ";
    const body = jobsToBody(form);
    expect(body.review!.liquidity_days).toBe(0.5);
    expect(body.chat!.model).toBe("claude-sonnet-5");
    expect(body.ownership!.index_url).toBe("https://example.test/13f");
  });

  it("edits tracked managers as rows and keeps a CIK's leading zeros", () => {
    const form = jobsFromBody(FULL);
    expect(form.managers.map((m) => [m.cik, m.name])).toEqual([
      ["1067983", "Berkshire Hathaway Inc"],
      ["0001350694", "Bridgewater Associates, LP"],
    ]);
    form.managers = [{ ...newManager(), cik: " 0000102909 ", name: " Vanguard Group Inc " }];
    expect(jobsToBody(form).ownership!.tracked_managers).toEqual([{ cik: "0000102909", name: "Vanguard Group Inc" }]);
    expect(jobsToBody(jobsFromBody(null)).ownership!.tracked_managers).toEqual([]);
  });
});

describe("cron reading", () => {
  it("reads the default schedules", () => {
    expect(describeCron("30 22 * * *").text).toBe("At 22:30 every day.");
    expect(describeCron("0 6-22/2 * * *").text).toBe("Every 2 h from 06:00 to 22:00.");
    expect(describeCron("0 7 * * mon-fri").text).toBe("At 07:00 on weekdays (Mon–Fri).");
    expect(describeCron("0 18 * * sun").text).toBe("At 18:00 on Sundays.");
    expect(describeCron("0 9 1 * *").text).toBe("At 09:00 on day 1 of the month.");
    expect(describeCron("*/15 * * * *").text).toBe("Every 15 minutes.");
    expect(describeCron("0 5 1 1,4,7,10 *").text).toBe("At 05:00 on day 1 of the month in Jan, Apr, Jul, Oct.");
  });

  it("flags numeric weekdays and wrong field counts", () => {
    // Standard crontab numbering (0 = Sunday); the backend translates it for APScheduler.
    expect(describeCron("0 7 * * 1-5")).toMatchObject({ ok: true, text: expect.stringContaining("weekdays") });
    expect(describeCron("0 7 * * mon-fri")).toMatchObject({ ok: true, text: expect.stringContaining("weekdays") });
    expect(describeCron("0 7 * *")).toMatchObject({ ok: false });
    expect(describeCron("0 7 L * *").text).toBe("Custom schedule.");
  });
});

describe("jobs form: escalation, macro and monitoring", () => {
  it("knows the macro and monitoring schedules with the backend's default cron", () => {
    expect(SCHEDULES.macro_collect.cron).toBe("30 11 * * *");
    expect(SCHEDULES.monitor.cron).toBe("*/15 * * * *");
    const names = jobsFromBody(null).schedules.map((s) => s.name);
    expect(names).toEqual(expect.arrayContaining(["macro_collect", "monitor"]));
  });

  it("edits the escalation and monitoring settings", () => {
    const form = jobsFromBody(FULL);
    expect(form.settings.monitoring.values.alert_failed_jobs).toBe("portfolio_sync, macro_collect");
    form.settings.monitoring.values.alert_failed_jobs = "news_ingest;  earnings_check\nmacro_collect";
    form.settings.escalation.values.enabled = true;
    form.settings.escalation.values.max_steps = "4";
    const body = jobsToBody(form);
    expect(body.monitoring!.alert_failed_jobs).toEqual(["news_ingest", "earnings_check", "macro_collect"]);
    expect(body.escalation).toEqual({ ...FULL.escalation, enabled: true, max_steps: 4 });
  });

  it("edits macro series as rows per source", () => {
    const form = jobsFromBody(FULL);
    expect(form.settings.macro.values.geos).toBe("CZ, EA21");
    expect(form.macroSeries.eurostat[0]).toMatchObject({ key: "hicp", ref: "prc_hicp_minr", filters: "unit=RCH_A\ncoicop18=TOTAL" });
    expect(form.macroSeries.fred[0]).toMatchObject({ ref: "DFF", units: "chg", policy_rate: true });
    form.settings.macro.values.geos = "cz de ea21";
    form.macroSeries.fred.push({ ...newMacroSeries("fred"), key: " us_cpi ", label: "US CPI", ref: "CPIAUCSL", units: "pc1" });
    form.macroSeries.ecb = [];
    const body = jobsToBody(form);
    expect(body.macro!.geos).toEqual(["CZ", "DE", "EA21"]);
    expect(body.macro!.fred![1]).toEqual({ key: "us_cpi", label: "US CPI", series_id: "CPIAUCSL", units: "pc1", unit: "%", policy_rate: false });
    expect(body.macro!.ecb).toEqual([]);
    expect(body.macro!.eurostat).toEqual(FULL.macro!.eurostat);
  });

  it("starts from the backend's macro series", () => {
    const form = jobsFromBody(null);
    expect(form.macroSeries.eurostat.map((r) => r.key)).toEqual(["hicp", "unemployment", "gdp", "industrial_production", "sentiment"]);
    expect(form.macroSeries.fred.map((r) => r.ref)).toEqual(["CPIAUCSL", "UNRATE", "DFF"]);
    expect(form.macroSeries.ecb[0].policy_rate).toBe(true);
  });

  it("edits ČNB rates (CZEONIA for the Sharpe ratio, the repo rate as a policy rate)", () => {
    const form = jobsFromBody(null);
    expect(form.macroSeries.cnb.map((r) => [r.key, r.ref, r.policy_rate])).toEqual([
      ["czeonia", "czeonia", false],
      ["cnb_repo_2w", "repo_2w", true],
    ]);
    form.macroSeries.cnb = form.macroSeries.cnb.slice(0, 1);
    const body = jobsToBody(form);
    expect(body.macro!.cnb).toEqual([{ key: "czeonia", label: "CZEONIA (CZK overnight rate)", series: "czeonia", unit: "%", policy_rate: false }]);
    // A stored config from before the ČNB source keeps the defaults.
    expect(jobsFromBody({ macro: { ecb: [] } } as never).macroSeries.cnb).toHaveLength(2);
  });

  it("parses dimension filters", () => {
    expect(parseFilters(" unit = RCH_A \n\ns_adj=SA\nbroken")).toEqual({ unit: "RCH_A", s_adj: "SA", broken: "" });
  });
});

describe("jobs form: optional effort, tiers and the other preferences", () => {
  it("lists every new settings field in the editor", () => {
    const keys = (s: keyof typeof SETTINGS) => SETTINGS[s].fields.map((f) => f.key as string);
    expect(keys("earnings_check")).toEqual(expect.arrayContaining(["locate_effort", "kpi_history_periods"]));
    expect(keys("earnings_calendar_settings")).toContain("effort");
    expect(keys("news")).toEqual(expect.arrayContaining(["classification_effort", "corroboration_effort"]));
    expect(keys("digest")).toEqual(expect.arrayContaining(["effort", "min_severity_listed", "daily_window_hours", "weekly_window_days"]));
    expect(keys("escalation")).toEqual(expect.arrayContaining(["effort", "source_tiers", "price_move_news_days", "price_move_news_items"]));
    expect(keys("chat")).toEqual(expect.arrayContaining(["effort", "source_tiers"]));
    expect(keys("review")).toEqual(expect.arrayContaining(["use_batch", "batch_wait_minutes", "holder_drop_flag_pct"]));
    expect(keys("reference")).toContain("wikidata_languages");
  });

  it("sends an unset effort as null and a picked one as typed", () => {
    const form = jobsFromBody(FULL);
    expect(form.settings.news.values.classification_effort).toBe("");
    expect(form.settings.news.values.corroboration_effort).toBe("low");
    form.settings.news.values.corroboration_effort = "";
    form.settings.chat.values.effort = "medium";
    const body = jobsToBody(form);
    expect(body.news!.corroboration_effort).toBeNull();
    expect(body.chat!.effort).toBe("medium");
  });

  it("edits outlet tiers as a sorted, unique list", () => {
    expect(parseTiers("3, 1 ,1;x 4")).toEqual([1, 3]);
    const form = jobsFromBody(FULL);
    expect(form.settings.chat.values.source_tiers).toBe("1, 2, 3");
    form.settings.escalation.values.source_tiers = "2, 1";
    expect(jobsToBody(form).escalation!.source_tiers).toEqual([1, 2]);
  });

  it("keeps a saved body without the new fields on today's defaults", () => {
    const old = structuredClone(FULL) as unknown as Record<string, Record<string, unknown>>;
    delete old.review.use_batch;
    delete old.review.batch_wait_minutes;
    delete old.review.holder_drop_flag_pct;
    delete old.chat.effort;
    delete old.chat.source_tiers;
    delete old.digest.min_severity_listed;
    const body = jobsToBody(jobsFromBody(old as unknown as JobsConfig));
    expect(body.review).toMatchObject({ use_batch: true, batch_wait_minutes: 60, holder_drop_flag_pct: 10 });
    expect(body.chat).toMatchObject({ effort: null, source_tiers: [1, 2] });
    expect(body.digest!.min_severity_listed).toBe(1);
  });
});

describe("jobs form: market strip", () => {
  it("round-trips the market strip as symbol + label [| Yahoo symbol] lines", () => {
    const form = jobsFromBody(null);
    expect(form.settings.prices.values.market_strip).toBe(
      "^spx S&P 500 | ^GSPC\n^stoxx STOXX Europe 600 | ^STOXX\n^px PX",
    );
    form.settings.prices.values.market_strip = "^dax DAX 40 | ^gdaxi\n^px";
    const body = jobsToBody(form) as unknown as { prices: { market_strip: unknown } };
    expect(body.prices.market_strip).toEqual([
      { symbol: "^dax", label: "DAX 40", yahoo_symbol: "^GDAXI" },
      { symbol: "^px", label: "^px" },
    ]);
  });
});

describe("jobs form: intraday prices", () => {
  it("edits the intraday flag, interval and the Yahoo fallback, on by default", () => {
    const form = jobsFromBody(null);
    expect(form.settings.prices.values).toMatchObject({
      intraday_enabled: true,
      intraday_interval_minutes: "15",
      yahoo_daily_fallback: true,
    });
    form.settings.prices.values.intraday_enabled = false;
    form.settings.prices.values.intraday_interval_minutes = "30";
    const body = jobsToBody(form);
    expect(body.prices).toMatchObject({ intraday_enabled: false, intraday_interval_minutes: 30, intraday_retention_days: 5 });
  });
});
