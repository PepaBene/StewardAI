import { describe, expect, it } from "vitest";

import type { DriftReport, StrategyRule } from "@/lib/api/client";

import { complianceRows, holds, ruleRow, thesisRow } from "./rule-compliance";

const position = (ticker: string, weight: number, status: DriftReport["positions"][number]["status"] = "in_band") => ({
  ticker,
  weight,
  target_weight: status === "no_target" ? null : weight,
  band: 0.02,
  lower: weight - 0.02,
  upper: weight + 0.02,
  hard_limit: null,
  drift: 0,
  status,
  held: true,
});

const drift: DriftReport = {
  weight_basis: "total",
  basis_value_base: 1000,
  positions: [position("NVDA_US_EQ", 0.082, "above_band"), position("ASMLa_EQ", 0.098), position("CEZ_EQ", 0.058, "no_target")],
  categories: [
    {
      dimension: "sector",
      buckets: [
        { bucket: "Semiconductors", value_base: 238, weight: 0.238, target_weight: null, band: null, lower: null, upper: null, drift: null, status: "no_target" },
        { bucket: "Utilities", value_base: 58, weight: 0.058, target_weight: null, band: null, lower: null, upper: null, drift: null, status: "no_target" },
      ],
    },
  ],
  cash: { value_base: 39, weight: 0.039, target_weight: 0.05, band: 0.03, lower: 0.02, upper: 0.08, drift: -0.011, status: "in_band" },
  out_of_band: 1,
};

const base = { on_breach: "severity_2" as const, description: "", enabled: true };

describe("rule compliance", () => {
  it("compares with the backend's tolerance", () => {
    expect(holds(0.1 + 1e-12, "<=", 0.1)).toBe(true);
    expect(holds(0.1, "<", 0.1)).toBe(false);
    expect(holds(10, ">=", 10)).toBe(true);
  });

  it("evaluates each rule type against the drift report", () => {
    const rules: StrategyRule[] = [
      { ...base, id: "nvda", type: "position_weight", operator: "<=", value: 0.09, ticker: "NVDA_US_EQ" },
      { ...base, id: "single", type: "position_weight", operator: "<=", value: 0.09 },
      { ...base, id: "semis", type: "category_weight", operator: "<=", value: 0.25, dimension: "sector", bucket: "Semiconductors" },
      { ...base, id: "banks", type: "category_weight", operator: ">=", value: 0.05, dimension: "sector", bucket: "Banks" },
      { ...base, id: "region", type: "category_weight", operator: "<=", value: 0.5, dimension: "region" },
      { ...base, id: "cash", type: "cash_weight", operator: "<=", value: 0.1 },
      { ...base, id: "count", type: "position_count", operator: ">=", value: 10 },
    ];
    const rows = rules.map((r) => ruleRow(r, drift));
    expect(rows.map((r) => r.status)).toEqual(["pass", "breach", "pass", "breach", "unknown", "pass", "breach"]);
    expect(rows[1]).toMatchObject({ detail: "ASML 9.8%", fraction: 2 / 3 });
    expect(rows[3].detail).toBe("Banks 0.0%");
    expect(rows[6].detail).toBe("Positions 3");
    expect(rows[0].label).toBe("NVDA ≤ 9%");
  });

  it("lists bands first and skips disabled rules", () => {
    const rows = complianceRows(drift, {
      schema_version: 1,
      base_currency: "CZK",
      weight_basis: "total",
      goals: "",
      notes: "",
      hard_limit_severity: "severity_3",
      band_breach_severity: "severity_1",
      rules: [
        { ...base, id: "off", type: "cash_weight", operator: "<=", value: 0.01, enabled: false },
        { ...base, id: "on", type: "cash_weight", operator: "<=", value: 0.1, description: "Cash drag at most 10%." },
      ],
    });
    expect(rows.map((r) => r.id)).toEqual(["position-bands", "cash-band", "on"]);
    expect(rows[0]).toMatchObject({ detail: "1 of 2 inside", status: "breach", fraction: 0.5 });
    expect(rows[1]).toMatchObject({ label: "Cash 2%–8%", status: "pass" });
    expect(rows[2].label).toBe("Cash drag at most 10%.");
  });

  it("counts intact theses among the judged", () => {
    expect(thesisRow([{ state: "intact" }, { state: "watch" }, { state: "unjudged" }])).toMatchObject({
      detail: "1 of 2 intact · 1 unjudged",
      fraction: 0.5,
    });
    expect(thesisRow([])).toBeNull();
    expect(thesisRow([{ state: "unjudged" }])?.status).toBe("unknown");
  });
});
