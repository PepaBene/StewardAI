import { describe, expect, it } from "vitest";

import { benchmarkValues, foreignCurrencyShare, RISK_AXIS_DEFS, riskAxes, riskScaleNote, type RiskAxisDef } from "./risk-profile";

describe("risk axes", () => {
  it("normalises each axis on its stated scale and skips unknown ones", () => {
    const axes = riskAxes({
      volatility: 0.15,
      beta: 0.9,
      maxDrawdown: -0.1,
      hhi: 0.25,
      positions: 4,
      fxShare: 0.6,
      benchmarkVolatility: 0.6,
      benchmarkMaxDrawdown: null,
    });
    expect(axes.map((a) => a.key)).toEqual(["volatility", "beta", "drawdown", "concentration", "fx"]);
    expect(axes[0]).toMatchObject({ portfolio: 0.5, benchmark: 1 });
    expect(axes[1].portfolio).toBeCloseTo(0.45);
    expect(axes[1].benchmark).toBe(0.5);
    expect(axes[2]).toMatchObject({ portfolio: 0.25, benchmark: null });
    expect(axes[3].portfolio).toBe(0); // four equal weights: evenly spread
    expect(axes[3].benchmark).toBeNull();
    expect(axes[4]).toMatchObject({ portfolio: 0.6, benchmark: null });

    const partial = riskAxes({ volatility: null, beta: null, maxDrawdown: -0.2, hhi: null, positions: 0, fxShare: null });
    expect(partial.map((a) => a.key)).toEqual(["drawdown"]);
  });

  it("uses the look-through concentration and FX, with the benchmark's, when present", () => {
    const axes = riskAxes({
      volatility: 0.15,
      beta: 1,
      maxDrawdown: -0.1,
      hhi: 0.5,
      positions: 12,
      fxShare: 0.2,
      benchmarkVolatility: 0.12,
      benchmarkMaxDrawdown: -0.08,
      lookThrough: { hhi: 0.06, benchmarkHhi: 0.024, fxShare: 0.85, benchmarkFxShare: 1 },
    });
    const byKey = Object.fromEntries(axes.map((a) => [a.key, a]));
    expect(byKey.concentration.portfolio).toBeCloseTo(0.6); // HHI 0.06 on a 0.10 rim
    expect(byKey.concentration.benchmark).toBeCloseTo(0.24);
    expect(byKey.fx).toMatchObject({ portfolio: 0.85, benchmark: 1 });
    expect(benchmarkValues(axes)).toEqual(axes.map((a) => a.benchmark));
    expect(riskScaleNote(axes)).toContain("look-through HHI 0.10");
  });

  it("leaves the benchmark open where it has no value, and nothing when it has none", () => {
    const axes = riskAxes({ volatility: 0.2, beta: null, maxDrawdown: -0.1, hhi: 0.3, positions: 5, fxShare: 0.5 });
    expect(benchmarkValues(axes)).toBeNull();
    const some = riskAxes({ volatility: 0.2, beta: 1.1, maxDrawdown: -0.1, hhi: 0.3, positions: 5, fxShare: 0.5 });
    expect(benchmarkValues(some)).toEqual([null, 0.5, null, null, null]);
  });

  it("takes extra axes from the definition list (extension point)", () => {
    const valuation: RiskAxisDef = {
      key: "valuation",
      label: "Valuation",
      compute: () => ({ portfolio: 0.4, benchmark: 0.5, rim: "P/E 40" }),
    };
    const axes = riskAxes({ volatility: 0.15, beta: null, maxDrawdown: null, hhi: null, positions: 0, fxShare: null }, [
      ...RISK_AXIS_DEFS,
      valuation,
    ]);
    expect(axes.map((a) => a.key)).toEqual(["volatility", "valuation"]);
    expect(riskScaleNote(axes)).toBe("Rim = 30% volatility, P/E 40; centre = none.");
  });

  it("counts every non-base currency as foreign", () => {
    const buckets = [
      { bucket: "USD", value_base: 45 },
      { bucket: "CZK", value_base: 10 },
      { bucket: "EUR", value_base: 40 },
    ];
    expect(foreignCurrencyShare(buckets, "CZK", 100)).toBeCloseTo(0.85);
    expect(foreignCurrencyShare(undefined, "CZK", 100)).toBeNull();
  });
});
