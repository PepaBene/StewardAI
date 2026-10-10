import { describe, expect, it } from "vitest";

import { gateProgress, outcomeText, reviewOrder, type PairReviewItem } from "@/lib/disclosure/pair-reviews";

const pair = (over: Partial<PairReviewItem>): PairReviewItem => ({
  earlier_id: 1, later_id: 2, series: "published_analyst_consensus", metric: "Revenue", segment: null, fiscal_period: "FY2026Q3",
  earlier_known_at: "2026-05-01T00:00:00Z", later_known_at: "2026-06-01T00:00:00Z", comparable: true, break_reasons: [], statistic: "mean",
  earlier_value: "100", later_value: "110", change: "10", change_pct: "10", pct_note: null, elapsed_days: 31, analyst_count_change: null,
  analyst_count_note: null, direction: "up", note: null, formula_version: "revision.v1", reviewed: false, correct: null, review_note: null, reviewed_at: null,
  ...over,
});

describe("reviewOrder", () => {
  it("lists unreviewed pairs first, newest later file first", () => {
    const a = pair({ earlier_id: 1, reviewed: true, correct: true });
    const b = pair({ earlier_id: 2, later_known_at: "2026-07-01T00:00:00Z" });
    const c = pair({ earlier_id: 3, later_known_at: "2026-08-01T00:00:00Z" });
    expect(reviewOrder([a, b, c]).map((p) => p.earlier_id)).toEqual([3, 2, 1]);
  });
});

describe("gateProgress", () => {
  const gate = { reviewed: 12, needed: 20, correct: 11, rate: "0.91", bar: "0.90", met: false, reason: "12 of 20 reviewed pairs" };
  it("says how many pairs remain", () => {
    expect(gateProgress(gate)).toEqual({ remaining: 8, text: "8 more pairs to review. 12 of 20 reviewed pairs." });
  });
  it("never reports a negative remainder, and keeps the reason when the count is reached but the bar is missed", () => {
    const g = { ...gate, reviewed: 25, met: false, reason: "25 pairs reviewed but only 80% correct (bar 90%)" };
    expect(gateProgress(g)).toEqual({ remaining: 0, text: g.reason });
  });
  it("uses the reason alone once the gate is met", () => {
    expect(gateProgress({ ...gate, reviewed: 20, met: true, reason: "20 pairs reviewed, 19 correct" }).text).toBe("20 pairs reviewed, 19 correct");
  });
});

describe("outcomeText", () => {
  it("states a refusal with its reason and no number", () => {
    const t = outcomeText(pair({ comparable: false, break_reasons: ["panel_method_changed"], change: null, earlier_value: null, later_value: null }));
    expect(t).toBe("Not compared: panel method changed.");
  });
  it("states the change, and a missing value as not stated", () => {
    expect(outcomeText(pair({}))).toBe("Revenue: 100 to 110, change 10 (10%).");
    expect(outcomeText(pair({ earlier_value: null, change_pct: null, pct_note: "earlier value is zero" }))).toBe("Revenue: not stated to 110, change 10 (earlier value is zero).");
  });
});
