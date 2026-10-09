import { describe, expect, it } from "vitest";

import { apiToTyped, buildFilters, formatMetric, typedToApi, type MetricInfo } from "./discover";

const roic = { id: "roic", label: "ROIC", unit: "fraction", kind: "numeric", enabled: true, definition_version: "1", period: "ttm" } as unknown as MetricInfo;
const off = { id: "tracking", label: "Tracking difference", unit: "fraction", kind: "numeric", enabled: false, disabled_reason: "No source." } as unknown as MetricInfo;

describe("discover helpers", () => {
  it("converts percentages to fractions and back", () => {
    expect(typedToApi(15, "fraction")).toBe(0.15);
    expect(apiToTyped(0.15, "fraction")).toBe(15);
    expect(typedToApi(12, "multiple")).toBe(12);
  });

  it("builds a filter pinned to the metric definition", () => {
    const r = buildFilters([{ id: 1, metric: "roic", op: "gte", value: "15" }], [roic]);
    expect(r.errors).toEqual([]);
    expect(r.filters[0]).toMatchObject({ metric: "roic", op: "gte", value: "0.15", definition_version: "1", period: "ttm" });
  });

  it("rejects disabled metrics with their reason, bad numbers and duplicates", () => {
    expect(buildFilters([{ id: 1, metric: "tracking", op: "lte", value: "1" }], [off]).errors[0]).toContain("No source.");
    expect(buildFilters([{ id: 1, metric: "roic", op: "gte", value: "abc" }], [roic]).errors[0]).toContain("enter a number");
    const dup = buildFilters(
      [
        { id: 1, metric: "roic", op: "gte", value: "1" },
        { id: 2, metric: "roic", op: "gte", value: "2" },
      ],
      [roic],
    );
    expect(dup.errors).toHaveLength(1);
  });

  it("never renders an unknown value as zero", () => {
    expect(formatMetric(undefined, "fraction")).toBeNull();
    expect(formatMetric({ value: null }, "fraction")).toBeNull();
    expect(formatMetric({ value: "0.1234" }, "fraction")).toBe("12.3%");
  });
});
