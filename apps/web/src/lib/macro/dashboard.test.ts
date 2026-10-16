import { describe, expect, it } from "vitest";

import {
  cadenceOf,
  changeIsPoints,
  formatChange,
  formatLevel,
  formatPeriod,
  geographyKey,
  rangePosition,
  standing,
  unitSuffix,
} from "./dashboard";

describe("macro dashboard wording", () => {
  it("moves rates in points and levels in percent", () => {
    expect(changeIsPoints("%")).toBe(true);
    expect(changeIsPoints("% p.a.")).toBe(true);
    expect(changeIsPoints("USD/bbl")).toBe(false);
    expect(formatChange(3.35, 3.72, "%")).toBe("+0.37 pp");
    expect(formatChange(100, 126.6, "USD/bbl")).toBe("+26.6%");
    expect(formatChange(133, 29, "thousands")).toBe("−104k");
    expect(formatChange(null, 1, "%")).toBeNull();
  });

  it("writes levels and units for a reader", () => {
    expect(formatLevel(5.19, "%")).toBe("5.19");
    expect(formatLevel(13540, "USD/t")).toBe("13.5k");
    expect(formatLevel(199000, "claims")).toBe("199.0k");
    expect(unitSuffix("%")).toBe("%");
    expect(unitSuffix("USD/bbl")).toBe(" USD/bbl");
    expect(unitSuffix("thousands")).toBe("k jobs");
  });

  it("reads the cadence from the gaps and names periods by it", () => {
    expect(
      cadenceOf(["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]),
    ).toBe("daily");
    expect(
      cadenceOf(["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]),
    ).toBe("monthly");
    expect(cadenceOf(["2025-10-01", "2026-01-01", "2026-04-01"])).toBe(
      "quarterly",
    );
    expect(formatPeriod("2026-04-01", "quarterly")).toBe("Q2 2026");
    expect(formatPeriod("2026-09-01", "monthly")).toBe("Sep 2026");
    expect(formatPeriod("2026-10-14", "daily")).toBe("14 Oct 2026");
  });

  it("says where the value stands against the longest window", () => {
    const r = (years: number, percentile: number) => ({
      years,
      low: 0,
      high: 1,
      percentile,
      readings: 100,
    });
    expect(standing([r(1, 1), r(10, 1)])).toBe("The highest in 10 years.");
    expect(standing([r(5, 0.01)])).toBe("The lowest in 5 years.");
    expect(standing([r(5, 0.9)])).toBe("High for the past 5 years.");
    expect(standing([r(1, 0.5)])).toBe("Mid-range over the past 1 year.");
    expect(standing([])).toBeNull();
  });

  it("clamps the marker and merges the euro areas", () => {
    expect(rangePosition(5, 0, 10)).toBe(0.5);
    expect(rangePosition(12, 0, 10)).toBe(1);
    expect(rangePosition(1, 1, 1)).toBe(0.5);
    expect(geographyKey("EA21")).toBe("EA");
  });
});
