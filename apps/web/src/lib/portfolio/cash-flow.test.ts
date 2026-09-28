import { describe, expect, it } from "vitest";

import type { TransactionOut } from "@/lib/api/client";

import { cashFlowGraph, dividendsByTicker, monthlyOrderFlows, monthsBetween, securityClass, splitBuys } from "./cash-flow";

const total = (type: "order" | "dividend" | "cash", subtype: string | null, amount: number, currency = "CZK") => ({
  type,
  subtype,
  currency,
  count: 1,
  amount,
});

const row = (over: Partial<TransactionOut>): TransactionOut => ({
  id: Math.random().toString(),
  type: "order",
  subtype: "BUY",
  ticker: "NVDA_US_EQ",
  name: null,
  quantity: 1,
  price: 1,
  amount: -100,
  currency: "CZK",
  occurred_at: "2026-02-10T10:00:00Z",
  ...over,
});

describe("cash-flow graph", () => {
  const totals = [
    total("cash", "DEPOSIT", 450),
    total("cash", "INTEREST", 4),
    total("dividend", "ORDINARY", 24),
    total("order", "BUY", -400),
    total("order", "SELL", 60),
    total("cash", "DEPOSIT", 5, "EUR"),
  ];

  it("balances sources against uses with cash kept", () => {
    const g = cashFlowGraph(totals, "CZK");
    expect(g.inflow).toBe(538);
    expect(g.bought).toBe(400);
    expect(g.excludedCurrencies).toEqual(["EUR"]);
    const labels = Object.fromEntries(g.nodes.map((n) => [n.id, n.label]));
    expect(labels["in:cash:DEPOSIT"]).toBe("Deposits");
    expect(labels["out:order:BUY"]).toBe("Buys");
    const kept = g.links.find((l) => l.target === "out:kept");
    expect(kept?.value).toBe(138);
  });

  it("draws on earlier cash when more was used than came in, and splits buys", () => {
    const g = cashFlowGraph([total("cash", "DEPOSIT", 100), total("order", "BUY", -300)], "CZK", { stocks: 200, etfs: 100, other: 0 });
    expect(g.nodes.map((n) => n.label)).toEqual(["Deposits", "Earlier cash", "Available", "Stocks", "ETFs"]);
    expect(g.links.find((l) => l.source === "in:earlier")?.value).toBe(200);
  });

  it("ignores a split that does not add up to the buys", () => {
    const g = cashFlowGraph([total("cash", "DEPOSIT", 100), total("order", "BUY", -80)], "CZK", { stocks: 10, etfs: 10, other: 0 });
    expect(g.nodes.map((n) => n.label)).toContain("Buys");
  });
});

describe("row derivations", () => {
  it("classifies security types", () => {
    expect(securityClass("ETP")).toBe("etf");
    expect(securityClass("Common Stock")).toBe("stock");
    expect(securityClass(null)).toBeNull();
  });

  it("splits buys by the held instrument's type", () => {
    const rows = [row({ ticker: "VWCEd_EQ", amount: -300 }), row({ amount: -100 }), row({ ticker: "OLD_EQ", amount: -5 }), row({ subtype: "SELL", amount: 50 })];
    const split = splitBuys(rows, [
      { ticker: "VWCEd_EQ", security_type: "ETP" },
      { ticker: "NVDA_US_EQ", security_type: "Common Stock" },
    ], "CZK");
    expect(split).toEqual({ stocks: 100, etfs: 300, other: 5 });
  });

  it("buckets orders by month, empty months included", () => {
    expect(monthsBetween("2025-11-15", "2026-02-01")).toEqual(["2025-11", "2025-12", "2026-01", "2026-02"]);
    const flows = monthlyOrderFlows(
      [row({ amount: -100 }), row({ subtype: "SELL", amount: 40 }), row({ occurred_at: "2026-03-01T00:00:00Z", amount: -7 })],
      "CZK",
      "2026-01-01",
      "2026-03-31",
    );
    expect(flows).toEqual([
      { month: "2026-01", buys: 0, sells: 0 },
      { month: "2026-02", buys: 100, sells: 40 },
      { month: "2026-03", buys: 7, sells: 0 },
    ]);
  });

  it("ranks dividend payers and folds the tail", () => {
    const rows = ["A", "B", "C"].flatMap((t, i) => [row({ type: "dividend", subtype: null, ticker: t, amount: 10 * (i + 1) })]);
    expect(dividendsByTicker(rows, "CZK", 2).map((p) => [p.label, p.amount])).toEqual([
      ["C", 30],
      ["B", 20],
      ["Other 1", 10],
    ]);
  });
});
