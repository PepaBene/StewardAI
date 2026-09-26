import { describe, expect, it } from "vitest";

import type { ComplianceRow, ReviewBody, StrategyConfig } from "@/lib/api/client";

import {
  actionBasis,
  complianceAnchor,
  complianceCounts,
  kpiAnchor,
  missingRules,
  monthLabel,
  parseKpiRef,
  reviewIndex,
  sortCompliance,
  strategyVersionOf,
} from "./monthly";

const row = (id: string, kind: ComplianceRow["kind"], status: ComplianceRow["status"]): ComplianceRow => ({
  id,
  kind,
  description: id,
  status,
  detail: "",
  source_ids: ["S1"],
});

const STRATEGY = {
  rules: [
    { id: "max-position", type: "position_weight", operator: "<=", value: 0.1, on_breach: "severity_2", description: "No position above 10 %" },
    { id: "min-cash", type: "cash_weight", operator: ">=", value: 0.02, on_breach: "severity_1", description: "Cash at least 2 %" },
    { id: "count", type: "position_count", operator: "<=", value: 30, on_breach: "severity_1", description: "At most 30 positions" },
  ],
} as unknown as StrategyConfig;

const BODY: ReviewBody = {
  month: "2026-09",
  headline: "One breach",
  as_of: "2026-09-30",
  total_base: 100000,
  base_currency: "CZK",
  compliance: [
    row("max-position", "rule", "breach"),
    row("min-cash", "rule", "pass"),
    row("count", "rule", "unknown"),
    row("hard_limit:AAPL_US_EQ", "hard_limit", "pass"),
    row("band:position:AAPL_US_EQ", "band", "breach"),
  ],
  kpis: [
    {
      ticker: "AAPL_US_EQ",
      kpi_id: "revenue_growth",
      ref: "AAPL_US_EQ:revenue_growth",
      description: "Revenue grows",
      status: "pass",
      period: "Q3 2026",
      verdict_id: 5,
      source_ids: ["S3"],
    },
  ],
  reviews_due: [],
  flags: [{ text: "AAPL is above its band.", source_ids: ["S2"], written_by: "code" }],
  actions: [
    {
      action: "Trim AAPL to its target",
      ticker: "AAPL_US_EQ",
      rationale: "Above band and the rule.",
      rule_ids: ["max-position", "band:position:AAPL_US_EQ", "gone-rule"],
      kpis: ["AAPL_US_EQ:revenue_growth", "MSFT_US_EQ:margin"],
    },
  ],
  dropped_actions: 0,
  sources: [
    { id: "S1", kind: "strategy", ref: "42", title: "Investment strategy", app_path: "/config/strategy", tier: 1 },
    { id: "S2", kind: "position", ref: "snapshot:2026-09-30", title: "Snapshot", app_path: "/", tier: 1 },
    { id: "S3", kind: "verdict", ref: "5", title: "Verdict", app_path: "/holdings/AAPL_US_EQ", tier: 1 },
  ],
  model: "claude-opus-5-5",
  notes: [],
};

describe("monthly review: compliance table", () => {
  it("finds no missing rule when every strategy rule has a row", () => {
    expect(missingRules(STRATEGY, BODY.compliance)).toEqual([]);
  });

  it("names every strategy rule the table leaves out, never silently", () => {
    const rows = BODY.compliance.filter((r) => r.id !== "min-cash");
    expect(missingRules(STRATEGY, rows)).toEqual([{ id: "min-cash", description: "Cash at least 2 %" }]);
    // A hard-limit row with the same id does not stand in for the rule.
    expect(missingRules(STRATEGY, [row("count", "band", "pass")]).map((r) => r.id)).toEqual(["max-position", "min-cash", "count"]);
    expect(missingRules(null, rows)).toEqual([]);
  });

  it("keeps every row when sorting: breaches first, then unknown, then pass", () => {
    const sorted = sortCompliance(BODY.compliance);
    expect(sorted).toHaveLength(BODY.compliance.length);
    expect(sorted.map((r) => r.id)).toEqual([
      "max-position",
      "band:position:AAPL_US_EQ",
      "count",
      "min-cash",
      "hard_limit:AAPL_US_EQ",
    ]);
    expect(complianceCounts(BODY.compliance)).toEqual({ pass: 2, breach: 2, unknown: 1 });
  });

  it("reads the strategy version the review was judged against", () => {
    expect(strategyVersionOf(BODY.sources)).toBe(42);
    expect(strategyVersionOf([{ kind: "strategy", ref: "active" }])).toBeNull();
    expect(strategyVersionOf([])).toBeNull();
  });
});

describe("monthly review: proposed actions", () => {
  it("links each rule and KPI an action cites to its row, and marks the ones not in the review", () => {
    const basis = actionBasis(BODY.actions[0], BODY);
    expect(basis.rules.map((r) => r.href)).toEqual([
      `#${complianceAnchor("max-position")}`,
      `#${complianceAnchor("band:position:AAPL_US_EQ")}`,
      null,
    ]);
    expect(basis.kpis[0]).toMatchObject({ href: `#${kpiAnchor("AAPL_US_EQ:revenue_growth")}`, holdingHref: "/holdings/AAPL_US_EQ" });
    expect(basis.kpis[1]).toMatchObject({ row: null, href: null, ticker: "MSFT_US_EQ", holdingHref: "/holdings/MSFT_US_EQ" });
  });

  it("makes anchors safe for ids with colons", () => {
    expect(complianceAnchor("band:position:AAPL_US_EQ")).toBe("compliance-band_position_AAPL_US_EQ");
    expect(parseKpiRef("AAPL_US_EQ:revenue_growth")).toEqual({ ticker: "AAPL_US_EQ", kpiId: "revenue_growth" });
    expect(parseKpiRef("nocolon")).toBeNull();
  });

  it("resolves every citation to a source card", () => {
    const index = reviewIndex(BODY);
    expect(index.unresolved).toEqual([]);
    expect(index.cards.map((c) => c.source.id)).toEqual(["S1", "S2", "S3"]);
  });

  it("names the month", () => {
    expect(monthLabel("2026-09", "en-GB")).toBe("September 2026");
    expect(monthLabel("Q3")).toBe("Q3");
  });
});
