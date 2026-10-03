import { describe, expect, it } from "vitest";

import type { ActivityCoverage, JobListItem } from "@/lib/api/client";

import {
  earningsCoverage,
  firstTimeRate,
  gridDays,
  gridStart,
  identityCoverage,
  jobGrid,
  lastRunNote,
  priceCoverage,
  sourceCoverage,
  spend,
} from "./overview";

function job(p: Partial<JobListItem> & Pick<JobListItem, "id" | "job_type" | "created_at">): JobListItem {
  return {
    trigger: "schedule",
    status: "succeeded",
    error: null,
    started_at: p.created_at,
    finished_at: null,
    params: {},
    attempts: 1,
    cost_usd: null,
    failed_step: null,
    ...p,
  };
}

describe("gridDays", () => {
  it("lists the last n days ending today, oldest first", () => {
    const days = gridDays(new Date("2026-09-27T21:00:00Z"), 3, "UTC");
    expect(days).toEqual(["2026-09-25", "2026-09-26", "2026-09-27"]);
    expect(gridStart(days)).toBe("2026-09-25T00:00:00Z");
    expect(gridDays(new Date("2026-09-27T23:30:00Z"), 1, "Europe/Prague")).toEqual(["2026-09-28"]);
  });
});

describe("jobGrid", () => {
  const days = ["2026-09-25", "2026-09-26", "2026-09-27"];
  it("marks each day failed > retried > ok > none and keeps the last run", () => {
    const rows = jobGrid(
      [
        job({ id: 1, job_type: "news_ingest", created_at: "2026-09-25T08:00:00Z" }),
        job({ id: 2, job_type: "news_ingest", created_at: "2026-09-25T10:00:00Z", status: "failed" }),
        job({ id: 3, job_type: "news_ingest", created_at: "2026-09-26T08:00:00Z", attempts: 2 }),
        job({ id: 4, job_type: "portfolio_sync", created_at: "2026-09-27T08:00:00Z" }),
        job({ id: 5, job_type: "portfolio_sync", created_at: "2026-09-26T08:00:00Z", status: "running" }),
        job({ id: 6, job_type: "zzz_custom", created_at: "2026-09-27T08:00:00Z" }),
        job({ id: 7, job_type: "news_ingest", created_at: "2026-09-01T08:00:00Z", status: "failed" }),
      ],
      days,
      "UTC",
    );
    expect(rows.map((r) => r.jobType)).toEqual(["portfolio_sync", "news_ingest", "zzz_custom"]);
    expect(rows[0].cells).toEqual(["none", "none", "ok"]);
    expect(rows[1].cells).toEqual(["failed", "retried", "none"]);
    expect(rows[1].last?.id).toBe(3);
    expect(rows[1].runs).toBe(3);
  });
});

describe("firstTimeRate", () => {
  it("counts finished runs that succeeded on the first attempt", () => {
    expect(
      firstTimeRate([
        { status: "succeeded", attempts: 1 },
        { status: "succeeded", attempts: 0 },
        { status: "succeeded", attempts: 3 },
        { status: "failed", attempts: 1 },
        { status: "running", attempts: 1 },
      ]),
    ).toEqual({ rate: 0.5, finished: 4 });
    expect(firstTimeRate([{ status: "queued", attempts: 0 }])).toBeNull();
  });
});

describe("lastRunNote", () => {
  it("shows the time today and the day before, with the duration", () => {
    const now = new Date("2026-09-27T20:00:00Z");
    expect(lastRunNote({ created_at: "2026-09-27T18:02:00Z", started_at: "2026-09-27T18:02:00Z", finished_at: "2026-09-27T18:02:41Z" }, now, "UTC")).toBe(
      "18:02 · 41 s",
    );
    expect(lastRunNote({ created_at: "2026-09-26T08:00:00Z", started_at: null, finished_at: null }, now, "UTC")).toBe("26 Sep");
  });
});

describe("spend", () => {
  const summary = { total_usd: 10, cap_usd: 40, period_start: "2026-09-01T00:00:00Z", period_end: "2026-10-01T00:00:00Z" };
  it("projects the month linearly once a tenth has passed", () => {
    const s = spend(summary, new Date("2026-09-16T00:00:00Z"));
    expect(s.elapsed).toBeCloseTo(0.5, 6);
    expect(s.projected).toBeCloseTo(20, 6);
    expect(s.projectedOver).toBe(false);
    expect(spend(summary, new Date("2026-09-02T00:00:00Z")).projected).toBeNull();
    expect(spend({ ...summary, total_usd: 30 }, new Date("2026-09-16T00:00:00Z")).projectedOver).toBe(true);
  });
});

describe("coverage ratios", () => {
  const h = (ticker: string, p: Partial<ActivityCoverage["holdings"][number]>) => ({
    ticker,
    name: null,
    held_since: "2025-01-01T00:00:00Z",
    news_last_covered: null,
    news_gap: false,
    earnings_last_check: "2026-09-01T00:00:00Z",
    earnings_last_status: "succeeded",
    price_symbol: `${ticker}.us`,
    prices_last_date: "2026-09-25",
    prices_gap: false,
    ...p,
  });
  const cov = {
    holdings: [h("A", {}), h("B", { prices_gap: true, earnings_last_status: "failed" }), h("C", { price_symbol: null, earnings_last_check: null, earnings_last_status: null })],
  };
  it("counts holdings with successful earnings checks", () => {
    expect(earningsCoverage(cov)).toEqual({ value: 1, total: 3, note: "1 failed, 1 never checked" });
  });
  it("price ring is the backend's value-weighted share, not a holding count (regression)", () => {
    const pc = { usable_share: 0.9, holdings_usable: 1, holdings_total: 3, max_stale_sessions: 3, holdings: [{ ticker: "A", state: "usable" }, { ticker: "B", state: "stale" }, { ticker: "C", state: "no_price" }] } as never;
    const r = priceCoverage(pc);
    // 1 of 3 holdings is usable, but they hold 90% of the value: the ring says 90%, not 33%
    expect(r).toMatchObject({ share: 0.9, value: 1, total: 3, figure: "90.0% of held value" });
    expect(r?.note).toBe("1 of 3 holdings usable; gap: B, C");
    expect(priceCoverage({ ...(pc as object), usable_share: null } as never)).toBeNull();
    expect(priceCoverage({ ...(pc as object), holdings_total: 0 } as never)).toBeNull();
  });
  it("identity ring is the reviewed-path count from the identity review; no positions is unknown", () => {
    expect(identityCoverage({ held_positions: 3, reviewed: 2, unresolved: [{ ticker: "B" }] } as never)).toEqual({ value: 2, total: 3, note: "unresolved: B" });
    expect(identityCoverage({ held_positions: 0, reviewed: 0, unresolved: [] })).toBeNull();
  });
  it("counts healthy enabled feeds", () => {
    const src = (enabled: boolean, healthy: boolean) => ({ enabled, healthy });
    expect(sourceCoverage({ window_hours: 48, sources: [src(true, true), src(true, false), src(false, false)] as never })).toMatchObject({
      value: 1,
      total: 2,
    });
  });
});
