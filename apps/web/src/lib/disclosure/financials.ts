import type { components } from "@/lib/api/schema";
import type { RevisionLinePoint, TreemapItem, WaterfallStep } from "@/components/charts";

/*
 * Financial statements (plan P2): types and pure logic for the as-filed view. The same rules as
 * the backend's `disclosure/financials.py`, so the sample fixtures and live endpoints agree:
 * a value is visible only if its filing was known by the cutoff, a restatement shows the first
 * value beside the latest, a missing value is never zero, a failed check blocks, and segment axes
 * are bridged one at a time and never added together.
 */

type S = components["schemas"];
export type StatementsOut = S["StatementsOut"];
export type HistoryPoint = S["HistoryPointOut"];
export type DerivedOut = S["DerivedOut"];
export type CheckOut = S["CheckOut"];
export type SegmentBridge = S["SegmentBridgeOut"];
export type BridgeAxis = S["BridgeAxisOut"];

export type FiledFact = {
  metric: string;
  /** ISO date, the period end. Annual periods only in the sample. */
  period_end: string;
  value: string | null;
  known_at: string;
  accession: string;
};

export const METRIC_LABEL: Record<string, string> = {
  revenue: "Revenue",
  cost_of_revenue: "Cost of revenue",
  gross_profit: "Gross profit",
  operating_income: "Operating income",
  net_income: "Net income",
  operating_cash_flow: "Operating cash flow",
  capex: "Capital expenditure",
  free_cash_flow: "Free cash flow",
  cash: "Cash and equivalents",
  total_debt: "Total debt",
};

const ms = (iso: string) => Date.parse(iso);

/** Latest value known by the cutoff per (metric, period), with the first value when restated. */
export function historyAt(facts: readonly FiledFact[], cutoffMs: number): HistoryPoint[] {
  const groups = new Map<string, FiledFact[]>();
  for (const f of facts) {
    if (ms(f.known_at) > cutoffMs) continue;
    const k = `${f.metric}|${f.period_end}`;
    groups.set(k, [...(groups.get(k) ?? []), f]);
  }
  const out: HistoryPoint[] = [];
  for (const rows of groups.values()) {
    rows.sort((a, b) => ms(a.known_at) - ms(b.known_at));
    const first = rows[0];
    const last = rows[rows.length - 1];
    const changed = rows.length > 1 && first.value !== last.value;
    out.push({
      metric: last.metric,
      period_end: last.period_end,
      value: last.value,
      accession: last.accession,
      known_at: last.known_at,
      restated: changed,
      original_value: changed ? first.value : null,
    });
  }
  return out.sort((a, b) => a.metric.localeCompare(b.metric) || a.period_end.localeCompare(b.period_end));
}

const num = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export const periodLabel = (end: string) => `FY${end.slice(0, 4)}`;

/** One small-multiple series: latest as a line, first-on-record dashed, a mark where restated. */
export function seriesFor(history: readonly HistoryPoint[], metric: string): RevisionLinePoint[] {
  return history
    .filter((p) => p.metric === metric)
    .map((p) => ({
      period: periodLabel(p.period_end),
      first: p.restated ? num(p.original_value) : num(p.value),
      latest: num(p.value),
      revised: p.restated,
    }));
}

/** Metrics that have at least one point, in display order. */
export function metricsPresent(history: readonly HistoryPoint[], order: readonly string[]): string[] {
  const have = new Set(history.map((p) => p.metric));
  return order.filter((m) => have.has(m));
}

function valueOf(history: readonly HistoryPoint[], metric: string, end: string): number | null {
  const p = history.find((h) => h.metric === metric && h.period_end === end);
  return p ? num(p.value) : null;
}

/** Operating cash flow, less capex, to free cash flow. Null when a piece is missing or capex is negative. */
export function cashWaterfall(history: readonly HistoryPoint[], end: string): WaterfallStep[] | null {
  const op = valueOf(history, "operating_cash_flow", end);
  const capex = valueOf(history, "capex", end);
  if (op == null || capex == null || capex < 0) return null;
  return [
    { id: "op", label: "Operating cash flow", value: op, kind: "total" },
    { id: "capex", label: "Capex", value: -capex, kind: "delta" },
    { id: "fcf", label: "Free cash flow", value: op - capex, kind: "total", derived: true },
  ];
}

/** Treemap tiles for one axis: members only. Eliminations and the unallocated remainder stay in the table. */
export function bridgeTiles(axis: BridgeAxis): TreemapItem[] {
  return axis.members
    .filter((m) => !m.eliminations && Number(m.value) > 0)
    .map((m) => ({ id: `${axis.axis}:${m.member}`, label: m.member, weight: Number(m.value), change: null }));
}

/** Axis names as the filing writes them ("ProductOrServiceAxis") in words. */
export function axisLabel(axis: string): string {
  return axis
    .replace(/Axis$/, "")
    .replace(/^Statement/, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();
}

export type PeriodGate = { end: string; usable: boolean; failing: CheckOut[] };

/** Per period: may it feed a valuation? Anything not passed blocks, and says why. */
export function gates(out: Pick<StatementsOut, "checks" | "usable_for_valuation">): PeriodGate[] {
  return Object.keys(out.usable_for_valuation)
    .sort()
    .map((end) => ({
      end,
      usable: out.usable_for_valuation[end],
      failing: (out.checks[end] ?? []).filter((c) => c.status !== "pass"),
    }));
}

export type MaturityBucket = { label: string; value: number };

/** Debt maturity buckets summed against the reported total, net of discount. */
export function maturityCheck(buckets: readonly MaturityBucket[], total: number | null, discount = 0, tol = 0.005, unit = 1) {
  if (total == null || buckets.length === 0) return { status: "needs_review" as const, sum: null };
  // As in `check_debt_maturities`: a negative bucket needs a person, and rounding to the reported unit is allowed.
  if (buckets.some((b) => b.value < 0)) return { status: "needs_review" as const, sum: null };
  const sum = buckets.reduce((a, b) => a + b.value, 0) - discount;
  const ok = Math.abs(sum - total) <= Math.max(Math.max(Math.abs(total), Math.abs(sum)) * tol, unit);
  return { status: ok ? ("pass" as const) : ("fail" as const), sum };
}

export const compactMoney = (n: number) =>
  new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 2 }).format(n);
