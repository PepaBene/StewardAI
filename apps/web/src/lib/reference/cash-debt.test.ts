import { describe, expect, it } from "vitest";

import { waterfallBars } from "@/lib/charts/waterfall";

import { parseCashConversion, parseDebtMaturities } from "./cash-debt";

const fact = (item: string, value: string | null, o: Record<string, unknown> = {}) => ({
  line_item: item,
  value,
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  basis: "us-gaap",
  currency: "USD",
  dimensions: {},
  known_at: "2026-02-01T00:00:00Z",
  reconciliation: "reconciled",
  ...o,
});

describe("cash conversion", () => {
  const rows = [
    fact("net_income", "100"),
    fact("depreciation_amortization", "30"),
    fact("share_based_compensation", "10"),
    fact("cash_from_operations", "170"),
    fact("capex", "-50"),
  ];

  it("bridges net income to free cash flow with the balancing item marked as derived", () => {
    const c = parseCashConversion(rows)!;
    expect(c.steps.map((s) => s.id)).toEqual(["ni", "da", "sbc", "other", "cfo", "capex", "fcf"]);
    expect(c.steps.find((s) => s.id === "other")).toMatchObject({ value: 30, derived: true });
    expect(c.freeCashFlow).toBe(120);
    expect(c.conversion).toBeCloseTo(1.2);
    const bars = waterfallBars(c.steps);
    expect(bars.find((b) => b.id === "cfo")!.level).toBe(170);
    // The running level agrees with the filed subtotal before it is reset.
    expect(bars.find((b) => b.id === "other")!.level).toBe(170);
  });

  it("leaves a missing line out and says so instead of using zero as a filed value", () => {
    const c = parseCashConversion(rows.filter((r) => r.line_item !== "capex" && r.line_item !== "share_based_compensation"))!;
    expect(c.missing).toEqual(["share-based compensation", "capital expenditure"]);
    expect(c.freeCashFlow).toBeNull();
    expect(c.conversion).toBeNull();
    expect(c.steps.at(-1)!.id).toBe("cfo");
  });

  it("gives no conversion for non-positive net income and needs both ends of the bridge", () => {
    expect(parseCashConversion([fact("net_income", "-5"), fact("cash_from_operations", "10"), fact("capex", "3")])!.conversion).toBeNull();
    expect(parseCashConversion([fact("net_income", "5")])).toBeNull();
    expect(parseCashConversion({})).toBeNull();
  });

  it("flags the worst reconciliation of the facts used and ignores dimensional or quarterly facts", () => {
    const c = parseCashConversion([
      ...rows.slice(0, 3),
      fact("cash_from_operations", "170", { reconciliation: "failed" }),
      fact("net_income", "999", { dimensions: { segment: "x" } }),
      fact("net_income", "55", { period_start: "2025-10-01" }),
    ])!;
    expect(c.netIncome).toBe(100);
    expect(c.reconciliation).toBe("failed");
  });
});

describe("debt maturities", () => {
  it("keeps an untagged bucket as a gap and totals only what is filed", () => {
    const d = parseDebtMaturities([
      fact("debt_maturity_y1", "10", { period_start: null }),
      fact("debt_maturity_y2", "20", { period_start: null }),
      fact("debt_maturity_after_y5", "70", { period_start: null }),
    ])!;
    expect(d.buckets.map((b) => b.value)).toEqual([10, 20, null, null, null, 70]);
    expect(d.total).toBe(100);
    expect(d.missing).toEqual(["Year 3", "Year 4", "Year 5"]);
  });

  it("uses only the latest period and shows a lone remainder under after 5", () => {
    const d = parseDebtMaturities([
      fact("debt_maturity_y1", "99", { period_start: null, period_end: "2024-12-31" }),
      fact("debt_maturity_y1", "10", { period_start: null }),
      fact("debt_maturity_remainder", "40", { period_start: null }),
    ])!;
    expect(d.periodEnd).toBe("2025-12-31");
    expect(d.buckets[0].value).toBe(10);
    expect(d.buckets[5].value).toBe(40);
    expect(parseDebtMaturities([])).toBeNull();
  });
});
