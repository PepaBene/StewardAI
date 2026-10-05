import { describe, expect, it } from "vitest";

import { bucketSegments, bucketTotal, expenseSeries, overlapMatrix, parseAggregate, parseFactsheets, parseLookThrough, parseOverlap, topPairs } from "./funds";

describe("overlap", () => {
  const o = parseOverlap({
    as_of: "2026-10-01",
    funds: [
      { isin: "IE1", label: "World", unknown_weight: 0.03, stale: false },
      { isin: "IE2", label: "US", unknown_weight: null },
      { isin: "IE3", label: "EM" },
    ],
    pairs: [
      { fund_a: "IE1", fund_b: "IE2", overlap: 0.6, shared_issuers: 400, same_date: true },
      { fund_a: "IE1", fund_b: "IE3", overlap: null, same_date: false },
    ],
  })!;
  it("builds a symmetric matrix with null diagonal and unknown cells", () => {
    const m = overlapMatrix(o);
    expect(m.cells[0][1]).toBe(0.6);
    expect(m.cells[1][0]).toBe(0.6);
    expect(m.cells[0][0]).toBeNull();
    expect(m.cells[1][2]).toBeNull();
    expect(m.cells[0][2]).toBeNull();
    expect(m.mixedDatePairs).toBe(1);
  });
  it("keeps unknown fund weight null and sorts pairs without a figure last", () => {
    expect(o.funds[0].unknownWeight).toBe(0.03);
    expect(o.funds[1].unknownWeight).toBeNull();
    expect(topPairs(o)[0].overlap).toBe(0.6);
    expect(parseOverlap([])).toBeNull();
  });
});

describe("aggregate", () => {
  const a = parseAggregate({
    as_of: "2026-10-01",
    available: true,
    unknown: { unknown_fund_weight: 0.04, not_looked_through_weight: null },
    fees: { annual_drag: 0.001, unpriced_weight: 0.1 },
    exposures: [
      {
        dimension: "sector",
        reconciles: true,
        buckets: [
          { key: "tech", label: "Tech", kind: "classified", weight: 0.3 },
          { key: "unk", label: "Unknown fund weight", kind: "unknown_fund", weight: 0.04 },
          { key: "x", label: "Missing", kind: "classified", weight: null },
          { key: "z", label: "Zero", kind: "cash", weight: 0 },
        ],
      },
    ],
  })!;
  it("leaves unknown weight as its own bucket and never rescales", () => {
    const segs = bucketSegments(a.exposures[0]);
    expect(segs.map((s) => s.label)).toEqual(["Tech", "Unknown fund weight"]);
    expect(segs[0].value).toBe(0.3);
    expect(bucketTotal(a.exposures[0])).toBeCloseTo(0.34);
    expect(a.notLookedThroughWeight).toBeNull();
    expect(a.unknownFundWeight).toBe(0.04);
  });
});

describe("look-through and factsheet", () => {
  it("parses the report and keeps a missing share null", () => {
    const holding = { key: "a", name: "A", isin: null, isins: [], ticker: null, sector: null, country: null, currency: null, weight: 0.05, value_base: 5, via: [{}, {}] as never, benchmark_weight: null };
    const l = parseLookThrough({
      as_of: "2026-09-30",
      snapshot_ids: { IE00B4L5Y983: 1, IE00B3RBWM25: 2 },
      report: {
        snapshot_date: "2026-09-30",
        base_currency: "EUR",
        available: true,
        method: "m",
        coverage: null,
        exposures: [],
        top_holdings: [holding],
        concentration: null,
        benchmark: null,
        benchmarks: [],
        fx: null,
        funds: [],
        notes: ["n"],
      },
    });
    expect(l.snapshotIds).toEqual([1, 2]);
    expect(l.portfolioShareLookedThrough).toBeNull();
    expect(l.effectiveN).toBeNull();
    expect(l.top[0].viaFunds).toBe(2);
    expect(l.top[0].sector).toBeNull();
    expect(l.notes).toEqual(["n"]);
  });
  it("builds an expense series without inventing zeros", () => {
    const base = { id: 1, fund_isin: "X", source: "issuer", holdings_count: 0, reconcile_tolerance: 0.01, lookthrough_basis: "b", missing_fields: [], fetched_at: "t", field_names_verified: true, extraction_warnings: [] };
    const rows = parseFactsheets([
      { ...base, as_of: "2026-06-30", expense_ratio: 0.002 },
      { ...base, as_of: "2026-03-31", expense_ratio: null },
      { ...base, as_of: "2025-12-31", expense_ratio: 0.0022, field_names_verified: false, coverage_note: "c" },
    ]);
    expect(expenseSeries(rows)).toEqual([{ date: "2025-12-31", value: 0.0022 }, { date: "2026-06-30", value: 0.002 }]);
    expect(rows[2].fieldNamesVerified).toBe(false);
    expect(rows[2].note).toBe("c");
    expect(rows[1].expenseRatio).toBeNull();
  });
});
