import { describe, expect, it } from "vitest";

import type { StrategyConfig } from "@/lib/api/client";

import {
  DEFAULT_STRATEGY,
  fractionToPercent,
  mergeErrors,
  newCategory,
  newPositionTarget,
  normalizeStrategyErrors,
  percentToFraction,
  shiftDecimal,
  strategyFromBody,
  strategyToBody,
  targetSumErrors,
} from "./strategy-form";

/** Every field set, including optional ones at null and awkward binary fractions. */
const FULL: StrategyConfig = {
  schema_version: 1,
  base_currency: "CZK",
  fx_source: "ecb",
  weight_basis: "invested",
  goals: "Grow capital over 15 years.\nKeep drawdowns tolerable.",
  horizon_years: 15,
  notes: "Prefer quality compounders. Avoid leverage.",
  categories: ["sector", "geography", "currency", "style"],
  classifications: {
    ASML_EQ: { sector: "Semiconductors", geography: "Europe", style: "Quality" },
    AAPL_US_EQ: { sector: "Technology", currency: "USD" },
    CEZ_CZ_EQ: {},
  },
  cash_target: { target_weight: 0.07, band: 0.035 },
  target_positions: [
    { ticker: "ASML_EQ", target_weight: 0.1, band: 0.02, hard_limit: 0.15 },
    { ticker: "AAPL_US_EQ", target_weight: 0.123456789, band: 0.0125, hard_limit: null },
    { ticker: "CEZ_CZ_EQ", target_weight: 0.29, band: 0, hard_limit: 0.57 },
  ],
  target_categories: [
    { dimension: "sector", bucket: "Semiconductors", target_weight: 0.3, band: 0.05 },
    { dimension: "geography", bucket: "Europe", target_weight: 0.6, band: 0.1 },
    { dimension: "geography", bucket: "US", target_weight: 0.4, band: 0.1 },
  ],
  rules: [
    { id: "max-single-position", type: "position_weight", operator: "<=", value: 0.1, ticker: null, exclude: ["VUAA_EQ", "CSPX_EQ"], on_breach: "severity_2", description: "", enabled: true },
    { id: "asml-cap", type: "position_weight", operator: "<", value: 0.2, ticker: "ASML_EQ", exclude: [], on_breach: "severity_3", description: "Hard cap", enabled: true },
    { id: "max-sector", type: "category_weight", operator: "<=", value: 0.35, dimension: "sector", bucket: null, on_breach: "severity_1", description: "", enabled: true },
    { id: "europe-min", type: "category_weight", operator: ">=", value: 0.2, dimension: "geography", bucket: "Europe", on_breach: "severity_2", description: "At least a fifth in Europe", enabled: true },
    { id: "cash-floor", type: "cash_weight", operator: ">", value: 0.01, on_breach: "severity_2", description: "", enabled: true },
    { id: "diversified", type: "position_count", operator: ">=", value: 12, on_breach: "severity_1", description: "", enabled: true },
  ],
  risk: {
    max_volatility: 0.2,
    max_beta: 1.2,
    position_loss_review: 0.25,
    max_look_through_weight: null,
    max_sector_weight: 0.4,
    max_country_weight: null,
    max_drawdown: 0.2,
    max_pair_correlation: 0.85,
    correlation_min_combined_weight: 0.1,
    max_var_95: 0.03,
    max_tracking_error: null,
    max_stress_loss: 0.15,
    stress_equity_fall: 0.2,
    credit_spread_alert_bp: 600,
    max_valuation_terminal_growth: null,
    max_valuation_growth: null,
    on_breach: "severity_2",
    auto_tiers: false,
    tier_speculative_market_cap: 2e9,
    tier_core_min_market_cap: 1e10,
  },
  hard_limit_severity: "severity_2",
  band_breach_severity: "severity_2",
  // Not editable in the form; a save must keep it as loaded, not reset it to the defaults.
  pretrade: {
    return_hurdle: 0.25,
    asymmetry_gate: 3,
    start_size_core: 0.06,
    start_size_growth: 0.03,
    start_size_speculative: 0.01,
    switch_safety_margin: 0.04,
    short_term_tax_rate: 0.15,
    trade_cost_bps: 5,
    fx_cost_bps: 15,
    risk_contribution_cap: 0.2,
  },
};

describe("percent conversion", () => {
  it("shows fractions as exact percentages", () => {
    expect(fractionToPercent(0.07)).toBe("7");
    expect(fractionToPercent(0.035)).toBe("3.5");
    expect(fractionToPercent(0.123456789)).toBe("12.3456789");
    expect(fractionToPercent(1)).toBe("100");
    expect(fractionToPercent(0)).toBe("0");
    expect(fractionToPercent(1e-7)).toBe("0.00001");
    expect(fractionToPercent(null)).toBe("");
  });

  it("parses percentages to the exact fraction", () => {
    expect(percentToFraction("7")).toBe(0.07);
    expect(percentToFraction("3,5 %")).toBe(0.035);
    expect(percentToFraction(" 12.3456789 ")).toBe(0.123456789);
    expect(percentToFraction(".5")).toBe(0.005);
    expect(percentToFraction("abc")).toBe("abc");
    expect(percentToFraction("")).toBe("");
  });

  it("round-trips many fractions without floating-point drift", () => {
    for (let i = 0; i <= 1000; i++) {
      const fraction = i / 1000;
      expect(percentToFraction(fractionToPercent(fraction))).toBe(fraction);
    }
    expect(0.07 * 100).not.toBe(7); // why the decimal shift matters
  });

  it("shifts exponent notation", () => {
    expect(shiftDecimal("1.5e-3", 2)).toBe("0.15");
    expect(shiftDecimal("-0.5", 2)).toBe("-50");
    expect(shiftDecimal("x", 2)).toBeNull();
  });
});

describe("strategy form", () => {
  it("round-trips every field of a fully populated body", () => {
    expect(strategyToBody(strategyFromBody(FULL))).toEqual(FULL);
  });

  it("starts from the backend defaults with a CZK base when nothing is saved", () => {
    const body = strategyToBody(strategyFromBody(null));
    expect(body).toEqual(DEFAULT_STRATEGY);
    expect(body.base_currency).toBe("CZK");
    expect(body.fx_source).toBeNull();
  });

  it("keeps a holding's buckets when its dimension is renamed", () => {
    const form = strategyFromBody(FULL);
    form.categories[0].name = "industry";
    const body = strategyToBody(form);
    expect(body.categories![0]).toBe("industry");
    expect(body.classifications!.ASML_EQ).toEqual({ industry: "Semiconductors", geography: "Europe", style: "Quality" });
  });

  it("drops a removed dimension's buckets and empty cells", () => {
    const form = strategyFromBody(FULL);
    form.categories = form.categories.filter((c) => c.name !== "style");
    form.classifications[1].buckets[form.categories[1].key] = "  ";
    form.categories.push(newCategory("theme"));
    const body = strategyToBody(form);
    expect(body.classifications!.ASML_EQ).toEqual({ sector: "Semiconductors", geography: "Europe" });
    expect(body.classifications!.AAPL_US_EQ).toEqual({ sector: "Technology", currency: "USD" });
  });

  it("changing a target changes the saved fraction", () => {
    const form = strategyFromBody(FULL);
    form.target_positions[0].target_weight = "12.5";
    form.target_positions.push({ ...newPositionTarget("MSFT_US_EQ"), target_weight: "0" });
    const body = strategyToBody(form);
    expect(body.target_positions![0].target_weight).toBe(0.125);
    expect(body.target_positions![3]).toEqual({ ticker: "MSFT_US_EQ", target_weight: 0, band: 0.02, hard_limit: null });
  });

  it("switching a rule's type keeps only that type's fields", () => {
    const form = strategyFromBody(FULL);
    form.rules[1].type = "cash_weight";
    form.rules[5].value = "abc";
    const body = strategyToBody(form);
    expect(body.rules![1]).toEqual({ id: "asml-cap", type: "cash_weight", operator: "<", value: 0.2, on_breach: "severity_3", description: "Hard cap", enabled: true });
    expect(body.rules![5].value).toBe("abc");
  });
});

describe("target sums", () => {
  it("accepts targets summing to exactly 100 %", () => {
    // 0.1 + 0.123456789 + 0.29 + 0.07 < 1, and the geography buckets sum to exactly 1.
    expect(targetSumErrors(FULL)).toEqual({});
    const form = strategyFromBody(FULL);
    form.target_positions[1].target_weight = "53.1"; // 10 + 53.1 + 29 + 7 = 99.1
    form.target_positions[0].target_weight = "10.9"; // = 100
    expect(targetSumErrors(strategyToBody(form))).toEqual({});
  });

  it("flags every position and the cash target when they sum above 100 %", () => {
    const form = strategyFromBody(FULL);
    form.target_positions[1].target_weight = "60";
    const errors = targetSumErrors(strategyToBody(form));
    expect(Object.keys(errors).sort()).toEqual([
      "cash_target.target_weight",
      "target_positions.0.target_weight",
      "target_positions.1.target_weight",
      "target_positions.2.target_weight",
    ]);
    expect(errors["cash_target.target_weight"][0]).toContain("106 %");
  });

  it("flags only the rows of the dimension that sums above 100 %", () => {
    const form = strategyFromBody(FULL);
    form.target_categories[2].target_weight = "40.5";
    const errors = targetSumErrors(strategyToBody(form));
    expect(Object.keys(errors).sort()).toEqual([
      "target_categories.1.target_weight",
      "target_categories.2.target_weight",
    ]);
    expect(errors["target_categories.1.target_weight"][0]).toContain("geography");
  });
});

describe("strategy errors", () => {
  it("moves union-tagged rule errors onto the rule's field and merges sources", () => {
    const normalized = normalizeStrategyErrors({
      "rules.2.category_weight.dimension": ["bad"],
      "rules.0.position_weight": ["missing"],
      "": ["sum above 1"],
    });
    expect(normalized).toEqual({ "rules.2.dimension": ["bad"], "rules.0": ["missing"], "": ["sum above 1"] });
    expect(mergeErrors({ a: ["x"] }, { a: ["x", "y"], b: ["z"] })).toEqual({ a: ["x", "y"], b: ["z"] });
  });
});

describe("strategy form: breach severities", () => {
  it("defaults a body saved before them to today's severities", () => {
    const { hard_limit_severity: _h, band_breach_severity: _b, ...old } = FULL;
    void _h;
    void _b;
    const body = strategyToBody(strategyFromBody(old as StrategyConfig));
    expect(body.hard_limit_severity).toBe("severity_3");
    expect(body.band_breach_severity).toBe("severity_1");
  });
});
