import { describe, expect, it } from "vitest";

import { foreignCurrencyShare, riskAxes } from "./risk-profile";

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
    expect(axes[4].portfolio).toBe(0.6);

    const partial = riskAxes({ volatility: null, beta: null, maxDrawdown: -0.2, hhi: null, positions: 0, fxShare: null });
    expect(partial.map((a) => a.key)).toEqual(["drawdown"]);
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
