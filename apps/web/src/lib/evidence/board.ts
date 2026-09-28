import type { BoardKpi, BoardRow, Confidence, VerdictStatus } from "@/lib/api/client";

import { formatObserved, formatTarget, humanizeMetric, tallyConfidence, VERDICT_STATUS } from "./confidence";

/*
 * The thesis board: one row per holding with a thesis, one cell per KPI. A KPI that was
 * never judged is "unknown", counted and shown, never left out.
 */

export type OverallStatus = BoardRow["overall"];

/** The row's overall status: the verdict labels, plus "No KPIs" for a thesis without any. */
export const OVERALL_STATUS = {
  breach: { label: "Breach", description: "At least one KPI breaches its threshold." },
  unknown: { label: "Unknown", description: "No KPI breaches, but at least one has no verified figure." },
  pass: { label: "Pass", description: "Every KPI passes." },
  no_kpis: { label: "No KPIs", description: "The thesis has no KPIs to judge." },
} as const satisfies Record<OverallStatus, { label: string; description: string }>;

export const NEVER_JUDGED = "Never judged — no verified figure yet";

/** The order of the counts, worst first. */
export const STATUS_ORDER = ["breach", "unknown", "pass"] as const satisfies readonly VerdictStatus[];

/** Counts per status, 0 when the API leaves one out. */
export function statusCounts(row: Pick<BoardRow, "counts">): Record<VerdictStatus, number> {
  return {
    breach: row.counts.breach ?? 0,
    unknown: row.counts.unknown ?? 0,
    pass: row.counts.pass ?? 0,
  };
}

/** "1 breach · 2 unknown · 3 pass"; each status is named, so the text reads without colour. */
export function countsText(counts: Record<VerdictStatus, number>): string {
  return STATUS_ORDER.map((s) => `${counts[s]} ${VERDICT_STATUS[s].label.toLowerCase()}`).join(" · ");
}

/** Confidence of each judged KPI's verdict on this row; a KPI never judged has none to count. */
export function confidenceCounts(row: Pick<BoardRow, "kpis">): Record<Confidence, number> {
  return tallyConfidence(
    row.kpis.map((cell) => cell.verdict?.confidence).filter((c): c is Confidence => c !== undefined),
  );
}

/** Holdings per overall status across the board, for the summary line. */
export function boardSummary(rows: Pick<BoardRow, "overall" | "open_review_items" | "review_overdue">[]) {
  const byOverall: Record<OverallStatus, number> = { breach: 0, unknown: 0, pass: 0, no_kpis: 0 };
  let openReviewItems = 0;
  let overdue = 0;
  for (const r of rows) {
    byOverall[r.overall] += 1;
    openReviewItems += r.open_review_items;
    if (r.review_overdue) overdue += 1;
  }
  return { holdings: rows.length, byOverall, openReviewItems, overdue };
}

/** A stable DOM id for a KPI cell's verdict panel. */
export function kpiPanelId(ticker: string, kpiId: string): string {
  return `verdict-${ticker}-${kpiId}`.replace(/[^A-Za-z0-9_-]/g, "-");
}

/** The KPI's short name on the board: its metric, plus the basis when it is adjusted. */
export function kpiLabel(kpi: BoardKpi["kpi"]): string {
  const name = humanizeMetric(kpi.metric);
  return kpi.basis === "adjusted" ? `${name} (adjusted)` : name;
}

/** Accessible name of a KPI cell's button, e.g. "Revenue growth YoY, target >= 5 %: Breach". */
export function kpiCellLabel(cell: BoardKpi): string {
  const target = formatTarget(cell.kpi.operator, cell.kpi.threshold, cell.kpi.unit);
  const status = cell.verdict ? VERDICT_STATUS[cell.status].label : `${VERDICT_STATUS.unknown.label}, never judged`;
  const stale = cell.verdict?.stale ? " (stale: judged under an older theses config)" : "";
  return `${kpiLabel(cell.kpi)}, target ${target}: ${status}${stale}`;
}

/**
 * "Observed 9.63% against >= 5 %" — the verdict's own threshold (the one it was judged
 * under), falling back to the KPI's when there is no verdict.
 */
export function observedVsTarget(cell: BoardKpi): { observed: string; target: string } {
  const v = cell.verdict;
  if (!v) {
    return { observed: "—", target: formatTarget(cell.kpi.operator, cell.kpi.threshold, cell.kpi.unit) };
  }
  return {
    observed: formatObserved(v.observed_value, v.threshold_unit),
    target: formatTarget(v.operator, v.threshold, v.threshold_unit),
  };
}

// ---------------------------------------------------------------------------
// Scored board (kanban): state columns, KPI headroom meters, card meta
// ---------------------------------------------------------------------------

export type ThesisState = BoardRow["state"];

/** Kanban order, best first. */
export const THESIS_STATE_ORDER = ["intact", "watch", "at_risk", "unjudged"] as const satisfies readonly ThesisState[];

/** Each state and what it means (the API's rule, in words). */
export const THESIS_STATE = {
  intact: { label: "Intact", note: "No KPI breaching" },
  watch: { label: "Watch", note: "One KPI breaching" },
  at_risk: { label: "At risk", note: "Two or more KPIs breaching" },
  unjudged: { label: "Unjudged", note: "No KPI judged yet, or no KPIs" },
} as const satisfies Record<ThesisState, { label: string; note: string }>;

/** Holdings per state, 0 for a state with none. */
export function stateCounts(rows: readonly Pick<BoardRow, "state">[]): Record<ThesisState, number> {
  const counts: Record<ThesisState, number> = { intact: 0, watch: 0, at_risk: 0, unjudged: 0 };
  for (const r of rows) counts[r.state] += 1;
  return counts;
}

/** Rows per state, in the API's order within each state (worst first). */
export function groupByState<T extends Pick<BoardRow, "state">>(rows: readonly T[]): Record<ThesisState, T[]> {
  const groups: Record<ThesisState, T[]> = { intact: [], watch: [], at_risk: [], unjudged: [] };
  for (const r of rows) groups[r.state].push(r);
  return groups;
}

/**
 * Trading212 ticker -> the short symbol for tight labels: "NVDA_US_EQ" -> "NVDA",
 * "ASMLa_EQ" -> "ASML" (the lowercase letter is T212's exchange suffix). Anything else is
 * returned unchanged.
 */
export function shortTicker(ticker: string): string {
  const m = /^([A-Z0-9.]+?)[a-z]?(?:_[A-Z]{2})?_EQ$/.exec(ticker);
  return m ? m[1] : ticker;
}

/**
 * How much room a KPI has against its threshold, as a meter fill in 0..1 with the threshold
 * at 0.5: 1 is twice as good as the threshold (or zero, for a ceiling), 0 is twice as bad.
 * ">=", ">": observed / (2 x threshold); "<=", "<": 1 - observed / (2 x threshold); "==": 1 at
 * the threshold, falling with the relative distance. A zero threshold measures the distance
 * in the KPI's unit. Null when there is no observed value.
 */
export function kpiHeadroom(operator: string, observed: number | null | undefined, threshold: number): number | null {
  if (observed === null || observed === undefined || !Number.isFinite(observed) || !Number.isFinite(threshold)) {
    return null;
  }
  const base = Math.abs(threshold) > 0 ? Math.abs(threshold) : 1;
  const rel = (observed - threshold) / base;
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  if (operator === ">=" || operator === ">") return clamp(0.5 + rel / 2);
  if (operator === "<=" || operator === "<") return clamp(0.5 - rel / 2);
  if (operator === "==") return clamp(1 - Math.abs(rel));
  return null;
}

/** A KPI cell's meter: fill (null: no figure), whether it breaches, and its accessible name. */
export function kpiMeter(cell: BoardKpi): { fill: number | null; breach: boolean; label: string } {
  const v = cell.verdict;
  const fill = v ? kpiHeadroom(v.operator, v.observed_value, v.threshold) : null;
  const { observed, target } = observedVsTarget(cell);
  const what = v ? `observed ${observed} against ${target}` : `never judged, target ${target}`;
  return { fill, breach: cell.status === "breach", label: `${kpiLabel(cell.kpi)}: ${what} (${VERDICT_STATUS[cell.status].label})` };
}

const STATUS_RANK: Record<VerdictStatus, number> = { breach: 0, pass: 1, unknown: 2 };

/**
 * The card's one-line summary: the rationale of the most recent verdict on the row (a
 * breach first among verdicts recorded together). Null when no KPI was ever judged.
 */
export function latestRationale(row: Pick<BoardRow, "kpis">): string | null {
  const judged = row.kpis
    .map((c) => c.verdict)
    .filter((v): v is NonNullable<BoardKpi["verdict"]> => v !== null && v.rationale.trim() !== "");
  if (judged.length === 0) return null;
  judged.sort((a, b) => b.created_at.localeCompare(a.created_at) || STATUS_RANK[a.status] - STATUS_RANK[b.status]);
  return judged[0].rationale.trim();
}

/** When the row was last judged and how many distinct facts its current verdicts cite. */
export function judgedMeta(row: Pick<BoardRow, "kpis">): { lastJudged: string | null; factCount: number } {
  let lastJudged: string | null = null;
  const facts = new Set<number>();
  for (const cell of row.kpis) {
    const v = cell.verdict;
    if (!v) continue;
    if (lastJudged === null || v.created_at > lastJudged) lastJudged = v.created_at;
    for (const id of v.fact_ids) facts.add(id);
  }
  return { lastJudged, factCount: facts.size };
}

/** The portfolio score's move since the earliest point of its history. Null without both. */
export function scoreChange(
  current: number | null | undefined,
  history: readonly { date: string; score: number }[] | null | undefined,
): { delta: number; since: string } | null {
  if (current === null || current === undefined || !history || history.length === 0) return null;
  return { delta: current - history[0].score, since: history[0].date };
}
