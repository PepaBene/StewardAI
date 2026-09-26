import type { BoardKpi, BoardRow, VerdictStatus } from "@/lib/api/client";

import { formatObserved, formatTarget, humanizeMetric, VERDICT_STATUS } from "./confidence";

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
