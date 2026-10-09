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
  splits: [],
  mergers: [],
  spin_offs: [],
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

  it("round-trips entered splits and rejects a ratio of 1 or a missing date", () => {
    const split = { ticker: "NVDA_US_EQ", day: "2024-06-10", ratio: "10", note: "10-for-1" };
    expect(formToSettings(settingsToForm({ ...base, splits: [split] }))).toEqual({ ok: true, body: { ...base, splits: [split] } });
    const form = settingsToForm(base);
    form.splits = [{ ticker: "A", day: "2024-06-10", ratio: "1", note: "" }];
    expect(formToSettings(form).ok).toBe(false);
    form.splits = [{ ticker: "A", day: "", ratio: "2", note: "" }];
    expect(formToSettings(form).ok).toBe(false);
    form.splits = [{ ticker: "", day: "", ratio: "", note: "" }];
    expect(formToSettings(form)).toEqual({ ok: true, body: base });
  });

  it("round-trips mergers and spin-offs, with the cost share typed as a percent", () => {
    const merger = { old_ticker: "AAA_US_EQ", new_ticker: "BBB_US_EQ", day: "2025-03-03", ratio: "1.5", note: "" };
    const spin = { parent_ticker: "AAA_US_EQ", child_ticker: "CCC_US_EQ", day: "2025-04-01", ratio: "0.5", cost_share: "0.2", note: "" };
    const saved = { ...base, mergers: [merger], spin_offs: [spin] };
    const form = settingsToForm(saved);
    expect(form.spin_offs[0].cost_percent).toBe("20");
    expect(formToSettings(form)).toEqual({ ok: true, body: saved });
  });

  it("rejects a merger or spin-off that is incomplete, circular or out of range", () => {
    const form = settingsToForm(base);
    form.mergers = [{ old_ticker: "A", new_ticker: "A", day: "2025-03-03", ratio: "1", note: "" }];
    expect(formToSettings(form).ok).toBe(false);
    form.mergers = [{ old_ticker: "A", new_ticker: "B", day: "", ratio: "1", note: "" }];
    expect(formToSettings(form).ok).toBe(false);
    form.mergers = [];
    form.spin_offs = [{ parent_ticker: "A", child_ticker: "B", day: "2025-04-01", ratio: "1", cost_percent: "120", note: "" }];
    expect(formToSettings(form).ok).toBe(false);
    form.spin_offs = [{ parent_ticker: "", child_ticker: "", day: "", ratio: "", cost_percent: "", note: "" }];
    expect(formToSettings(form)).toEqual({ ok: true, body: base });
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
          "Benchmark weights must add up to 100 (use more decimals, such as 33.333333, for thirds).",
        ]),
      );
    }
  });

  it("uses the backend tolerance for benchmark weights", () => {
    const row = (weight: string) => ({ symbol: weight, weight, currency: "USD", basis: "total" as const });
    const form = settingsToForm(base);
    form.benchmark = [row("33.33"), row("33.33"), row("33.34") /* sums to 100.00 */];
    form.benchmark[1].symbol = "B";
    form.benchmark[2].symbol = "C";
    expect(formToSettings(form).ok).toBe(true);
    // 99.99 passed the old 0.01 tolerance but the backend answers 422 for it
    form.benchmark = [row("33.33"), row("33.33"), row("33.33")];
    form.benchmark[1].symbol = "B";
    form.benchmark[2].symbol = "C";
    expect(formToSettings(form).ok).toBe(false);
    form.benchmark = [row("33.333333"), row("33.333333"), row("33.333334")];
    form.benchmark[1].symbol = "B";
    form.benchmark[2].symbol = "C";
    expect(formToSettings(form).ok).toBe(true);
  });
});
