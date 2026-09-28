import { describe, expect, it } from "vitest";

import type { FeeTotals, TotalByCurrency } from "@/lib/api/client";

import { cashFlowGraph, feeFlow, feeNodes, feeStatusNote } from "./cash-flow";
import { feeName, rowFees } from "./fees";

const total = (type: "order" | "dividend" | "cash", subtype: string | null, amount: number, currency = "CZK"): TotalByCurrency => ({
  type,
  subtype,
  currency,
  count: 1,
  amount,
});

const feeTotals = (over: Partial<FeeTotals> = {}): FeeTotals => ({
  currency: "CZK",
  fx_source: "cnb",
  fx_conversion: 12,
  taxes_levies: 3,
  account_fees: 0,
  total: 15,
  included_in_buys: 0,
  included_in_sales: 0,
  by_name: [],
  unconverted: [],
  fills: 4,
  fills_captured: 4,
  fills_with_fees: 3,
  status: "captured",
  ...over,
});

describe("fee node in the cash-flow graph", () => {
  const totals = [total("cash", "DEPOSIT", 1000), total("order", "BUY", -600), total("order", "SELL", 100)];

  it("splits FX conversion and taxes & levies, and takes them out of cash kept", () => {
    const g = cashFlowGraph(totals, "CZK", null, feeFlow(feeTotals(), "CZK"));
    const labels = g.nodes.map((n) => n.label);
    expect(labels).toContain("FX conversion");
    expect(labels).toContain("Taxes & levies");
    expect(g.fees).toBe(15);
    expect(g.bought).toBe(600);
    expect(g.links.find((l) => l.target === "out:kept")?.value).toBe(1100 - 600 - 15);
  });

  it("is a single FX & fees node when only one kind is present", () => {
    const g = cashFlowGraph(totals, "CZK", null, feeFlow(feeTotals({ taxes_levies: 0, total: 12 }), "CZK"));
    expect(g.nodes.filter((n) => n.id.startsWith("out:fees")).map((n) => n.label)).toEqual(["FX & fees"]);
    expect(feeNodes({ fx: 0, taxes: 0, account: 4, includedInBuys: 0, includedInSales: 0 })).toEqual([
      { id: "fees", label: "FX & fees", value: 4 },
    ]);
  });

  it("does not count account FEE rows twice", () => {
    const withFee = [...totals, total("cash", "FEE", -5)];
    const g = cashFlowGraph(withFee, "CZK", null, feeFlow(feeTotals({ account_fees: 5, total: 20 }), "CZK"));
    expect(g.nodes.map((n) => n.id)).not.toContain("out:cash:FEE");
    expect(g.nodes.find((n) => n.id === "out:fees:account")?.label).toBe("Account fees");
    expect(g.fees).toBe(20);
    // Without fee data the FEE row stays a plain use.
    expect(cashFlowGraph(withFee, "CZK").nodes.map((n) => n.id)).toContain("out:cash:FEE");
  });

  it("moves fees already inside order amounts out of buys and back into sales", () => {
    const fees = feeFlow(feeTotals({ included_in_buys: 10, included_in_sales: 2 }), "CZK");
    const g = cashFlowGraph(totals, "CZK", { stocks: 400, etfs: 200, other: 0 }, fees);
    expect(g.bought).toBe(590);
    const stock = g.links.find((l) => l.target === "out:buy:stock")!.value;
    const etf = g.links.find((l) => l.target === "out:buy:etf")!.value;
    expect(stock + etf).toBeCloseTo(590);
    expect(g.links.find((l) => l.source === "in:order:SELL")?.value).toBe(102);
    // Sources and uses still balance.
    const into = g.links.filter((l) => l.target === "available").reduce((s, l) => s + l.value, 0);
    const out = g.links.filter((l) => l.source === "available").reduce((s, l) => s + l.value, 0);
    expect(into).toBeCloseTo(out);
  });

  it("uses fees only in the graph's currency", () => {
    expect(feeFlow(feeTotals({ currency: "EUR" }), "CZK")).toBeNull();
    expect(feeFlow(null, "CZK")).toBeNull();
  });

  it("words the capture status honestly", () => {
    expect(feeStatusNote(feeTotals({ status: "not_captured", fills_captured: 0 }))).toMatch(/No fee data yet/);
    expect(feeStatusNote(feeTotals({ status: "partial", fills_captured: 1 }))).toBe(
      "Fees read for 1 of 4 orders; the rest are backfilled on the next sync.",
    );
    expect(feeStatusNote(feeTotals())).toBeNull();
  });
});

describe("row fees", () => {
  const order = {
    type: "order" as const,
    currency: "CZK",
    fees_captured: true,
    fee_basis: "excluded" as const,
    fees: [
      { name: "CURRENCY_CONVERSION_FEE", category: "fx" as const, amount: 71.13, currency: "CZK" },
      { name: "FRENCH_TRANSACTION_TAX", category: "tax" as const, amount: 3.5, currency: "CZK" },
    ],
  };

  it("sums the charges with FX / Tax badges", () => {
    const f = rowFees(order);
    expect(f.kind).toBe("fees");
    if (f.kind !== "fees") return;
    expect(f.total).toBeCloseTo(74.63);
    expect(f.badges).toEqual(["FX", "Tax"]);
    expect(f.lines[0]).toMatch(/^FX conversion fee /);
    expect(f.inAmount).toBe(false);
  });

  it("says when fees were not read yet, and shows nothing for other rows", () => {
    expect(rowFees({ ...order, fees_captured: false, fees: [] }).kind).toBe("not_read");
    expect(rowFees({ ...order, fees: [] }).kind).toBe("none");
    expect(rowFees({ ...order, type: "cash", fees_captured: null }).kind).toBe("none");
  });

  it("names fees", () => {
    expect(feeName("STAMP_DUTY_RESERVE_TAX")).toBe("Stamp duty reserve tax");
    expect(feeName("NEW_LEVY_X")).toBe("New levy x");
  });
});
