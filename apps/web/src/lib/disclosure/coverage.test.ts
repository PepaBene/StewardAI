import { describe, expect, it } from "vitest";

import { blindSpots, cellFor, cellSummary, failureLine, jurisdictions, money, needsAttention, pct, qualityLine, rollup } from "@/lib/disclosure/coverage";
import { SAMPLE_COVERAGE } from "@/lib/disclosure/fixtures-analytics";

const cells = SAMPLE_COVERAGE.cells;

describe("coverage", () => {
  it("never turns a missing share into zero", () => {
    expect(pct(null)).toBe("unknown");
    expect(pct("0")).toBe("0%");
    expect(pct("0.625", 1)).toBe("62.5%");
    expect(money(null)).toBe("unknown");
  });

  it("rolls cells up with denominators and the earliest and latest known dates", () => {
    const r = rollup(cells);
    const ins = r.find((x) => x.dataset === "insiders")!;
    expect(ins.tickers).toBe(5);
    expect(ins.covered).toBe(3);
    expect(ins.uncovered).toBe(2);
    expect(ins.denominator).toBe(10000);
    expect(ins.coveredValue).toBe(6700);
    expect(ins.share).toBeCloseTo(0.67);
    expect(ins.first).toBe("2025-03-01T00:00:00Z");
    expect(ins.last).toBe("2026-09-12T00:00:00Z");
    const est = r.find((x) => x.dataset === "estimates")!;
    expect(est.covered).toBe(1);
  });

  it("gives no share when the denominator is zero and no date for an uncovered dataset", () => {
    const empty = rollup([{ ...cells[1], denominator_value: "0", covered_value: "0" }]);
    expect(empty[0].share).toBeNull();
    expect(empty[0].first).toBeNull();
  });

  it("describes a cell and flags the ones to look at first", () => {
    expect(cellSummary(cellFor(cells, "filings", "GB"))).toBe("0 of 2 covered");
    expect(cellSummary(cellFor(cells, "filings", "ZZ"))).toBe("no holdings");
    expect(needsAttention(cellFor(cells, "filings", "GB")!)).toBe(true);
    expect(needsAttention(cellFor(cells, "filings", "US")!)).toBe(false);
    expect(needsAttention(cellFor(cells, "insiders", "US")!)).toBe(true);
    expect(jurisdictions(cells)).toEqual(["GB", "US"]);
  });

  it("reports sampled error rates with numerator and denominator", () => {
    const q = SAMPLE_COVERAGE.quality;
    expect(qualityLine(q[0])).toContain("Not sampled");
    expect(qualityLine(q[1])).toContain("1 wrong of 52 labelled");
    expect(qualityLine(q[1])).toContain("1.9%");
  });

  it("lists each blind spot once, tagged with its jurisdiction when it names tickers", () => {
    const spots = blindSpots(cells);
    expect(spots.some((s) => s.text.startsWith("GB: 2 held with no record"))).toBe(true);
    expect(spots.filter((s) => s.text.includes("Named analyst")).length).toBe(1);
  });

  it("writes a failure plainly", () => {
    expect(failureLine(SAMPLE_COVERAGE.failures[0])).toBe("Source sedar_plus (CA): Authentication failed - Session expired");
  });
});
