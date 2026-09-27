import { describe, expect, it } from "vitest";

import { downsample } from "./downsample";

describe("downsample", () => {
  it("keeps every point when there are already few enough", () => {
    expect(downsample([1, 2, 3], 12)).toEqual([1, 2, 3]);
    expect(downsample([], 12)).toEqual([]);
  });

  it("keeps the first and last point when reducing to a maximum", () => {
    const values = Array.from({ length: 100 }, (_, i) => i);
    const reduced = downsample(values, 12);
    expect(reduced).toHaveLength(12);
    expect(reduced[0]).toBe(0);
    expect(reduced[reduced.length - 1]).toBe(99);
  });

  it("spaces points evenly across the input", () => {
    const values = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100];
    expect(downsample(values, 3)).toEqual([0, 50, 100]);
  });
});
