import { describe, expect, it } from "vitest";

import type { Settings } from "./performance";
import { formToSettings, settingsToForm } from "./settings-form";

const base: Settings = {
  core_tickers: ["VWCE_EQ", "CSPX_EQ"],
  cost_method: "fifo",
  time_test_years: 3,
  time_test_warning_months: 6,
  exemption_limit: "100000",
  exemption_currency: "CZK",
  sleeve_gap_points: "2",
  sleeve_lookback_years: 2,
  cash_tolerance: "1",
  alert_severity: 2,
  benchmark: null,
  note: "",
};

describe("settings form", () => {
  it("round-trips the saved settings", () => {
    const result = formToSettings(settingsToForm(base));
    expect(result).toEqual({ ok: true, body: base });
  });

  it("reads a blended benchmark in percent and writes weights as fractions", () => {
    const form = settingsToForm(base);
    form.benchmark = [
      { symbol: "ACWI", weight: "60", currency: "usd", basis: "total" },
      { symbol: "AGG", weight: "40", currency: "USD", basis: "price" },
    ];
    form.rebalance = "quarterly";
    const result = formToSettings(form);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.body.benchmark?.rebalance).toBe("quarterly");
      expect(result.body.benchmark?.components.map((c) => [c.symbol, Number(c.weight), c.currency])).toEqual([
        ["ACWI", 0.6, "USD"],
        ["AGG", 0.4, "USD"],
      ]);
    }
  });

  it("keeps the alert severity between 0 (off) and 3", () => {
    const form = settingsToForm({ ...base, alert_severity: 0 });
    expect(formToSettings(form)).toEqual({ ok: true, body: { ...base, alert_severity: 0 } });
    form.alert_severity = "4";
    const result = formToSettings(form);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors).toContain("Alert severity must be a whole number from 0 to 3.");
  });

  it("explains each mistake before anything is sent", () => {
    const form = settingsToForm(base);
    form.core_tickers = "A, A";
    form.time_test_years = "0";
    form.exemption_currency = "CZ";
    form.benchmark = [{ symbol: "ACWI", weight: "60", currency: "USD", basis: "total" }];
    const result = formToSettings(form);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors).toEqual(
        expect.arrayContaining([
          "A core ticker is listed twice.",
          "Holding period must be a whole number from 1 to 10.",
          "The exemption currency must be a three-letter code such as CZK.",
          "Benchmark weights must add up to 100.",
        ]),
      );
    }
  });
});
