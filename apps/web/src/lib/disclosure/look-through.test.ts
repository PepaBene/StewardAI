import { describe, expect, it } from "vitest";

import { coverageSentence, datingWarnings, exposureOrder, relationshipOrder, weightText, type LookExposure, type LookRelationship, type LookThrough } from "@/lib/disclosure/look-through";

const exp = (ticker: string, total: string): LookExposure => ({ ticker, direct_weight: total, held_directly: true, fund_weight: "0", total_weight: total, funds: [] });
const rel = (ticker: string, weighted: string | null): LookRelationship => ({
  ticker, relationship: "customer", counterparty_key: null, counterparty_name: null, anonymous: true, share: null, share_basis: null, fiscal_period: null,
  known_at: "2026-01-01T00:00:00Z", source_url: null, holding_weight: "0.1", weighted_share: weighted, weight_state: weighted == null ? "unknown" : "reported", concentrated_customer: false,
});

describe("weightText", () => {
  it("shows a fraction as a percentage and a missing one as unknown, never 0%", () => {
    expect(weightText("0.1234")).toBe("12.3%");
    expect(weightText("0")).toBe("0.0%");
    expect(weightText(null)).toBe("unknown");
    expect(weightText("")).toBe("unknown");
  });
});

describe("ordering", () => {
  it("orders exposures largest first with ticker as the tie-break", () => {
    expect(exposureOrder([exp("B", "0.1"), exp("A", "0.1"), exp("C", "0.3")]).map((e) => e.ticker)).toEqual(["C", "A", "B"]);
  });
  it("puts unknown shares after every known one, not at zero", () => {
    expect(relationshipOrder([rel("A", null), rel("B", "0.01"), rel("C", "0.05")]).map((r) => r.ticker)).toEqual(["C", "B", "A"]);
  });
});

describe("coverage and dating", () => {
  const totals = { relationship_covered_weight: "0.4", relationship_covered_holdings: 2, unknown_weight_holdings: ["X"], unknown_weight: "0.05" } as LookThrough["totals"];
  it("states the denominator and the unknown remainder", () => {
    const s = coverageSentence(totals, 5);
    expect(s).toContain("2 of 5 holdings");
    expect(s).toContain("40.0%");
    expect(s).toContain("left unknown rather than counted as zero");
  });
  it("warns about mixed dates and lagging fund reports only when they apply", () => {
    expect(datingWarnings({ mixed_as_of: false, max_fund_lag_days: 0 } as LookThrough)).toEqual([]);
    expect(datingWarnings({ mixed_as_of: true, max_fund_lag_days: 40 } as LookThrough)).toHaveLength(2);
  });
});
