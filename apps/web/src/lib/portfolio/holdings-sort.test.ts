import { describe, expect, it } from "vitest";

import type { Holding } from "@/lib/api/client";

import { DEFAULT_HOLDINGS_SORT, nextSort, sortHoldings } from "./holdings-sort";

function holding(ticker: string, value_base: number, extra: Partial<Holding> = {}): Holding {
  return {
    ticker,
    name: ticker,
    isin: null,
    security_type: null,
    quantity: 1,
    avg_price: 1,
    current_price: 1,
    instrument_currency: "USD",
    value: 1,
    account_currency: "CZK",
    value_account: value_base,
    cost_account: value_base,
    ppl: 0,
    ppl_pct: 0,
    fx_rate: 1,
    fx_rate_date: null,
    value_base,
    weight: value_base / 100,
    target_weight: null,
    drift: null,
    drift_status: "no_target",
    ...extra,
  };
}

const HOLDINGS = [
  holding("B_EQ", 20, { ppl: -5, drift: 0.01 }),
  holding("A_EQ", 50, { ppl: 10 }),
  holding("C_EQ", 30, { ppl: 2, drift: -0.02 }),
];

describe("sortHoldings", () => {
  it("defaults to value, largest first", () => {
    expect(sortHoldings(HOLDINGS, DEFAULT_HOLDINGS_SORT).map((h) => h.ticker)).toEqual([
      "A_EQ",
      "C_EQ",
      "B_EQ",
    ]);
  });

  it("keeps every holding exactly once", () => {
    const sorted = sortHoldings(HOLDINGS, { key: "ppl", direction: "asc" });
    expect(new Set(sorted.map((h) => h.ticker)).size).toBe(HOLDINGS.length);
    expect(sorted.map((h) => h.ticker)).toEqual(["B_EQ", "C_EQ", "A_EQ"]);
  });

  it("puts holdings without a value last in both directions", () => {
    for (const direction of ["asc", "desc"] as const) {
      const sorted = sortHoldings(HOLDINGS, { key: "drift", direction });
      expect(sorted.at(-1)?.ticker).toBe("A_EQ");
    }
  });

  it("does not mutate its input", () => {
    const copy = [...HOLDINGS];
    sortHoldings(HOLDINGS, { key: "name", direction: "asc" });
    expect(HOLDINGS).toEqual(copy);
  });
});

describe("nextSort", () => {
  it("flips the direction on the same column", () => {
    expect(nextSort(DEFAULT_HOLDINGS_SORT, "value")).toEqual({ key: "value", direction: "asc" });
  });

  it("starts names ascending and figures descending", () => {
    expect(nextSort(DEFAULT_HOLDINGS_SORT, "name").direction).toBe("asc");
    expect(nextSort(DEFAULT_HOLDINGS_SORT, "ppl").direction).toBe("desc");
  });
});
