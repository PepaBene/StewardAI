import { describe, expect, it } from "vitest";

import { fromMultipleDraft, setPerYear, sortMultiple, toMultipleDraft, withYears, type MultipleAssumptions } from "./multiple";

const base: MultipleAssumptions = {
  name: "base",
  years: 5,
  base_revenue: null,
  revenue_growth: ["0.08"],
  net_margin: [0.12],
  exit_pe: "18",
  share_change: [0],
  diluted_shares: null,
  risk_free: 0.04,
  equity_risk_premium: 0.05,
  beta: 1,
  extra_premium: 0,
  discount_rate: null,
};

describe("multiple-path draft", () => {
  it("round-trips assumptions through the draft in percent", () => {
    const d = toMultipleDraft(base);
    expect(d.growth).toEqual(["8"]);
    const back = fromMultipleDraft(d);
    expect(typeof back).toBe("object");
    if (typeof back === "object") {
      expect(back.revenue_growth).toEqual([0.08]);
      expect(back.exit_pe).toBe(18);
      expect(back.discount_rate).toBeNull();
    }
  });

  it("resizes per-year paths with the horizon and keeps constants single", () => {
    const d = { ...toMultipleDraft(base), growth: setPerYear(["8"], true, 5) };
    const seven = withYears(d, 7);
    expect(seven.growth).toHaveLength(7);
    expect(seven.margin).toHaveLength(1);
    expect(setPerYear(seven.growth, false, 7)).toEqual(["8"]);
  });

  it("names the first bad field", () => {
    expect(fromMultipleDraft({ ...toMultipleDraft(base), exitPe: "0" })).toMatch(/exit P\/E/);
    expect(fromMultipleDraft({ ...toMultipleDraft(base), years: "12" })).toMatch(/3 to 10/);
    expect(fromMultipleDraft({ ...toMultipleDraft(base), margin: ["x"] })).toMatch(/net margin/);
  });

  it("orders bear, base, bull", () => {
    expect(sortMultiple([{ name: "bull" }, { name: "bear" }, { name: "base" }]).map((x) => x.name)).toEqual(["bear", "base", "bull"]);
  });
});
