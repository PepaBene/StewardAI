import { isFiniteNumber } from "@/lib/charts/scale";
import { DATA_STATES, type DataState } from "@/lib/data-state/states";

import {
  PROVIDER_LABELS,
  type BudgetUsage,
  type CoverageCell,
  type CoverageRow,
  type EntityScope,
  type ProviderId,
} from "./types";

export type ScopeFilter = EntityScope | "all";
export type StateFilter = DataState | "all";

/** The cell(s) of a row that a scope filter selects. */
export function cellsFor(row: CoverageRow, scope: ScopeFilter): readonly CoverageCell[] {
  return scope === "held" ? [row.held] : scope === "watchlist" ? [row.watchlist] : [row.held, row.watchlist];
}

/** Rows whose selected cells include the state (or all rows), optionally limited to one provider. */
export function filterRows(
  rows: readonly CoverageRow[],
  { scope = "all", state = "all", provider = "all" }: { scope?: ScopeFilter; state?: StateFilter; provider?: ProviderId | "all" },
): CoverageRow[] {
  return rows.filter((r) => {
    if (provider !== "all" && r.provider !== provider) return false;
    if (state === "all") return true;
    return cellsFor(r, scope).some((c) => c.state === state);
  });
}

/** Cells per state across the selected scope, in the canonical state order, zeros included. */
export function stateCounts(rows: readonly CoverageRow[], scope: ScopeFilter = "all"): Record<DataState, number> {
  const counts = Object.fromEntries(DATA_STATES.map((s) => [s, 0])) as Record<DataState, number>;
  for (const r of rows) for (const c of cellsFor(r, scope)) counts[c.state] += 1;
  return counts;
}

/** Rows grouped by provider in the canonical provider order, preserving row order within each. */
export function groupByProvider(rows: readonly CoverageRow[]): { provider: ProviderId; label: string; rows: CoverageRow[] }[] {
  const order = Object.keys(PROVIDER_LABELS) as ProviderId[];
  const map = new Map<ProviderId, CoverageRow[]>();
  for (const r of rows) map.set(r.provider, [...(map.get(r.provider) ?? []), r]);
  return order.filter((p) => map.has(p)).map((p) => ({ provider: p, label: PROVIDER_LABELS[p], rows: map.get(p)! }));
}

/** Entities still unresolved in the cell, as a non-negative count. */
export function missingCount(cell: CoverageCell): number {
  return Math.max(0, cell.total - cell.covered);
}

// ---------------------------------------------------------------------------------------
// Request budget
// ---------------------------------------------------------------------------------------

export type BudgetTone = "ok" | "reserve" | "exhausted" | "uncapped";

/** Spend as a fraction of the cap; null when the provider publishes none. */
export function usedFraction(b: Pick<BudgetUsage, "limit" | "used">): number | null {
  if (!isFiniteNumber(b.limit) || b.limit <= 0) return null;
  return Math.max(0, b.used / b.limit);
}

/**
 * Where a budget stands: "exhausted" once spend reaches the cap, "reserve" once only the
 * retry/urgent reserve is left, "uncapped" when there is no published cap (never read as
 * unlimited), otherwise "ok".
 */
export function budgetTone(b: Pick<BudgetUsage, "limit" | "used" | "reserve">): BudgetTone {
  if (!isFiniteNumber(b.limit) || b.limit <= 0) return "uncapped";
  if (b.used >= b.limit) return "exhausted";
  if (b.used >= b.limit - Math.max(0, b.reserve)) return "reserve";
  return "ok";
}

/** Credits still spendable on routine demand: cap − reserve − used, never negative. */
export function routineHeadroom(b: Pick<BudgetUsage, "limit" | "used" | "reserve">): number | null {
  if (!isFiniteNumber(b.limit)) return null;
  return Math.max(0, b.limit - Math.max(0, b.reserve) - b.used);
}

/** True when any provider ledger shows an overage event or non-zero spend (a release blocker). */
export function hasOverage(budgets: readonly BudgetUsage[], spendUsd: number): boolean {
  return spendUsd > 0 || budgets.some((b) => b.overageEvents > 0);
}

export const PERIOD_NOUN = { minute: "minute", day: "day", week: "week", month: "month" } as const;

/** "7 / 25 calls per day" or "No published cap". */
export function describeBudget(b: BudgetUsage): string {
  if (!isFiniteNumber(b.limit)) return `${b.used} ${b.unit} used this ${PERIOD_NOUN[b.period]}, no published cap`;
  return `${b.used} / ${b.limit} ${b.unit} per ${PERIOD_NOUN[b.period]}`;
}

/** Where the failed-endpoint drill-through goes: the failed run, else the job-log filter, else null. */
export function drillThroughHref(row: CoverageRow): string | null {
  if (row.failedJobId !== null) return `/jobs/${row.failedJobId}`;
  if (row.jobType) return `/activity?type=${encodeURIComponent(row.jobType)}#job-log`;
  return null;
}
