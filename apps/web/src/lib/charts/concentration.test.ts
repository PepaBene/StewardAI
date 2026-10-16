import { describe, expect, it } from "vitest";

import { effectiveHoldings, hhi, lorenzPoints, namesForShare, topShare } from "./concentration";

describe("concentration", () => {
  it("builds a Lorenz curve largest-first from (0,0) to (1,1)", () => {
    expect(lorenzPoints([1, 3])).toEqual([
      { x: 0, y: 0 },
      { x: 0.5, y: 0.75 },
      { x: 1, y: 1 },
    ]);
    expect(lorenzPoints([])).toEqual([]);
    expect(lorenzPoints([0, -1, Number.NaN])).toEqual([]);
  });

  it("computes HHI and effective holdings", () => {
    expect(hhi([1, 1, 1, 1])).toBeCloseTo(0.25);
    expect(effectiveHoldings([1, 1, 1, 1])).toBeCloseTo(4);
    expect(effectiveHoldings([1])).toBeCloseTo(1);
    expect(hhi([])).toBeNull();
  });

  it("finds how many names hold half the value and the top-20% share", () => {
    expect(namesForShare([50, 30, 10, 10])).toEqual({ names: 1, share: 0.5 });
    expect(topShare([40, 20, 10, 10, 10, 10], 0.2)).toEqual({ names: 2, share: 0.6 });
  });
});
