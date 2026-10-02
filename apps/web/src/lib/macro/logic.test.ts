import { describe, expect, it } from "vitest";

import { COUNTRY_COMPARISON_FIXTURE, EXPOSURE_MAP_FIXTURE, MACRO_SERIES_FIXTURE, TRADE_SLICE_FIXTURE } from "./fixture";
import {
  commonTimeDomain,
  concentrationTrend,
  exposureEffect,
  exposureSummary,
  hhi,
  latestGrowth,
  newObservations,
  partnerShares,
  rangePosition,
  revisedObservations,
  seriesPoints,
  slopeRows,
  trailingRange,
  valueFor,
  vintageSummary,
} from "./logic";
import type { MacroObservation, MacroSeries } from "./types";

const obs = (period: string, latest: number, firstRelease: number | null, revisedAt: string | null = null): MacroObservation => ({
  period,
  latest,
  firstRelease,
  releasedAt: `${period}T00:00:00Z`,
  revisedAt,
});
const series = (observations: MacroObservation[]): MacroSeries => ({ ...MACRO_SERIES_FIXTURE[0], observations });

describe("vintages", () => {
  it("returns null, not the latest value, for an unarchived first release", () => {
    const o = obs("2020-12-31", 1.2, null);
    expect(valueFor(o, "latest")).toBe(1.2);
    expect(valueFor(o, "first_release")).toBeNull();
  });

  it("marks revisions only where the value actually moved", () => {
    const s = series([obs("2021-12-31", 2, 2, null), obs("2022-12-31", 3, 2.5, "2023-01-01"), obs("2023-12-31", 1, null)]);
    expect(revisedObservations(s).map((o) => o.period)).toEqual(["2022-12-31"]);
    expect(vintageSummary(s)).toEqual({ total: 3, revised: 1, unavailable: 1, maxRevision: 0.5 });
    expect(seriesPoints(s, "first_release").map((p) => p.value)).toEqual([2, 2.5, null]);
  });

  it("the fixtures carry both revisions and unarchived first releases", () => {
    const sums = MACRO_SERIES_FIXTURE.map(vintageSummary);
    expect(sums.some((x) => x.revised > 0)).toBe(true);
    expect(sums.some((x) => x.unavailable > 0)).toBe(true);
  });
});

describe("alignment and context", () => {
  it("finds one shared time domain", () => {
    const [a, b] = commonTimeDomain(MACRO_SERIES_FIXTURE)!;
    expect(a).toBe("2012-12-31");
    expect(b >= "2026-08-31").toBe(true);
    expect(commonTimeDomain([])).toBeNull();
  });

  it("computes a trailing range with its point count and places a value inside it", () => {
    const s = series([2021, 2022, 2023, 2024, 2025, 2026].map((y, i) => obs(`${y}-12-31`, [1, 5, 3, 2, 4, 3][i], null)));
    const r = trailingRange(s, 3)!;
    expect(r).toMatchObject({ low: 2, high: 4, count: 3 });
    expect(rangePosition(3, r)).toBe(0.5);
    expect(rangePosition(9, r)).toBe(1);
    expect(rangePosition(1, { low: 2, high: 2 })).toBeNull();
    expect(trailingRange(series([]), 5)).toBeNull();
  });

  it("raises alerts only for periods not seen before, not for revisions", () => {
    const prev = [obs("2026-06-30", 1, 1), obs("2026-07-31", 1, 1)];
    const cur = [obs("2026-06-30", 1.4, 1, "2026-09-01"), obs("2026-07-31", 1, 1), obs("2026-08-31", 2, 2)];
    expect(newObservations(prev, cur).map((o) => o.period)).toEqual(["2026-08-31"]);
  });
});

describe("slopegraph", () => {
  const s = slopeRows(COUNTRY_COMPARISON_FIXTURE.rows);
  const by = (c: string) => s.find((r) => r.country === c)!;

  it("ranks each end separately, highest first", () => {
    expect(by("IND").fromRank).toBe(1);
    expect(by("IND").toRank).toBe(1);
    expect(by("CHN").fromRank).toBe(2);
    expect(by("DEU").fromRank).toBe(10);
  });

  it("leaves a missing value unranked rather than ranking it last or zero", () => {
    expect(by("TWN").toRank).toBeNull();
    expect(by("TWN").rankChange).toBeNull();
    expect(by("TWN").change).toBeNull();
  });

  it("reports a positive rank change for a country that moved up", () => {
    expect(by("JPN").rankChange).toBeGreaterThan(0);
    expect(by("KOR").rankChange).toBeLessThan(0);
  });

  it("gives ties the better shared rank", () => {
    const t = slopeRows([
      { country: "A", countryName: "A", from: 3, to: 1 },
      { country: "B", countryName: "B", from: 3, to: 2 },
      { country: "C", countryName: "C", from: 1, to: 3 },
    ]);
    expect(t.map((r) => r.fromRank)).toEqual([1, 1, 3]);
  });
});

describe("trade", () => {
  it("keeps unmapped trade out of the partner mix but in the denominator", () => {
    const p = partnerShares(TRADE_SLICE_FIXTURE, 2025);
    expect(p.unmappedUsd).toBe(2.1e9);
    expect(p.totalUsd).toBeCloseTo(p.mappedUsd + p.unmappedUsd);
    expect(p.shares.reduce((s, x) => s + x.share, 0) + (p.unmappedShare ?? 0)).toBeCloseTo(1);
    expect(p.shares[0].valueUsd).toBeGreaterThanOrEqual(p.shares[1].valueUsd);
  });

  it("computes HHI as 1 for one partner, 1/n for n equal partners, null for none", () => {
    expect(hhi([{ valueUsd: 5 }])).toBe(1);
    expect(hhi([{ valueUsd: 5 }, { valueUsd: 5 }])).toBe(0.5);
    expect(hhi([])).toBeNull();
    expect(hhi([{ valueUsd: 0 }])).toBeNull();
  });

  it("shows the planted shift away from the largest partner in the concentration trend", () => {
    const t = concentrationTrend(TRADE_SLICE_FIXTURE);
    expect(t).toHaveLength(8);
    expect(t[0].hhi).toBeGreaterThan(t[t.length - 1].hhi);
    expect(latestGrowth(TRADE_SLICE_FIXTURE)!.year).toBe(2025);
  });
});

describe("exposure mapping", () => {
  const sum = exposureSummary(EXPOSURE_MAP_FIXTURE);

  it("sums mapped weights per kind and leaves an explicit unknown remainder", () => {
    expect(sum.mapped).toBeCloseTo(0.85);
    expect(sum.unknown).toBeCloseTo(0.15);
    expect(sum.byKind.reported).toBeCloseTo(0.7);
    expect(sum.byKind.estimated).toBeCloseTo(0.1);
    expect(sum.byKind.user_assigned).toBeCloseTo(0.05);
    expect(sum.byTarget[0].target).toBe("CHN");
    expect(sum.overAllocated).toBe(false);
  });

  it("flags weights that exceed 100% instead of rescaling them", () => {
    const over = exposureSummary({ weights: [{ ...EXPOSURE_MAP_FIXTURE.weights[0], weight: 0.7 }, { ...EXPOSURE_MAP_FIXTURE.weights[1], weight: 0.5 }] });
    expect(over.overAllocated).toBe(true);
    expect(over.unknown).toBe(0);
  });

  it("states an effect only where exposure is mapped", () => {
    expect(exposureEffect(sum, "CHN", "China")).toMatchObject({ kind: "mapped", weight: 0.29 });
    expect(exposureEffect(sum, "BRA", "Brazil").kind).toBe("none");
    expect(exposureEffect(sum, "BRA", "Brazil").text).toMatch(/not proof of zero/);
    const empty = exposureSummary({ weights: [] });
    expect(exposureEffect(empty, "CHN", "China")).toMatchObject({ kind: "unknown_only" });
    expect(empty.unknown).toBe(1);
  });
});
