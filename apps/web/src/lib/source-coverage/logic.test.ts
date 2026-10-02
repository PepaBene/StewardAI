import { describe, expect, it } from "vitest";

import { SOURCE_COVERAGE_FIXTURE as F } from "./fixture";
import {
  budgetTone,
  describeBudget,
  drillThroughHref,
  filterRows,
  groupByProvider,
  hasOverage,
  routineHeadroom,
  stateCounts,
  usedFraction,
} from "./logic";
import { PROVIDERS } from "./types";

describe("fixture contract", () => {
  it("covers all 13 named providers in both the matrix and the budget list", () => {
    const rowProviders = new Set(F.rows.map((r) => r.provider));
    const budgetProviders = new Set(F.budgets.map((b) => b.provider));
    for (const p of PROVIDERS) {
      expect(rowProviders.has(p), `row for ${p}`).toBe(true);
      expect(budgetProviders.has(p), `budget for ${p}`).toBe(true);
    }
  });

  it("has unique row ids and zero spend", () => {
    expect(new Set(F.rows.map((r) => r.id)).size).toBe(F.rows.length);
    expect(F.spendUsd).toBe(0);
    expect(hasOverage(F.budgets, F.spendUsd)).toBe(false);
  });

  it("keeps covered within total", () => {
    for (const r of F.rows) for (const c of [r.held, r.watchlist]) expect(c.covered).toBeLessThanOrEqual(c.total);
  });
});

describe("filtering and grouping", () => {
  it("filters by state within a scope", () => {
    const paid = filterRows(F.rows, { state: "paid_only" });
    expect(paid.length).toBeGreaterThan(0);
    expect(paid.every((r) => r.held.state === "paid_only" || r.watchlist.state === "paid_only")).toBe(true);
    const heldQuota = filterRows(F.rows, { scope: "held", state: "quota_exhausted" });
    expect(heldQuota).toHaveLength(0);
    expect(filterRows(F.rows, { scope: "watchlist", state: "quota_exhausted" }).length).toBe(1);
  });

  it("counts every cell of every state, zeros included", () => {
    const c = stateCounts(F.rows);
    expect(Object.keys(c)).toHaveLength(9);
    expect(Object.values(c).reduce((a, b) => a + b, 0)).toBe(F.rows.length * 2);
  });

  it("groups by provider in canonical order", () => {
    const g = groupByProvider(F.rows);
    expect(g[0].provider).toBe("alpha_vantage");
    expect(g.find((x) => x.provider === "fmp")!.rows).toHaveLength(2);
  });
});

describe("budget", () => {
  const b = (limit: number | null, used: number, reserve = 0) => ({ limit, used, reserve });

  it("classifies the budget tone, never treating a missing cap as unlimited", () => {
    expect(budgetTone(b(25, 10, 3))).toBe("ok");
    expect(budgetTone(b(25, 22, 3))).toBe("reserve");
    expect(budgetTone(b(25, 25, 3))).toBe("exhausted");
    expect(budgetTone(b(null, 5))).toBe("uncapped");
  });

  it("computes headroom without going negative and returns null for no cap", () => {
    expect(routineHeadroom(b(25, 10, 3))).toBe(12);
    expect(routineHeadroom(b(25, 24, 3))).toBe(0);
    expect(routineHeadroom(b(null, 3))).toBeNull();
    expect(usedFraction(b(20, 20))).toBe(1);
    expect(usedFraction(b(null, 20))).toBeNull();
  });

  it("describes a budget with its unit and period", () => {
    const av = F.budgets.find((x) => x.provider === "alpha_vantage")!;
    expect(describeBudget(av)).toBe("22 / 25 calls per day");
    expect(describeBudget(F.budgets.find((x) => x.provider === "finnhub")!)).toContain("no published cap");
  });

  it("flags overage", () => {
    expect(hasOverage([{ ...F.budgets[0], overageEvents: 1 }], 0)).toBe(true);
    expect(hasOverage(F.budgets, 0.01)).toBe(true);
  });
});

describe("drill-through", () => {
  it("prefers the failed run, then the job-log filter, then nothing", () => {
    const epo = F.rows.find((r) => r.provider === "epo_ops")!;
    expect(drillThroughHref(epo)).toBe("/jobs/4182");
    const fh = F.rows.find((r) => r.provider === "finnhub" && r.jobType)!;
    expect(drillThroughHref(fh)).toBe("/activity?type=earnings_check#job-log");
    expect(drillThroughHref(F.rows.find((r) => r.jobType === null)!)).toBeNull();
  });
});
