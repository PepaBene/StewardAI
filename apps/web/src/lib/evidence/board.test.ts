import { describe, expect, it } from "vitest";

import type { BoardKpi, BoardRow, ThesisKPI, VerdictOut } from "@/lib/api/client";

import {
  boardSummary,
  confidenceCounts,
  countsText,
  groupByState,
  judgedMeta,
  kpiCellLabel,
  kpiHeadroom,
  kpiLabel,
  kpiMeter,
  kpiPanelId,
  latestRationale,
  NEVER_JUDGED,
  observedVsTarget,
  OVERALL_STATUS,
  scoreChange,
  shortTicker,
  stateCounts,
  statusCounts,
  THESIS_STATE,
  THESIS_STATE_ORDER,
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

describe("scored board", () => {
  it("counts and groups rows by state, keeping the API's order", () => {
    const rows = [
      row("breach", { ticker: "A", state: "at_risk" }),
      row("pass", { ticker: "B", state: "intact" }),
      row("unknown", { ticker: "C", state: "intact" }),
      row("no_kpis", { ticker: "D", state: "unjudged" }),
    ];
    expect(stateCounts(rows)).toEqual({ intact: 2, watch: 0, at_risk: 1, unjudged: 1 });
    const groups = groupByState(rows);
    expect(groups.intact.map((r) => r.ticker)).toEqual(["B", "C"]);
    expect(groups.watch).toEqual([]);
    expect(THESIS_STATE_ORDER).toEqual(["intact", "watch", "at_risk", "unjudged"]);
    expect(THESIS_STATE.at_risk.label).toBe("At risk");
  });

  it("shortens Trading212 tickers and leaves others alone", () => {
    expect(shortTicker("NVDA_US_EQ")).toBe("NVDA");
    expect(shortTicker("ASMLa_EQ")).toBe("ASML");
    expect(shortTicker("NOVOb_EQ")).toBe("NOVO");
    expect(shortTicker("CEZ_EQ")).toBe("CEZ");
    expect(shortTicker("BRK.B_US_EQ")).toBe("BRK.B");
    expect(shortTicker("AAPL")).toBe("AAPL");
  });

  it("puts the threshold at half the meter, for floors and ceilings", () => {
    expect(kpiHeadroom(">=", 30, 30)).toBe(0.5);
    expect(kpiHeadroom(">=", 45, 30)).toBe(0.75);
    expect(kpiHeadroom(">=", 90, 30)).toBe(1);
    expect(kpiHeadroom(">", 12, 15)).toBeCloseTo(0.4);
    expect(kpiHeadroom("<=", 41, 30)).toBeCloseTo(0.3167, 3);
    expect(kpiHeadroom("<", 0, 25)).toBe(1);
    expect(kpiHeadroom("==", 10, 10)).toBe(1);
    expect(kpiHeadroom(">=", 1, 0)).toBe(1);
    expect(kpiHeadroom(">=", null, 30)).toBeNull();
    expect(kpiHeadroom("~", 1, 1)).toBeNull();
  });

  it("describes a KPI meter in words, and a never-judged KPI as empty", () => {
    const judged = kpiMeter({ kpi, status: "breach", verdict });
    expect(judged.breach).toBe(true);
    expect(judged.fill).toBeCloseTo(0.4017, 3);
    expect(judged.label).toBe("Revenue growth YoY: observed 3.21% against >= 4 % (Breach)");
    const never = kpiMeter({ kpi, status: "unknown", verdict: null });
    expect(never).toEqual({ fill: null, breach: false, label: "Revenue growth YoY: never judged, target >= 5 % (Unknown)" });
  });

  it("summarises a card by its latest verdict's rationale, breaches first on a tie", () => {
    const pass = { ...verdict, id: 8, status: "pass" as const, rationale: "Fine.", created_at: "2026-09-20T10:00:00Z" };
    const older = { ...verdict, id: 9, rationale: "Old.", created_at: "2026-08-01T10:00:00Z", fact_ids: [12, 13] };
    const cells: BoardKpi[] = [
      { kpi, status: "pass", verdict: pass },
      { kpi, status: "breach", verdict },
      { kpi, status: "breach", verdict: older },
      { kpi, status: "unknown", verdict: null },
    ];
    expect(latestRationale({ kpis: cells })).toBe("Revenue grew 3.2%, below 4%.");
    expect(latestRationale({ kpis: [{ kpi, status: "unknown", verdict: null }] })).toBeNull();
    expect(judgedMeta({ kpis: cells })).toEqual({ lastJudged: "2026-09-20T10:00:00Z", factCount: 3 });
    expect(judgedMeta({ kpis: [] })).toEqual({ lastJudged: null, factCount: 0 });
  });

  it("measures the portfolio score against the earliest point of its history", () => {
    expect(scoreChange(86, [{ date: "2025-01-24", score: 50 }, { date: "2026-09-27", score: 86 }])).toEqual({
      delta: 36,
      since: "2025-01-24",
    });
    expect(scoreChange(null, [{ date: "2025-01-24", score: 50 }])).toBeNull();
    expect(scoreChange(80, [])).toBeNull();
  });
});
