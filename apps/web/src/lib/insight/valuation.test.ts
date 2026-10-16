import { describe, expect, it } from "vitest";

import { coveredFairValue, groupHoldings, type HoldingFairValue } from "./valuation";

const h = (ticker: string, upside: number | null, stale = false, fairAccount: number | null = 100): HoldingFairValue =>
  ({
    ticker,
    name: ticker,
    quantity: 1,
    price: 10,
    price_currency: "USD",
    value_account: 90,
    fair_value: upside === null ? null : 10 * (1 + upside),
    fair_value_native: null,
    native_currency: null,
    fair_value_account: upside === null ? null : fairAccount,
    upside,
    basis: "dcf_default",
    basis_label: "DCF",
    method_version: null,
    scenario_version: null,
    as_of: null,
    inputs_as_of: null,
    age_days: null,
    stale,
    stale_reasons: [],
    reason: upside === null ? "no fair value" : null,
    history: [],
  }) as HoldingFairValue;

describe("portfolio valuation groups", () => {
  it("puts stale before the price verdict and never counts uncovered", () => {
    const g = groupHoldings([h("A", -0.2), h("B", 0.3), h("C", -0.5, true), h("D", null), h("E", 0.1)]);
    expect(g.over.map((x) => x.ticker)).toEqual(["A"]);
    expect(g.stale.map((x) => x.ticker)).toEqual(["C"]);
    expect(g.under.map((x) => x.ticker)).toEqual(["B", "E"]);
    expect(g.uncovered.map((x) => x.ticker)).toEqual(["D"]);
  });

  it("sums fair value over covered holdings only", () => {
    expect(coveredFairValue([h("A", 0.1, false, 110), h("B", null), h("C", -0.1, false, 90)])).toBe(200);
  });
});
