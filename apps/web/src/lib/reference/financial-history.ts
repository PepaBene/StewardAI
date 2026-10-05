/**
 * The financial history card's view model. `/holdings/{ticker}/financial-history` returns the
 * stored, as-filed statement facts (untyped in OpenAPI), newest period first. The card shows
 * one small series per line item: annual periods only, one value per period (the latest
 * known revision), and the worst reconciliation state across the series.
 */
import { toNumber } from "./peers";

export type StatementKind = "income" | "cash_flow" | "balance_sheet";

export const STATEMENTS: readonly { id: StatementKind; label: string }[] = [
  { id: "income", label: "Income statement" },
  { id: "cash_flow", label: "Cash flow" },
  { id: "balance_sheet", label: "Balance sheet" },
];

export type Reconciliation = "reconciled" | "unchecked" | "failed";

export type FactPoint = { periodEnd: string; value: number; reconciliation: Reconciliation; revision: number | null };

export type LineSeries = {
  statement: StatementKind;
  key: string;
  label: string;
  currency: string | null;
  basis: string | null;
  /** Oldest to newest. */
  points: FactPoint[];
  /** Worst reconciliation state among the points: failed > unchecked > reconciled. */
  reconciliation: Reconciliation;
  /** More than one revision exists for at least one period (a restatement). */
  restated: boolean;
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

const DAY = 86_400_000;

/** Full-year duration, or an instant (balance sheet: no start date). Quarters are excluded. */
function isAnnual(start: string | null, end: string): boolean {
  if (!start) return true;
  const days = (Date.parse(end) - Date.parse(start)) / DAY;
  return days >= 330 && days <= 380;
}

function worse(a: Reconciliation, b: Reconciliation): Reconciliation {
  const order: Reconciliation[] = ["failed", "unchecked", "reconciled"];
  return order.indexOf(a) <= order.indexOf(b) ? a : b;
}

function toReconciliation(v: unknown): Reconciliation {
  return v === "reconciled" || v === "failed" ? v : "unchecked";
}

export function parseFinancialHistory(payload: unknown, maxPeriods = 8): LineSeries[] {
  if (!Array.isArray(payload)) return [];
  type Acc = { series: LineSeries; byPeriod: Map<string, { point: FactPoint; known: string; count: number }> };
  const groups = new Map<string, Acc>();

  for (const raw of payload) {
    if (!isRecord(raw)) continue;
    const statement = raw.statement;
    if (statement !== "income" && statement !== "cash_flow" && statement !== "balance_sheet") continue;
    // Dimensional facts (segments, products) are not the consolidated line.
    if (isRecord(raw.dimensions) && Object.keys(raw.dimensions).length > 0) continue;
    const value = toNumber(raw.value);
    const periodEnd = str(raw.period_end);
    if (value === null || !periodEnd || !isAnnual(str(raw.period_start), periodEnd)) continue;

    const key = str(raw.line_item) ?? str(raw.concept) ?? "unknown";
    const groupKey = `${statement}\u0000${key}\u0000${str(raw.basis) ?? ""}\u0000${str(raw.currency) ?? ""}`;
    let acc = groups.get(groupKey);
    if (!acc) {
      acc = {
        series: {
          statement,
          key,
          label: str(raw.label) ?? key.replaceAll("_", " "),
          currency: str(raw.currency) ?? str(raw.unit),
          basis: str(raw.basis),
          points: [],
          reconciliation: "reconciled",
          restated: false,
        },
        byPeriod: new Map(),
      };
      groups.set(groupKey, acc);
    }
    const known = str(raw.known_at) ?? "";
    const point: FactPoint = {
      periodEnd,
      value,
      reconciliation: toReconciliation(raw.reconciliation),
      revision: toNumber(raw.revision),
    };
    const prior = acc.byPeriod.get(periodEnd);
    if (!prior) acc.byPeriod.set(periodEnd, { point, known, count: 1 });
    else {
      prior.count += 1;
      if (known > prior.known) Object.assign(prior, { point, known });
    }
  }

  const out: LineSeries[] = [];
  for (const { series, byPeriod } of groups.values()) {
    const entries = [...byPeriod.values()].sort((a, b) => a.point.periodEnd.localeCompare(b.point.periodEnd)).slice(-maxPeriods);
    if (entries.length === 0) continue;
    series.points = entries.map((e) => e.point);
    series.restated = entries.some((e) => e.count > 1);
    series.reconciliation = series.points.reduce<Reconciliation>((w, p) => worse(w, p.reconciliation), "reconciled");
    out.push(series);
  }
  return out.sort((a, b) => a.statement.localeCompare(b.statement) || a.label.localeCompare(b.label));
}

/** Latest period's change against the one before it, null with fewer than two periods or a zero base. */
export function latestChange(series: LineSeries): number | null {
  const n = series.points.length;
  if (n < 2) return null;
  const prev = series.points[n - 2].value;
  if (prev === 0) return null;
  return (series.points[n - 1].value - prev) / Math.abs(prev);
}

export const RECONCILIATION_LABEL: Record<Reconciliation, string> = {
  reconciled: "Matches the filed statement",
  unchecked: "Not yet checked against the filing",
  failed: "Failed the check against the filing",
};
