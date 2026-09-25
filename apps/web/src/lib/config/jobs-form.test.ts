import { describe, expect, it } from "vitest";

import type { JobsConfig } from "@/lib/api/client";

import { describeCron } from "./cron";
import {
  DEFAULT_JOBS,
  jobsFromBody,
  jobsToBody,
  newClosure,
  newMacroSeries,
  parseFilters,
  SCHEDULE_KEYS,
  SCHEDULES,
  scheduleLabel,
} from "./jobs-form";

const FULL: JobsConfig = {
  schema_version: 1,
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
  earnings_check: {
    extraction_model: "claude-opus-5-5",
    locate_model: "claude-sonnet-5",
    effort: "xhigh",
    max_steps: 12,
    max_searches: 0,
    check_days_after_report: [0, 1, 3, 7],
    max_document_mb: 32,
  },
  earnings_calendar_settings: { model: "claude-haiku-4-5", lookahead_days: 90, refresh_within_days: 21 },
  news: {
    classification_model: "claude-haiku-4-5",
    classification_batch_size: 25,
    corroboration_model: "claude-sonnet-5",
    corroboration_max_searches: 5,
    corroboration_rounds: 0,
    recheck_hours: 72,
    max_items_per_run: 1000,
  },
  digest: { model: "claude-sonnet-5", use_batch: false, batch_wait_minutes: 30, min_severity_summarised: 3 },
  escalation: { enabled: false, model: "claude-opus-5-5", max_steps: 9, max_searches: 0, min_severity: 3 },
  macro: {
    geos: ["CZ", "EA21"],
    eurostat: [{ key: "hicp", label: "HICP", dataset: "prc_hicp_minr", filters: { unit: "RCH_A", coicop18: "TOTAL" }, unit: "%" }],
    fred: [{ key: "fed_funds", label: "Fed funds", series_id: "DFF", units: "chg", unit: "pp", policy_rate: true }],
    ecb: [{ key: "ecb_deposit_rate", label: "ECB deposit rate", flow_key: "FM/D.U2.EUR.4F.KR.DFR.LEV", unit: "%", policy_rate: false }],
  },
  monitoring: { alert_failed_jobs: ["portfolio_sync", "macro_collect"], missed_run_grace_minutes: 120, news_coverage_hours: 72 },
  reference: { openfigi_refresh_days: 14, wikidata_enabled: false, wikidata_refresh_days: 60, max_subsidiary_aliases: 120 },
  prices: { benchmark_symbols: ["^spx", "^dax", "^ukx"], history_years: 5, plausibility_tolerance_pct: 1.5 },
  calendar: {
    holiday_countries: ["CZ", "US", "DE"],
    exchange_closures: [
      { mic: "XNYS", date: "2026-04-03", name: "Good Friday" },
      { mic: "XETR", date: "2026-12-24", name: "Christmas Eve" },
    ],
  },
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
    const future = { ...FULL, monthly_review: { enabled: true, cron: "0 9 1 * *", timezone: "UTC" }, digest: { ...FULL.digest!, language: "cs" } };
    const form = jobsFromBody(future as JobsConfig);
    expect(form.schedules.map((s) => s.name)).toContain("monthly_review");
    expect(scheduleLabel("monthly_review")).toBe("Monthly review");
    expect(jobsToBody(form)).toEqual(future);
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
    expect(body.escalation).toEqual({ enabled: true, model: "claude-opus-5-5", max_steps: 4, max_searches: 0, min_severity: 3 });
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

  it("parses dimension filters", () => {
    expect(parseFilters(" unit = RCH_A \n\ns_adj=SA\nbroken")).toEqual({ unit: "RCH_A", s_adj: "SA", broken: "" });
  });
});
