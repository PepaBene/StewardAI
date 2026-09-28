import type { HoldingSegments, SegmentHistoryPoint, SegmentRow } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";

/*
 * Revenue by region / segment / product (`GET /holdings/{ticker}/segments`). Categories are
 * the company's own ("as reported"): nothing here maps them onto a common taxonomy. Rows the
 * backend flags as subtotals (the sum of other rows, e.g. Novo Nordisk's "International
 * Operations") are listed but never drawn, so nothing is counted twice.
 */

export type SegmentAxis = "geographic" | "segment" | "product";
export const SEGMENT_AXES: readonly SegmentAxis[] = ["geographic", "segment", "product"];
export const AXIS_LABELS: Record<SegmentAxis, string> = {
  geographic: "Region",
  segment: "Segment",
  product: "Product",
};

export const AS_REPORTED_NOTE =
  "Categories as the company reports them; regions of different companies do not line up.";

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The breakdowns that have rows, in display order. */
export function availableAxes(data: HoldingSegments | null): SegmentAxis[] {
  if (!data) return [];
  return SEGMENT_AXES.filter((a) => data[a].length > 0);
}

/** 60_846_000_000 USD -> "USD 60.8bn"; 7_026_000_000 -> "USD 7.0bn"; 4_700_000 -> "USD 4.7m". */
export function formatRevenue(value: number | string | null | undefined, currency: string | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const abs = Math.abs(n);
  const unit = currency ? `${currency} ` : "";
  if (abs >= 1e9) return `${unit}${formatNumber(n / 1e9, abs >= 1e11 ? 0 : 1)}bn`;
  if (abs >= 1e6) return `${unit}${formatNumber(n / 1e6, 1)}m`;
  return `${unit}${formatNumber(n, 0)}`;
}

/** Rows drawn in a chart: subtotals left out, largest first, positive values only. */
export function chartRows(rows: readonly SegmentRow[]): { label: string; value: number }[] {
  return rows
    .filter((r) => !r.subtotal)
    .map((r) => ({ label: r.label, value: toNumber(r.value) ?? 0 }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value);
}

/** "FY2025 · Jan–Dec 2025" style caption from the backend's period. */
export function periodCaption(data: HoldingSegments, formatDate: (d: string) => string): string | null {
  const p = data.period;
  if (!p) return null;
  const span = `${formatDate(p.start)} – ${formatDate(p.end)}`;
  return p.basis === "annual" ? `${p.label} (${span})` : `${p.months} months, ${span}`;
}

/** Provenance line: "SEC EDGAR XBRL (official filing) · 10-Q filed 24 Jul 2026" / "Entered manually · …". */
export function sourceCaption(data: HoldingSegments, formatDate: (d: string) => string): string | null {
  const s = data.source;
  if (!s) return null;
  if (s.kind === "manual") return `Entered manually${s.note ? ` · ${s.note}` : ""}`;
  const filed = s.filed ? ` filed ${formatDate(s.filed)}` : "";
  return `${s.tier}${s.form ? ` · ${s.form}${filed}` : ""}`;
}

export type RegionColumn = { label: string; total: number; parts: { name: string; value: number }[] };

/** The remainder column of a figure, never a company's own label. */
export const OTHER_REGIONS = "Other regions (grouped)";

/**
 * Stacked columns for a "sales by region" figure: one column per stored period (oldest
 * first), the `keep - 1` largest regions of the latest period by name and the rest summed as
 * OTHER_REGIONS. Values stay in the reporting currency.
 */
export function regionColumns(
  history: readonly SegmentHistoryPoint[],
  keep = 4,
): { names: string[]; columns: RegionColumn[] } {
  if (history.length === 0) return { names: [], columns: [] };
  const latest = chartRows(history[history.length - 1].rows);
  const named = latest.length <= keep ? latest.map((r) => r.label) : latest.slice(0, keep - 1).map((r) => r.label);
  const hasRest = history.some((h) => chartRows(h.rows).some((r) => !named.includes(r.label)));
  const names = hasRest ? [...named, OTHER_REGIONS] : named;
  const columns = history.map((h) => {
    const rows = chartRows(h.rows);
    const parts = named.map((name) => ({ name, value: rows.find((r) => r.label === name)?.value ?? 0 }));
    if (hasRest) {
      parts.push({
        name: OTHER_REGIONS,
        value: rows.filter((r) => !named.includes(r.label)).reduce((s, r) => s + r.value, 0),
      });
    }
    return { label: h.period.label, total: parts.reduce((s, p) => s + p.value, 0), parts };
  });
  return { names, columns };
}

export type SalesRegionFigure = {
  /** Unit of the values shown, e.g. "USD bn". */
  unitLabel: string;
  /** Divide values by this for display (1e9 for bn). */
  divisor: number;
  names: string[];
  columns: RegionColumn[];
  source: string | null;
  basis: HoldingSegments["basis"];
};

/**
 * The research report's "Sales by region" figure: stacked columns of the stored geographic
 * history (fiscal years, or quarters when only those are stored). Null without geographic data.
 */
export function salesRegionFigure(
  data: HoldingSegments | null,
  formatDate: (d: string) => string,
  keep = 4,
): SalesRegionFigure | null {
  const history = data?.history?.geographic ?? [];
  if (!data || history.length === 0) return null;
  const { names, columns } = regionColumns(history, keep);
  if (columns.every((c) => c.total <= 0)) return null;
  const max = Math.max(...columns.map((c) => c.total));
  const [divisor, suffix] = max >= 1e9 ? [1e9, "bn"] : max >= 1e6 ? [1e6, "m"] : [1, ""];
  return {
    unitLabel: [data.currency ?? "", suffix].filter(Boolean).join(" "),
    divisor,
    names,
    columns,
    source: sourceCaption(data, formatDate),
    basis: data.basis,
  };
}
