import { describe, expect, it } from "vitest";

import { betaOf, betaScale, divergingBar, isCurrency, isSignificant } from "./factors";

describe("factor helpers", () => {
  it("grows a bar from the centre in the direction of the loading", () => {
    expect(divergingBar(0.5, 1)).toEqual({ left: 50, width: 25 });
    expect(divergingBar(-0.5, 1)).toEqual({ left: 25, width: 25 });
    expect(divergingBar(3, 1)).toEqual({ left: 50, width: 50 });
    expect(divergingBar(Number.NaN, 1)).toEqual({ left: 50, width: 0 });
    expect(divergingBar(null, 1)).toEqual({ left: 50, width: 0 });
  });

  it("takes the scale from the largest absolute loading and never returns zero", () => {
    expect(betaScale([0.2, -0.9, null, undefined])).toBe(0.9);
    expect(betaScale([])).toBe(1);
    expect(betaScale([0, Number.NaN])).toBe(1);
  });

  it("treats a t-statistic of 2 either way as measured", () => {
    expect(isSignificant(2)).toBe(true);
    expect(isSignificant(-2.5)).toBe(true);
    expect(isSignificant(1.9)).toBe(false);
    expect(isSignificant(undefined)).toBe(false);
  });

  it("finds a loading in a fit and recognises the currency factors", () => {
    const fit = { r_squared: 0.9, observations: 100, alpha_annual: 0, betas: [{ factor: "HML", label: "Value", beta: 0.4, t_stat: 3 }] };
    expect(betaOf(fit, "HML")?.beta).toBe(0.4);
    expect(betaOf(fit, "SMB")).toBeUndefined();
    expect(betaOf(null, "HML")).toBeUndefined();
    expect(isCurrency("USD")).toBe(true);
    expect(isCurrency("MOM")).toBe(false);
  });
});
