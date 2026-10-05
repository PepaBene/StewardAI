import { describe, expect, it } from "vitest";

import { latestChange, parseFinancialHistory } from "./financial-history";
import { formatPeerValue, parsePeerMetrics, peerDelta, trackPositions } from "./peers";

describe("peer comparison", () => {
  const payload = {
    ticker: "AAPL_US_EQ",
    confirmed_peers: [{ peer_ticker: "MSFT" }, { peer_ticker: "GOOG" }],
    covered_peer_metrics: {
      gross_margin: { mean: "0.55", covered_peers: 1, excluded: ["GOOG"], excluded_reasons: { GOOG: "different fiscal year end" }, holding_value: 0.44 },
      roic: { mean: null, covered_peers: 0, excluded: ["MSFT", "GOOG"], excluded_reasons: {}, holding_value: null, holding_reason: "pretax income not positive" },
    },
    excluded_peers: [{ peer: "GOOG", reason: "no comparable period" }],
    holding_period: { period_end: "2025-12-31", basis: "us-gaap" },
    formula_definitions: { gross_margin: "gross profit / revenue" },
  };

  it("keeps missing values null and carries every exclusion reason", () => {
    const view = parsePeerMetrics(payload)!;
    const gm = view.rows.find((r) => r.id === "gross_margin")!;
    expect(gm).toMatchObject({ holding: 0.44, peerMean: 0.55, covered: 1, formula: "gross profit / revenue" });
    expect(gm.excluded).toEqual([{ peer: "GOOG", reason: "different fiscal year end" }]);
    const roic = view.rows.find((r) => r.id === "roic")!;
    expect(roic.holding).toBeNull();
    expect(roic.holdingReason).toBe("pretax income not positive");
    expect(roic.excluded[0].reason).toBe("No value");
    expect(view.rows.find((r) => r.id === "revenue_growth")).toMatchObject({ holding: null, peerMean: null, covered: 0 });
    expect(view.confirmedPeers).toBe(2);
    expect(view.periodEnd).toBe("2025-12-31");
  });

  it("computes the delta and track positions only when values exist", () => {
    const view = parsePeerMetrics(payload)!;
    const gm = view.rows.find((r) => r.id === "gross_margin")!;
    expect(peerDelta(gm)).toBeCloseTo(-0.11);
    expect(trackPositions(gm)!.holding).toBeLessThan(trackPositions(gm)!.mean!);
    expect(peerDelta(view.rows.find((r) => r.id === "roic")!)).toBeNull();
    expect(trackPositions(view.rows.find((r) => r.id === "roic")!)).toBeNull();
    expect(formatPeerValue(null, "percent")).toBe("Not computable");
    expect(formatPeerValue(0.123, "percent")).toBe("12.3%");
    expect(formatPeerValue(1.5, "multiple")).toBe("1.50×");
  });

  it("rejects a non-object payload", () => {
    expect(parsePeerMetrics(null)).toBeNull();
    expect(parsePeerMetrics([])).toBeNull();
  });
});

describe("financial history", () => {
  const fact = (o: Record<string, unknown>) => ({
    statement: "income",
    line_item: "revenue",
    label: "Revenue",
    period_start: "2024-01-01",
    period_end: "2024-12-31",
    basis: "us-gaap",
    currency: "USD",
    value: "100",
    dimensions: {},
    known_at: "2025-02-01T00:00:00Z",
    reconciliation: "reconciled",
    ...o,
  });

  it("keeps annual consolidated facts, the latest revision per period, and flags restatements", () => {
    const series = parseFinancialHistory([
      fact({}),
      fact({ value: "105", known_at: "2025-06-01T00:00:00Z", reconciliation: "unchecked" }),
      fact({ period_start: "2025-01-01", period_end: "2025-12-31", value: "120" }),
      fact({ period_start: "2025-10-01", period_end: "2025-12-31", value: "30" }),
      fact({ dimensions: { segment: "cloud" }, value: "999" }),
      fact({ value: null, period_end: "2023-12-31", period_start: "2023-01-01" }),
    ]);
    expect(series).toHaveLength(1);
    const [rev] = series;
    expect(rev.points.map((p) => p.value)).toEqual([105, 120]);
    expect(rev.restated).toBe(true);
    expect(rev.reconciliation).toBe("unchecked");
    expect(latestChange(rev)).toBeCloseTo(120 / 105 - 1);
  });

  it("treats a missing check as unchecked and ignores unknown statements", () => {
    const [s] = parseFinancialHistory([fact({ reconciliation: undefined }), fact({ statement: "note" })]);
    expect(s.reconciliation).toBe("unchecked");
    expect(parseFinancialHistory({})).toEqual([]);
    expect(latestChange(s)).toBeNull();
  });
});
