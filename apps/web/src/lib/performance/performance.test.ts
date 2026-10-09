import { describe, expect, it } from "vitest";

import {
  barScale,
  holdingProgress,
  ledgerSentence,
  monthsRemainingLabel,
  seriesDomain,
  seriesPath,
  windowName,
  type LedgerHealth,
} from "./performance";

const clean: LedgerHealth = {
  status: "clean",
  entries: 3,
  first: "2026-01-05",
  last: "2026-01-07",
  by_kind: {},
  unsupported: [],
  needs_fx: 0,
  fees_not_captured: 0,
  position_breaks: [],
  cash_breaks: [],
  cash_breaks_total: 0,
  unexplained_cash: 0,
  cash_tolerance: 1,
};

describe("windowName", () => {
  it("spells the windows out and passes unknown labels through", () => {
    expect(windowName("YTD")).toBe("Year to date");
    expect(windowName("5Y")).toBe("5 years");
    expect(windowName("odd")).toBe("odd");
  });
});

describe("monthsRemainingLabel", () => {
  it("reads in years and months", () => {
    expect(monthsRemainingLabel(0, true)).toBe("Passed");
    expect(monthsRemainingLabel(0.3, false)).toBe("Under a month");
    expect(monthsRemainingLabel(5.6, false)).toBe("6 months");
    expect(monthsRemainingLabel(12, false)).toBe("1 year");
    expect(monthsRemainingLabel(28, false)).toBe("2 years 4 months");
  });
});

describe("holdingProgress", () => {
  it("runs from 0 at purchase to 1 at the pass date and clamps outside", () => {
    expect(holdingProgress("2023-01-01", "2026-01-02", "2023-01-01")).toBe(0);
    expect(holdingProgress("2023-01-01", "2026-01-02", "2026-01-02")).toBe(1);
    expect(holdingProgress("2023-01-01", "2026-01-02", "2030-01-01")).toBe(1);
    expect(holdingProgress("2023-01-01", "2025-01-01", "2024-01-01")).toBeCloseTo(0.5, 1);
  });
});

describe("ledgerSentence", () => {
  it("says the ledger is explained when there are no breaks", () => {
    expect(ledgerSentence(clean)).toMatch(/explained by the stored transactions/);
  });
  it("names each kind of gap with its count", () => {
    const text = ledgerSentence({
      ...clean,
      status: "breaks",
      cash_breaks_total: 2,
      position_breaks: [{ ticker: "A", ledger_quantity: 1, broker_quantity: 2, difference: 1, reason: "r" }],
      unsupported: ["cash:9"],
      needs_fx: 1,
    });
    expect(text).toContain("2 days where cash moved");
    expect(text).toContain("1 holding the fills");
    expect(text).toContain("1 transaction type");
    expect(text).toContain("1 in another currency");
  });
  it("handles an empty ledger", () => {
    expect(ledgerSentence({ ...clean, status: "empty" })).toMatch(/No transactions/);
  });
});

describe("barScale", () => {
  it("centres on zero and scales to the largest magnitude", () => {
    const bar = barScale([50, -25, 5]);
    expect(bar(50)).toEqual({ offset: 0.5, width: 0.5 });
    expect(bar(-25)).toEqual({ offset: 0.25, width: 0.25 });
    expect(barScale([0, 0])(0).width).toBe(0);
  });
});

describe("series helpers", () => {
  const pts = [
    { date: "2026-01-01", value: 100 },
    { date: "2026-01-03", value: null },
    { date: "2026-01-05", value: 110 },
  ];
  it("finds a padded domain over the non-null values", () => {
    const d = seriesDomain([pts], [100]);
    expect(d!.lo).toBeLessThan(100);
    expect(d!.hi).toBeGreaterThan(110);
    expect(seriesDomain([[]])).toBeNull();
  });
  it("leaves a gap at a null value", () => {
    const path = seriesPath(pts, { left: 0, right: 100, top: 0, bottom: 100 }, { first: "2026-01-01", last: "2026-01-05" }, { lo: 100, hi: 110 });
    expect(path.match(/M/g)).toHaveLength(2);
    expect(path.startsWith("M0.0 100.0")).toBe(true);
  });
});
