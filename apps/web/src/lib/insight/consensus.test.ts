import { describe, expect, it } from "vitest";

import { cone, pointAt, type ConsensusPoint } from "./consensus";

const p = (as_of: string, mean: number): ConsensusPoint =>
  ({ as_of, source: "fmp", mean, median: null, high: mean * 1.2, low: mean * 0.8, count: 5, currency: "USD", price: 100 }) as unknown as ConsensusPoint;

describe("consensus chart", () => {
  it("draws the cone from the last close to the target a year after the snapshot", () => {
    const c = cone([{ date: "2026-01-02", close: 100 }, { date: "2026-03-02", close: 110 }], p("2026-03-01", 130));
    expect(c?.from.v).toBe(110);
    expect(c?.to.mean).toBe(130);
    expect(c?.to.high).toBeCloseTo(156);
    expect(new Date(c!.to.t).toISOString().slice(0, 10)).toBe("2027-03-01");
    expect(cone([], p("2026-03-01", 130))).toBeNull();
  });

  it("finds the snapshot in force", () => {
    const h = [p("2026-01-01", 100), p("2026-02-01", 120)];
    expect(pointAt(h, new Date("2026-01-15").getTime())?.mean).toBe(100);
    expect(pointAt(h, new Date("2025-12-01").getTime())).toBeNull();
  });
});
