import { describe, expect, it } from "vitest";

import { checksFromBody, checksToBody, FIELDS, validateChecksForm, type ChecksConfig } from "./checks-form";

const defaults = {
  schema_version: 1,
  limits: {
    payout_limit_earnings: 0.75, payout_limit_cash: 0.75, roe_floor: 0.15, roce_floor: 0.15, forecast_roe_floor: 0.15,
    interest_coverage_min: 5, debt_to_equity_max: 0.4, net_debt_to_ebitda_max: 3, cfo_to_debt_min: 0.2, dilution_max: 0.05,
    accruals_max: 0.1, peg_max: 1.5, fcf_yield_margin: 0.01, analyst_count_min: 5, dividend_yield_floor: 0.01,
    dividend_growth_years: 10, no_cut_years_min: 10, small_cap_max: 2e9, liquidity_floor: 1e6, volatility_vs_benchmark_max: 1.5,
    benchmark_volatility: 0.16, debt_maturity_share_max: 0.25, earnings_window_days: 14, insider_window_days: 90,
  },
  dividends: { auto_confirm: true, auto_confirm_tolerance: 0.35, match_window_days: 21, assume_growth: false, min_payments_for_estimate: 4, single_source_history: true, tier_cuts: [1 / 3, 2 / 3] },
  fair_value: {
    stale_after_days: 90, move_alert: 0.1, drift_band: 0.05, fair_multiple_min_r2: 0.3,
    agreement_close: 0.25, agreement_wide: 0.5, consensus_chart_min_snapshots: 8,
  },
} as unknown as ChecksConfig;

describe("checks form", () => {
  it("covers every backend field", () => {
    const keys = new Set(FIELDS.map((f) => `${f.group}.${f.key}`));
    for (const g of ["limits", "dividends", "fair_value"] as const) {
      for (const k of Object.keys(defaults[g] ?? {})) if (k !== "tier_cuts") expect(keys.has(`${g}.${k}`)).toBe(true);
    }
  });

  it("round-trips the body and shows fractions as percent", () => {
    const form = checksFromBody(defaults);
    expect(form.values["limits.roe_floor"]).toBe("15");
    expect(form.values["dividends.auto_confirm"]).toBe("true");
    expect(validateChecksForm(form)).toEqual({});
    const body = checksToBody(form, defaults);
    expect(body.limits?.roe_floor).toBeCloseTo(0.15);
    expect(body.dividends?.tier_cuts?.[1]).toBeCloseTo(2 / 3);
    expect(body.fair_value?.stale_after_days).toBe(90);
  });

  it("flags out-of-bounds and crossed tier cuts", () => {
    const form = checksFromBody(defaults);
    form.values["limits.analyst_count_min"] = "0";
    form.values["fair_value.stale_after_days"] = "3.5";
    form.tierLow = "80";
    form.values["fair_value.agreement_wide"] = "20";
    const errors = validateChecksForm(form);
    expect(errors["fair_value.agreement_wide"]).toEqual(["Must be above the close-agreement cut."]);
    expect(errors["limits.analyst_count_min"]).toEqual(["At least 1."]);
    expect(errors["fair_value.stale_after_days"]).toEqual(["Enter a whole number."]);
    expect(errors["dividends.tier_cuts"]).toBeDefined();
  });
});
