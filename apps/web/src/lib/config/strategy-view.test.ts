import { describe, expect, it } from "vitest";

import type { StrategyConfig } from "@/lib/api/client";

import { DEFAULT_STRATEGY, newRule, strategyFromBody, strategyToBody } from "./strategy-form";
import {
  allocation,
  bandState,
  changesSummary,
  ruleSummary,
  strategyChanges,
  targetScale,
  versionNumbers,
} from "./strategy-view";

const base: StrategyConfig = {
  ...DEFAULT_STRATEGY,
  cash_target: { target_weight: 0.05, band: 0.03 },
  target_positions: [
    { ticker: "VWCEd_EQ", target_weight: 0.22, band: 0.03, hard_limit: null },
    { ticker: "NVDA_US_EQ", target_weight: 0.06, band: 0.02, hard_limit: 0.1 },
  ],
  rules: [
    { id: "nvda-cap", type: "position_weight", ticker: "NVDA_US_EQ", value: 0.09, operator: "<=", on_breach: "severity_2", description: "", enabled: true },
  ],
};
/** As the editor compares: both sides through the form, so key order and defaults match. */
const norm = (b: StrategyConfig) => strategyToBody(strategyFromBody(b));

describe("versionNumbers", () => {
  it("numbers versions oldest first, whatever the list order", () => {
    const n = versionNumbers([
      { id: 57, created_at: "2026-08-17T00:00:00Z" },
      { id: 12, created_at: "2024-09-27T00:00:00Z" },
      { id: 30, created_at: "2025-01-01T00:00:00Z" },
    ]);
    expect([n.get(12), n.get(30), n.get(57)]).toEqual([1, 2, 3]);
  });
});

describe("targetScale", () => {
  it("rounds the reach up to a 5 % step, between 10 % and 100 %", () => {
    expect(targetScale([0.22 + 0.03, 0.12])).toBe(0.25);
    expect(targetScale([0.251])).toBe(0.3);
    expect(targetScale([0.02])).toBe(0.1);
    expect(targetScale([null, undefined])).toBe(0.1);
    expect(targetScale([1.4])).toBe(1);
  });
});

describe("bandState", () => {
  it("places a weight against target, band and hard limit", () => {
    expect(bandState(null, 0.06, 0.02)).toBe("no_data");
    expect(bandState(0.05, null, null)).toBe("no_target");
    expect(bandState(0.082, 0.06, 0.02)).toBe("above_band");
    expect(bandState(0.03, 0.06, 0.02)).toBe("below_band");
    expect(bandState(0.08, 0.06, 0.02)).toBe("in_band");
    expect(bandState(0.11, 0.06, 0.02, 0.1)).toBe("above_limit");
  });
});

describe("allocation", () => {
  it("sums position and cash targets and reports cash apart", () => {
    const a = allocation(base);
    expect(a.total).toBeCloseTo(0.33);
    expect(a.cash).toBe(0.05);
    expect(allocation({ ...base, cash_target: null }).cash).toBeNull();
  });
});

describe("ruleSummary", () => {
  it("reads each rule type in one line", () => {
    expect(ruleSummary({ ...newRule("position_weight"), ticker: "NVDA_US_EQ", value: "9" })).toBe("NVDA_US_EQ weight ≤ 9 %");
    expect(ruleSummary({ ...newRule("position_weight"), value: "10" })).toBe("Every position ≤ 10 %");
    expect(ruleSummary({ ...newRule("category_weight"), dimension: "sector", bucket: "Semiconductors", value: "25" })).toBe(
      "sector · Semiconductors ≤ 25 %",
    );
    expect(ruleSummary({ ...newRule("cash_weight"), operator: ">=", value: "3" })).toBe("Cash ≥ 3 %");
    expect(ruleSummary({ ...newRule("position_count"), operator: ">=", value: "10" })).toBe("Positions ≥ 10");
  });
});

describe("strategyChanges", () => {
  it("is empty for an unchanged body", () => {
    expect(strategyChanges(norm(base), norm(base))).toEqual([]);
  });

  it("describes target, band, rule and basics changes", () => {
    const after: StrategyConfig = {
      ...base,
      base_currency: "EUR",
      goals: "More",
      target_positions: [
        { ticker: "VWCEd_EQ", target_weight: 0.2, band: 0.03, hard_limit: null },
        { ticker: "NVDA_US_EQ", target_weight: 0.06, band: 0.03, hard_limit: 0.1 },
        { ticker: "CEZ_EQ", target_weight: 0.06, band: 0.02, hard_limit: null },
      ],
      rules: [
        { ...base.rules![0], enabled: false },
        { id: "max-cash", type: "cash_weight", value: 0.1, operator: "<=", on_breach: "severity_1", description: "", enabled: true },
      ],
    };
    expect(strategyChanges(norm(base), norm(after))).toEqual([
      "Base currency CZK → EUR",
      "Goals edited",
      "VWCEd_EQ target 22 % → 20 %",
      "NVDA_US_EQ band ±2 pp → ±3 pp",
      "New target CEZ_EQ 6 %",
      "Rule nvda-cap off",
      "New rule max-cash",
    ]);
  });

  it("reports removals and cash changes", () => {
    const after: StrategyConfig = { ...base, cash_target: null, target_positions: [base.target_positions![0]], rules: [] };
    expect(strategyChanges(norm(base), norm(after))).toEqual([
      "Cash target removed",
      "Target NVDA_US_EQ removed",
      "Rule nvda-cap removed",
    ]);
  });

  it("ignores key order and absent-vs-null fields", () => {
    const reordered = JSON.parse(JSON.stringify(base), (_k, v) =>
      v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).reverse()) : v,
    ) as StrategyConfig;
    const first = reordered.target_positions![0] as Record<string, unknown>;
    delete first.hard_limit;
    expect(strategyChanges(base, reordered)).toEqual([]);
  });

  it("says first version when nothing was saved", () => {
    expect(strategyChanges(null, base)).toEqual(["First version"]);
  });
});

describe("changesSummary", () => {
  it("counts and truncates", () => {
    expect(changesSummary(["a", "b", "c", "d", "e"])).toEqual({ count: "5 unsaved changes", detail: "a, b, c, +2 more" });
    expect(changesSummary(["a"])).toEqual({ count: "1 unsaved change", detail: "a" });
  });
});
