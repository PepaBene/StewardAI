import { describe, expect, it } from "vitest";

import type { JobsConfig } from "@/lib/api/client";

import { describeCron } from "./cron";
import { DEFAULT_JOBS, jobsFromBody, jobsToBody, SCHEDULE_KEYS, scheduleLabel } from "./jobs-form";

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
