import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type DiscoveryResult = Schemas["DiscoveryResult"];
export type ResultRow = Schemas["ResultRow"];
export type MetricValue = Schemas["MetricValue"];
export type MetricInfo = Schemas["MetricInfo"];
export type CatalogueResponse = Schemas["CatalogueResponse"];
export type StockRequest = Schemas["StockRequest"];
export type FundRequest = Schemas["FundRequest"];
export type ApiFilter = Schemas["DiscoverFilter"];
export type Baseline = Schemas["Baseline"];
export type ScreenSummary = Schemas["ScreenSummary"];
export type ColumnSet = Schemas["ColumnSet"];

export type Kind = "stock" | "fund";
export type UniverseId = StockRequest["universe"];

export const MAX_FILTERS = 12;

export const UNIVERSES: readonly { id: UniverseId; label: string; hint: string }[] = [
  { id: "covered", label: "Covered universe", hint: "Every instrument the app has identity for, with whatever data sits behind it" },
  { id: "watchlist", label: "Watchlist", hint: "Covered instruments on your watchlist" },
  { id: "holdings", label: "Holdings", hint: "Covered instruments in your latest portfolio snapshot" },
  { id: "snapshot", label: "Frozen universe", hint: "Membership stored on a past date, delisted names included" },
];

export const DEFAULT_STOCK_COLUMNS = ["market_cap", "return_1y", "gross_margin", "roic", "earnings_yield", "qvm_score", "data_coverage"] as const;
export const DEFAULT_FUND_COLUMNS = ["asset_class", "expense_ratio", "total_assets", "top10_weight", "effective_holdings", "lookthrough_coverage", "holdings_age_days"] as const;

export const GROUP_LABEL: Record<string, string> = {
  identity: "Identity",
  size: "Size",
  liquidity: "Liquidity",
  income: "Income",
  performance: "Performance",
  risk: "Risk",
  financials: "Filed financials",
  value: "Value",
  quality: "Quality",
  momentum: "Momentum",
  red_flag: "Red flags",
  signal: "Signals",
  estimates: "Estimates",
  composite: "Composite",
  coverage: "Coverage",
  costs: "Costs",
  freshness: "Freshness",
  holdings: "Holdings",
  concentration: "Concentration",
};

export type FilterDraft = { id: number; metric: string; op: "gte" | "lte" | "in"; value: string };

export type BuiltFilters = { filters: ApiFilter[]; errors: string[] };

/** Fractions are typed as percentages and sent as fractions; multiples, money and counts as typed. */
export function typedToApi(value: number, unit: string): number {
  return unit === "fraction" ? Math.round(value * 1e6) / 1e8 : value;
}

export function apiToTyped(value: number, unit: string): number {
  return unit === "fraction" ? Math.round(value * 1e8) / 1e6 : value;
}

export function unitSuffix(unit: string): string {
  if (unit === "fraction") return "%";
  if (unit === "multiple") return "×";
  if (unit === "years") return "yrs";
  return "";
}

export function buildFilters(drafts: readonly FilterDraft[], catalogue: readonly MetricInfo[]): BuiltFilters {
  const errors: string[] = [];
  const filters: ApiFilter[] = [];
  const seen = new Set<string>();
  for (const d of drafts) {
    const info = catalogue.find((m) => m.id === d.metric);
    if (!info) {
      errors.push(`${d.metric}: unknown metric.`);
      continue;
    }
    if (!info.enabled) {
      errors.push(`${info.label}: ${info.disabled_reason ?? "filter disabled"}`);
      continue;
    }
    const key = `${d.metric}|${d.op}`;
    if (seen.has(key)) {
      errors.push(`${info.label}: only one "${OP_LABEL[d.op]}" condition per metric.`);
      continue;
    }
    seen.add(key);
    if (info.kind === "category") {
      const values = d.value.split(",").map((v) => v.trim()).filter(Boolean);
      if (values.length === 0) {
        errors.push(`${info.label}: enter at least one value, separated by commas.`);
        continue;
      }
      filters.push({ metric: d.metric, op: "in", value: values, definition_version: info.definition_version, period: info.period });
      continue;
    }
    const typed = d.value.trim().replace(",", ".");
    const n = typed === "" ? NaN : Number(typed);
    if (!Number.isFinite(n)) {
      errors.push(`${info.label}: enter a number.`);
      continue;
    }
    filters.push({
      metric: d.metric,
      op: d.op === "in" ? "gte" : d.op,
      value: String(typedToApi(n, info.unit)),
      definition_version: info.definition_version,
      period: info.period,
    } as ApiFilter);
  }
  return { filters, errors };
}

export const OP_LABEL: Record<FilterDraft["op"], string> = { gte: "at least", lte: "at most", in: "is one of" };

export function describeFilter(f: ApiFilter, catalogue: readonly MetricInfo[]): string {
  const info = catalogue.find((m) => m.id === f.metric);
  const label = info?.label ?? f.metric;
  if (f.op === "in") return `${label} is ${(f.value as string[]).join(" or ")}`;
  const n = Number(f.value);
  return `${label} ${OP_LABEL[f.op]} ${formatNumber(apiToTyped(n, info?.unit ?? ""), info?.unit ?? "")}`;
}

function compact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toFixed(2);
}

/** A typed value (percent already scaled) in its unit. */
export function formatNumber(n: number, unit: string): string {
  if (unit === "fraction") return `${n.toFixed(1)}%`;
  if (unit === "multiple") return `${n.toFixed(2)}×`;
  if (unit === "years") return `${n.toFixed(1)} yrs`;
  if (unit === "money") return compact(n);
  if (unit === "count") return n.toFixed(0);
  return n.toFixed(2);
}

/** One metric cell: text for a known value, or `null` for unknown (never "0"). */
export function formatMetric(v: MetricValue | undefined, unit: string): string | null {
  if (!v || v.value === null || v.value === undefined) return null;
  if (typeof v.value === "string" && Number.isNaN(Number(v.value))) return v.value;
  const n = Number(v.value);
  if (!Number.isFinite(n)) return String(v.value);
  return formatNumber(unit === "fraction" ? n * 100 : n, unit);
}

export type FunnelStep = { id: string; label: string; value: number };

/** Where every member of the universe ended up; the four counts sum to `universe_size`. */
export function funnel(r: DiscoveryResult): FunnelStep[] {
  return [
    { id: "matched", label: "Matched", value: r.matched },
    { id: "failed", label: "Failed a filter", value: r.failed_filter },
    { id: "unknown", label: "Missing a filtered metric", value: r.unknown_excluded },
    { id: "no_data", label: "No data to test", value: r.no_data },
  ];
}

export function funnelBalances(r: DiscoveryResult): boolean {
  return funnel(r).reduce((s, x) => s + x.value, 0) === r.universe_size;
}

export function coverageShare(known: number, total: number): number | null {
  return total > 0 ? known / total : null;
}

/** Metrics grouped for the picker, groups in catalogue order. */
export function groupMetrics(catalogue: readonly MetricInfo[]): { group: string; label: string; metrics: MetricInfo[] }[] {
  const out: { group: string; label: string; metrics: MetricInfo[] }[] = [];
  for (const m of catalogue) {
    let g = out.find((x) => x.group === m.group);
    if (!g) {
      g = { group: m.group, label: GROUP_LABEL[m.group] ?? m.group, metrics: [] };
      out.push(g);
    }
    g.metrics.push(m);
  }
  return out;
}

export function nextMetric(drafts: readonly FilterDraft[], catalogue: readonly MetricInfo[]): string {
  const used = new Set(drafts.map((d) => d.metric));
  return (catalogue.find((m) => m.enabled && !used.has(m.id)) ?? catalogue.find((m) => m.enabled) ?? catalogue[0])?.id ?? "";
}

export function defaultOp(info: MetricInfo | undefined): FilterDraft["op"] {
  return info?.kind === "category" ? "in" : "gte";
}

export const IDEA_PIPELINE_NOTE =
  "Top third on the quality-value-momentum score, no red flag, and for a cash-burning company a 2-year runway with at most 10% dilution a year.";

export const MAX_COMPARE = 4;
