import { describe, expect, it } from "vitest";

import { indexed, indexView, orderSeries, seriesView, type SeriesInput, type SeriesPointInput } from "./series";

const pt = (period: string, first: string | null, latest: string | null, vintages = 2): SeriesPointInput => ({
  period,
  first_value: first,
  first_known_at: "2026-01-01T00:00:00Z",
  latest_value: latest,
  latest_known_at: "2026-02-01T00:00:00Z",
  release_at: null,
  vintages,
  revised: first !== null && latest !== null && first !== latest,
});

const series = (id: string, points: SeriesPointInput[], over: Partial<SeriesInput> = {}): SeriesInput => ({
  source: "eurostat",
  series_id: id,
  geography: "DE",
  label: null,
  unit: "index",
  frequency: "M",
  seasonal_adjustment: null,
  first_basis: "first_recorded",
  source_url: null,
  points,
  revised_periods: points.filter((p) => p.revised).length,
  ...over,
});

describe("seriesView", () => {
  it("computes revisions only where both values are known", () => {
    const v = seriesView(series("a", [pt("2025-11-01", "2.0", "2.5"), pt("2025-12-01", null, "3.0")]));
    expect(v.points[0].revision).toBeCloseTo(0.5);
    expect(v.points[1].revision).toBeNull();
    expect(v.meanAbsRevision).toBeCloseTo(0.5);
    expect(v.change).toBeCloseTo(0.5);
  });

  it("reports no average revision, not zero, when nothing is comparable", () => {
    const v = seriesView(series("a", [pt("2025-11-01", null, "2.0", 1)]));
    expect(v.meanAbsRevision).toBeNull();
    expect(v.change).toBeNull();
    expect(v.hasVintages).toBe(false);
  });

  it("falls back to the series id when no label is stored", () => {
    expect(seriesView(series("cpi_yoy", [pt("2025-11-01", "1", "1")])).title).toBe("cpi_yoy");
  });
});

describe("orderSeries", () => {
  it("puts series without a score last", () => {
    const big = seriesView(series("big", [pt("2025-11-01", "1", "5")]));
    const none = seriesView(series("none", [pt("2025-11-01", null, "5", 1)]));
    const small = seriesView(series("small", [pt("2025-11-01", "1", "2")]));
    expect(orderSeries([none, small, big], "revised").map((v) => v.title)).toEqual(["big", "small", "none"]);
  });
});

describe("indexView", () => {
  it("rebases first and latest together so the revision gap is kept", () => {
    const v = indexView(seriesView(series("a", [pt("p1", "10", "10"), pt("p2", "10", "12")])));
    expect(v.points.map((p) => p.latest)).toEqual([100, 120]);
    expect(v.points[1].first).toBe(100);
    expect(v.points[1].revision).toBeCloseTo(20);
    expect(v.change).toBeCloseTo(20);
  });
});

describe("indexed", () => {
  it("rebases to 100 at the first known value and leaves gaps null", () => {
    const v = seriesView(series("a", [pt("p1", "1", null), pt("p2", "1", "50"), pt("p3", "1", "75")]));
    expect(indexed(v.points)).toEqual([null, 100, 150]);
  });

  it("refuses a zero base", () => {
    const v = seriesView(series("a", [pt("p1", "0", "0"), pt("p2", "1", "1")]));
    expect(indexed(v.points)).toEqual([null, null]);
  });
});
