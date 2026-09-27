import { describe, expect, it } from "vitest";

import { treemapShadeOpacity } from "./treemap";

describe("treemap tile shade", () => {
  it("is faint for no movement and deepens with the magnitude of the move", () => {
    expect(treemapShadeOpacity(0)).toBeCloseTo(0.3, 5);
    expect(treemapShadeOpacity(0.1)).toBeGreaterThan(treemapShadeOpacity(0.05));
  });

  it("clamps at the cap: a huge move is no darker than exactly the cap", () => {
    expect(treemapShadeOpacity(0.2, 0.2)).toBeCloseTo(1, 5);
    expect(treemapShadeOpacity(5, 0.2)).toBeCloseTo(1, 5);
  });

  it("treats gain and loss magnitude the same way (colour, not opacity, carries the sign)", () => {
    expect(treemapShadeOpacity(0.1)).toBeCloseTo(treemapShadeOpacity(-0.1), 10);
  });

  it("falls back to the faint shade when there's nothing to measure a move against", () => {
    expect(treemapShadeOpacity(null)).toBeCloseTo(0.3, 5);
    expect(treemapShadeOpacity(undefined)).toBeCloseTo(0.3, 5);
  });
});
