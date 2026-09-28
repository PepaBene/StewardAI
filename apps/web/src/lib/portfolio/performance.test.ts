import { describe, expect, it } from "vitest";

import {
  alignBenchmark,
  anchorIndex,
  annualisedVolatility,
  beta,
  indexedRange,
  lastChange,
  periodReturn,
  rangeStart,
  trailingRisk,
} from "./performance";

function daily(start: string, closes: number[]) {
  const t0 = Date.parse(`${start}T00:00:00Z`);
  return closes.map((close, i) => ({ date: new Date(t0 + i * 86_400_000).toISOString().slice(0, 10), close }));
}

describe("ranges", () => {
  it("anchors each range on the last point on or before its start", () => {
    expect(rangeStart("ytd", "2026-09-27")).toBe("2025-12-31");
    expect(rangeStart("1m", "2026-03-31")).toBe("2026-02-28");
    expect(rangeStart("1y", "2026-09-27")).toBe("2025-09-27");
    expect(rangeStart("max", "2026-09-27")).toBeNull();
    expect(anchorIndex(["2025-12-30", "2025-12-31", "2026-01-02"], "2025-12-31")).toBe(1);
    expect(anchorIndex(["2026-01-02"], "2025-12-31")).toBe(0);
  });

  it("measures a period return from the anchor", () => {
    const pts = [
      { date: "2025-12-31", close: 100 },
      { date: "2026-01-02", close: 104 },
      { date: "2026-06-30", close: 110 },
    ];
    expect(periodReturn(pts, "2025-12-31")).toBeCloseTo(0.1);
    expect(periodReturn(pts.slice(0, 1), null)).toBeNull();
  });
});

describe("benchmark alignment", () => {
  it("carries a close over a weekend but not over a long gap", () => {
    const dates = ["2026-09-25", "2026-09-26", "2026-09-27", "2026-10-05"];
    const bench = [{ date: "2026-09-25", close: 10 }];
    expect(alignBenchmark(dates, bench)).toEqual([10, 10, 10, null]);
    expect(alignBenchmark(["2026-09-24"], bench)).toEqual([null]);
  });

  it("rebases both series to 100 on the anchor day", () => {
    const pts = [
      { date: "2026-01-01", close: 50 },
      { date: "2026-01-02", close: 60 },
    ];
    const bench = [
      { date: "2026-01-01", close: 200 },
      { date: "2026-01-02", close: 190 },
    ];
    const r = indexedRange(pts, bench, null);
    expect(r.portfolio.map((p) => p.value)).toEqual([100, 120]);
    expect(r.benchmark?.map((p) => p.value)).toEqual([100, 95]);
    expect(r.portfolioReturn).toBeCloseTo(0.2);
    expect(r.benchmarkReturn).toBeCloseTo(-0.05);
    expect(indexedRange(pts, [], null).benchmark).toBeNull();
  });
});

describe("last change", () => {
  it("prices the flow-adjusted return on the earlier total", () => {
    const pts = [
      { date: "2026-09-26", close: 100 },
      { date: "2026-09-27", close: 101 },
    ];
    // The total jumped by a deposit; only the 1% return counts.
    const values = [
      { date: "2026-09-26", total: 1000 },
      { date: "2026-09-27", total: 2010 },
    ];
    const c = lastChange(pts, values)!;
    expect(c.pct).toBeCloseTo(0.01);
    expect(c.amount).toBeCloseTo(10);
    expect(c.since).toBe("2026-09-26");
    expect(lastChange(pts.slice(1), values)).toBeNull();
  });
});

describe("risk", () => {
  it("annualises volatility by the observed frequency", () => {
    // Alternating ±1% daily moves: stdev ≈ 0.01 per day.
    const closes = [100];
    for (let i = 0; i < 60; i++) closes.push(closes[i] * (i % 2 ? 0.99 : 1.01));
    const vol = annualisedVolatility(daily("2026-01-01", closes))!;
    expect(vol).toBeGreaterThan(0.01 * Math.sqrt(365) * 0.95);
    expect(vol).toBeLessThan(0.01 * Math.sqrt(365) * 1.05);
    expect(annualisedVolatility(daily("2026-01-01", [1, 2, 3]))).toBeNull();
  });

  it("measures beta on matching dates", () => {
    const bench = [100];
    const port = [100];
    for (let i = 0; i < 40; i++) {
      const r = (i % 3) - 1 === 0 ? 0.004 : ((i % 3) - 1) * 0.01;
      bench.push(bench[i] * (1 + r));
      port.push(port[i] * (1 + 2 * r));
    }
    expect(beta(daily("2026-01-01", port), daily("2026-01-01", bench))).toBeCloseTo(2, 5);
    expect(beta(daily("2026-01-01", port), [])).toBeNull();
  });

  it("covers the trailing year and flags a shorter history", () => {
    const pts = daily("2026-01-01", [100, 90, 95, 120]);
    const r = trailingRisk(pts, [])!;
    expect(r.fullYear).toBe(false);
    expect(r.maxDrawdown).toBeCloseTo(-0.1);
    expect(r.return).toBeCloseTo(0.2);
    expect(r.volatility).toBeNull();
    expect(r.benchmarkReturn).toBeNull();
  });
});
