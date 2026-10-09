import { describe, expect, it } from "vitest";

import { axisLabel, bridgeTiles, cashWaterfall, gates, historyAt, maturityCheck, metricsPresent, seriesFor } from "./financials";
import { SAMPLE_FACTS, SAMPLE_MATURITIES, sampleBridge, sampleStatements } from "./fixtures-financials";
import { endOfDayMs } from "./model";

const BEFORE = endOfDayMs("2025-01-01");
const BETWEEN = endOfDayMs("2026-03-15");
const AFTER = endOfDayMs("2026-06-01");

describe("as-filed history (mirrors the backend)", () => {
  it("shows nothing before the first filing was known", () => {
    expect(historyAt(SAMPLE_FACTS, BEFORE)).toEqual([]);
  });

  it("shows the original revenue, with no restatement, until the 10-K/A was known", () => {
    const rev = historyAt(SAMPLE_FACTS, BETWEEN).find((p) => p.metric === "revenue" && p.period_end === "2025-12-31")!;
    expect(rev.value).toBe("1000000000");
    expect(rev.restated).toBe(false);
    expect(rev.original_value).toBeNull();
  });

  it("after the amendment shows the restated value and keeps the first one", () => {
    const rev = historyAt(SAMPLE_FACTS, AFTER).find((p) => p.metric === "revenue" && p.period_end === "2025-12-31")!;
    expect(rev.value).toBe("950000000");
    expect(rev.restated).toBe(true);
    expect(rev.original_value).toBe("1000000000");
    expect(rev.accession).toBe("0000001234-26-000009");
  });

  it("keeps an undisclosed value null, not zero", () => {
    const ni = historyAt(SAMPLE_FACTS, AFTER).find((p) => p.metric === "net_income")!;
    expect(ni.value).toBeNull();
  });

  it("a restated value equal to the original is not called a restatement", () => {
    const facts = [
      { metric: "revenue", period_end: "2025-12-31", value: "10", known_at: "2026-01-01T00:00:00Z", accession: "a" },
      { metric: "revenue", period_end: "2025-12-31", value: "10", known_at: "2026-02-01T00:00:00Z", accession: "b" },
    ];
    const [p] = historyAt(facts, AFTER);
    expect(p.restated).toBe(false);
    expect(p.accession).toBe("b");
  });
});

describe("series, waterfall and gates", () => {
  const after = sampleStatements(AFTER);

  it("marks only the restated period on the small multiple", () => {
    const s = seriesFor(after.history, "revenue");
    expect(s.map((p) => [p.period, p.revised])).toEqual([["FY2024", false], ["FY2025", true]]);
    expect(s[1].first).toBe(1_000_000_000);
    expect(s[1].latest).toBe(950_000_000);
  });

  it("lists only metrics that have data", () => {
    expect(metricsPresent(after.history, ["revenue", "capex", "total_debt"])).toEqual(["revenue", "capex"]);
  });

  it("derives free cash flow with its inputs and formula version", () => {
    const fcf = after.derived.find((d) => d.period_end === "2025-12-31")!;
    expect(fcf.value).toBe("380000000");
    expect(fcf.formula).toBe("operating_cash_flow - capex");
    expect(fcf.inputs).toHaveLength(2);
  });

  it("builds the waterfall only when both pieces exist and capex is not negative", () => {
    const steps = cashWaterfall(after.history, "2025-12-31")!;
    expect(steps.map((s) => s.value)).toEqual([500_000_000, -120_000_000, 380_000_000]);
    expect(steps[2].derived).toBe(true);
    expect(cashWaterfall(after.history, "2023-12-31")).toBeNull();
    const negative = [
      { metric: "operating_cash_flow", period_end: "2025-12-31", value: "5", accession: "a", known_at: "2026-01-01T00:00:00Z", restated: false, original_value: null },
      { metric: "capex", period_end: "2025-12-31", value: "-1", accession: "a", known_at: "2026-01-01T00:00:00Z", restated: false, original_value: null },
    ];
    expect(cashWaterfall(negative, "2025-12-31")).toBeNull();
  });

  it("blocks a period whose arithmetic fails or cannot be checked", () => {
    const g = Object.fromEntries(gates(after).map((x) => [x.end, x]));
    expect(g["2025-12-31"].usable).toBe(true); // 950 - 600 = 350
    const before = Object.fromEntries(gates(sampleStatements(BETWEEN)).map((x) => [x.end, x]));
    expect(before["2025-12-31"].usable).toBe(true); // 1000 - 600 = 400
    const broken = gates({ checks: { "2025": [{ name: "gross_profit", status: "fail", detail: "d", expected: "1", actual: "2", tolerance: "0.005" }] }, usable_for_valuation: { "2025": false } });
    expect(broken[0].usable).toBe(false);
    expect(broken[0].failing).toHaveLength(1);
  });
});

describe("segment bridge and debt maturities", () => {
  it("bridges each axis on its own and shows the gap after the restatement", () => {
    const b = sampleBridge(AFTER);
    const product = b.axes.find((a) => a.axis === "ProductOrServiceAxis")!;
    expect(product.reconciles).toBe(false);
    expect(product.unallocated).toBe("10000000");
    expect(b.axes.find((a) => a.axis === "StatementGeographicalAxis")!.reconciles).toBe(true);
    expect(b.notes.some((n) => n.includes("not added together"))).toBe(true);
    expect(sampleBridge(BETWEEN).axes.every((a) => a.reconciles)).toBe(true);
  });

  it("keeps eliminations out of the tiles and names axes in words", () => {
    const product = sampleBridge(AFTER).axes.find((a) => a.axis === "ProductOrServiceAxis")!;
    expect(bridgeTiles(product).map((t) => t.label)).toEqual(["Widgets", "Services"]);
    expect(axisLabel("StatementGeographicalAxis")).toBe("Geographical");
    expect(axisLabel("ProductOrServiceAxis")).toBe("Product Or Service");
  });

  it("checks buckets against the total and does not guess when a piece is missing", () => {
    for (const m of SAMPLE_MATURITIES) expect(maturityCheck(m.buckets, m.total).status).toBe("pass");
    expect(maturityCheck(SAMPLE_MATURITIES[0].buckets, 900_000_000).status).toBe("fail");
    expect(maturityCheck([], 100).status).toBe("needs_review");
    expect(maturityCheck(SAMPLE_MATURITIES[0].buckets, null).status).toBe("needs_review");
  });
});
