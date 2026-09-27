import { describe, expect, it } from "vitest";

import type { BoardKpi, BoardRow, ThesisKPI, VerdictOut } from "@/lib/api/client";

import {
  boardSummary,
  confidenceCounts,
  countsText,
  kpiCellLabel,
  kpiLabel,
  kpiPanelId,
  NEVER_JUDGED,
  observedVsTarget,
  OVERALL_STATUS,
  statusCounts,
} from "./board";

const kpi: ThesisKPI = {
  id: "rev-growth",
  metric: "revenue_growth_yoy",
  operator: ">=",
  threshold: 5,
  unit: "%",
  basis: "reported",
  period: "quarter",
  description: "",
};

const verdict: VerdictOut = {
  id: 7,
  kpi_id: "rev-growth",
  period: "Q2 FY2026",
  fiscal_year: 2026,
  fiscal_period: "Q2",
  status: "breach",
  observed_value: 3.214,
  operator: ">=",
  threshold: 4,
  threshold_unit: "%",
  rationale: "Revenue grew 3.2%, below 4%.",
  fact_ids: [11, 12],
  config_version_id: 3,
  created_at: "2026-09-20T10:00:00Z",
  confidence: "verified",
  stale: false,
};

function row(overall: BoardRow["overall"], extra: Partial<BoardRow> = {}): BoardRow {
  return {
    ticker: "AAPL_US_EQ",
    name: "Apple",
    why_owned: "",
    review_date: null,
    review_overdue: false,
    held: true,
    weight: 0.1,
    overall,
    counts: { pass: 0, breach: 0, unknown: 0 },
    kpis: [],
    open_review_items: 0,
    score: null,
    score_history: [],
    state: "unjudged",
    ...extra,
  };
}

describe("thesis board", () => {
  it("names every overall status, including a thesis without KPIs", () => {
    expect(Object.keys(OVERALL_STATUS).sort()).toEqual(["breach", "no_kpis", "pass", "unknown"]);
    expect(OVERALL_STATUS.no_kpis.label).toBe("No KPIs");
  });

  it("counts each status, worst first, and reads without colour", () => {
    const counts = statusCounts({ counts: { pass: 3, unknown: 2 } });
    expect(counts).toEqual({ breach: 0, unknown: 2, pass: 3 });
    expect(countsText(counts)).toBe("0 breach · 2 unknown · 3 pass");
  });

  it("summarises holdings per overall status, open review items and overdue reviews", () => {
    const summary = boardSummary([
      row("breach", { open_review_items: 2 }),
      row("unknown", { review_overdue: true }),
      row("unknown", { open_review_items: 1 }),
      row("no_kpis"),
    ]);
    expect(summary).toEqual({
      holdings: 4,
      byOverall: { breach: 1, unknown: 2, pass: 0, no_kpis: 1 },
      openReviewItems: 3,
      overdue: 1,
    });
  });

  it("labels a never-judged KPI as unknown, never as pass or breach", () => {
    const never: BoardKpi = { kpi, status: "unknown", verdict: null };
    expect(kpiCellLabel(never)).toBe("Revenue growth YoY, target >= 5 %: Unknown, never judged");
    expect(observedVsTarget(never)).toEqual({ observed: "—", target: ">= 5 %" });
    expect(NEVER_JUDGED).toMatch(/never judged/i);
  });

  it("shows a judged KPI against the threshold it was judged under", () => {
    const cell: BoardKpi = { kpi, status: "breach", verdict };
    expect(kpiCellLabel(cell)).toBe("Revenue growth YoY, target >= 5 %: Breach");
    expect(observedVsTarget(cell)).toEqual({ observed: "3.21%", target: ">= 4 %" });
    expect(kpiCellLabel({ ...cell, verdict: { ...verdict, stale: true } })).toMatch(/Breach \(stale/);
  });

  it("marks adjusted KPIs and makes safe panel ids", () => {
    expect(kpiLabel({ ...kpi, basis: "adjusted" })).toBe("Revenue growth YoY (adjusted)");
    expect(kpiPanelId("BRK.B_US_EQ", "max single")).toBe("verdict-BRK-B_US_EQ-max-single");
  });

  it("counts judged KPIs' verdict confidence, leaving never-judged KPIs uncounted", () => {
    const neverJudged: BoardKpi = { kpi, status: "unknown", verdict: null };
    const judged: BoardKpi = { kpi, status: "breach", verdict };
    const disputedJudged: BoardKpi = { kpi, status: "pass", verdict: { ...verdict, confidence: "disputed" } };
    expect(confidenceCounts({ kpis: [] })).toEqual({ verified: 0, corroborated: 0, unconfirmed: 0, disputed: 0 });
    expect(confidenceCounts({ kpis: [neverJudged, judged, disputedJudged] })).toEqual({
      verified: 1,
      corroborated: 0,
      unconfirmed: 0,
      disputed: 1,
    });
  });
});
