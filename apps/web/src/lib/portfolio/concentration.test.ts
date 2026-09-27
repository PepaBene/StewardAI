import { describe, expect, it } from "vitest";

import { concentrationScalePosition, effectiveHoldings, herfindahlIndex } from "./concentration";

describe("Herfindahl-Hirschman concentration", () => {
  it("is 1 for a single position, 1/N for N equal positions", () => {
    expect(herfindahlIndex([1])).toBe(1);
    expect(herfindahlIndex([0.25, 0.25, 0.25, 0.25])).toBeCloseTo(0.25, 10);
  });

  it("rewards even weighting: a lopsided split has a higher HHI than an even one", () => {
    const even = herfindahlIndex([0.2, 0.2, 0.2, 0.2, 0.2]);
    const lopsided = herfindahlIndex([0.6, 0.1, 0.1, 0.1, 0.1]);
    expect(lopsided).toBeGreaterThan(even);
  });

  it("effective holdings is 1/HHI: N for N equal positions, 1 for a single one", () => {
    expect(effectiveHoldings(herfindahlIndex([0.25, 0.25, 0.25, 0.25]))).toBeCloseTo(4, 10);
    expect(effectiveHoldings(herfindahlIndex([1]))).toBeCloseTo(1, 10);
    expect(effectiveHoldings(0)).toBe(0);
  });
});

describe("concentration scale position", () => {
  it("sits at 0 for an even split and 1 for a single position, whatever N is", () => {
    expect(concentrationScalePosition(herfindahlIndex([0.25, 0.25, 0.25, 0.25]), 4)).toBeCloseTo(0, 10);
    expect(concentrationScalePosition(herfindahlIndex([1]), 1)).toBe(1);
  });

  it("moves meaningfully between the two ends for a lopsided-but-not-extreme split", () => {
    const hhi = herfindahlIndex([0.6, 0.1, 0.1, 0.1, 0.1]);
    const position = concentrationScalePosition(hhi, 5);
    expect(position).toBeGreaterThan(0.2);
    expect(position).toBeLessThan(1);
  });
});
