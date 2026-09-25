import { describe, expect, it } from "vitest";

import {
  formatCoefficient,
  formatDifferencePct,
  formatIndex,
  formatReturn,
  formatRiskPercent,
  parsePlausibility,
  rangeDays,
  rebaseSeries,
  sortCorrelations,
} from "./prices";

describe("rebasing the chart series", () => {
  it("rebases both series to 100 at the first common date", () => {
    const { points, base, withBenchmark } = rebaseSeries(
      [
        { date: "2026-01-02", close: 50 },
        { date: "2026-01-05", close: 100 },
        { date: "2026-01-06", close: 110 },
        { date: "2026-01-07", close: 90 },
      ],
      [
        { date: "2026-01-05", close: 4000 },
        { date: "2026-01-07", close: 4200 },
      ],
    );
    expect(base).toBe("2026-01-05");
    expect(withBenchmark).toBe(true);
    expect(points.map((p) => p.date)).toEqual(["2026-01-05", "2026-01-06", "2026-01-07"]);
    expect(points.map((p) => Number(p.holding?.toFixed(6)))).toEqual([100, 110, 90]);
    expect(points.map((p) => p.benchmark)).toEqual([100, null, 105]);
    expect(points[2]).toMatchObject({ holdingClose: 90, benchmarkClose: 4200 });
  });

  it("draws the holding alone when the benchmark shares no date", () => {
    const { points, base, withBenchmark } = rebaseSeries(
      [
        { date: "2026-01-06", close: 20 },
        { date: "2026-01-05", close: 10 },
      ],
      [{ date: "2025-12-31", close: 1 }],
    );
    expect(base).toBe("2026-01-05");
    expect(withBenchmark).toBe(false);
    expect(points.map((p) => [p.holding, p.benchmark])).toEqual([
      [100, null],
      [200, null],
    ]);
  });

  it("returns nothing for no bars", () => {
    expect(rebaseSeries([], [{ date: "2026-01-05", close: 1 }])).toEqual({ points: [], base: null, withBenchmark: false });
  });
});

describe("risk card formatting (no recomputation)", () => {
  it("formats the backend's fractions as percentages", () => {
    expect(formatReturn(0.12345, "en-US")).toBe("+12.3%");
    expect(formatReturn(-0.05, "en-US")).toBe("-5.0%");
    expect(formatReturn(null, "en-US")).toBe("—");
    expect(formatRiskPercent(0.2349, "en-US")).toBe("23.5%");
    expect(formatRiskPercent(-0.312, "en-US")).toBe("-31.2%");
  });

  it("formats beta and correlation with two decimals", () => {
    expect(formatCoefficient(0.8712, "en-US")).toBe("0.87");
    expect(formatCoefficient(-0.1, "en-US")).toBe("-0.10");
    expect(formatCoefficient(null, "en-US")).toBe("—");
    expect(formatIndex(112.345, "en-US")).toBe("112.3");
  });

  it("maps ranges to the API's days", () => {
    expect(rangeDays("3m")).toBe(91);
    expect(rangeDays("max")).toBe(3700);
  });

  it("sorts correlations highest first, missing last", () => {
    const rows = sortCorrelations([
      { ticker: "B", correlation: null },
      { ticker: "C", correlation: 0.2 },
      { ticker: "A", correlation: 0.9 },
    ]);
    expect(rows.map((r) => r.ticker)).toEqual(["A", "C", "B"]);
  });
});

describe("plausibility check", () => {
  it("reads the backend's record, keeping the percentage as stored", () => {
    const check = parsePlausibility({
      date: "2026-09-24",
      stooq_close: 101.2,
      trading212_price: 100,
      difference_pct: 1.2,
      flagged: true,
    });
    expect(check).toEqual({ date: "2026-09-24", stooqClose: 101.2, trading212Price: 100, differencePct: 1.2, flagged: true });
    expect(formatDifferencePct(1.2, "en-US")).toBe("+1.20 %");
    expect(formatDifferencePct(-0.437, "en-US")).toBe("-0.437 %");
  });

  it("ignores incomplete records", () => {
    expect(parsePlausibility(null)).toBeNull();
    // A record written before the check stored the Trading212 side.
    expect(parsePlausibility({ date: "2026-09-24", stooq_close: 1 } as never)).toBeNull();
  });
});
