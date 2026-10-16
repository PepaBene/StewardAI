import { describe, expect, it } from "vitest";

import { aheadShare, gapRuns } from "./gap-runs";

describe("gap runs", () => {
  it("keeps one run while the series stays above the benchmark", () => {
    const runs = gapRuns(
      [
        [0, 10],
        [10, 10],
      ],
      [
        [0, 20],
        [10, 20],
      ],
    );
    expect(runs).toEqual([{ ahead: true, d: "M0,10L10,10L10,20L0,20Z" }]);
  });

  it("cuts the run at the crossing point", () => {
    // Series goes from above (y 10 vs 20) to below (y 30 vs 20): they cross at x 5, y 20.
    const runs = gapRuns(
      [
        [0, 10],
        [10, 30],
      ],
      [
        [0, 20],
        [10, 20],
      ],
    );
    expect(runs.map((r) => r.ahead)).toEqual([true, false]);
    expect(runs[0].d).toBe("M0,10L5,20L5,20L0,20Z");
    expect(runs[1].d).toBe("M5,20L10,30L10,20L5,20Z");
  });

  it("breaks at a missing point and skips lone points", () => {
    const runs = gapRuns([[0, 10], null, [20, 10]], [[0, 20], [10, 20], [20, 20]]);
    expect(runs).toEqual([]);
  });

  it("measures how often the series was ahead", () => {
    expect(aheadShare([101, 99, 105, null], [100, 100, 100, 100])).toBeCloseTo(2 / 3);
    expect(aheadShare([1], [null])).toBeNull();
  });
});
