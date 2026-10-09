import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type ScreenRequest = Schemas["ScreenRequest"];
export type ScreenResult = Schemas["ScreenResult"];
export type ScreenRow = Schemas["ScreenRow"];
export type MetricId = NonNullable<ScreenRequest["sort_by"]>;
export type Universe = ScreenRequest["universe"];
export type MissingTreatment = ScreenRequest["missing"];

export const MAX_FILTERS = 6;

/** `percent` metrics are typed as a percentage and sent as a fraction; `multiple` is sent as typed. */
export const SCREEN_METRICS: readonly { id: MetricId; label: string; kind: "percent" | "multiple" }[] = [
  { id: "revenue_growth", label: "Revenue growth", kind: "percent" },
  { id: "gross_margin", label: "Gross margin", kind: "percent" },
  { id: "operating_margin", label: "Operating margin", kind: "percent" },
  { id: "roic", label: "Return on invested capital", kind: "percent" },
  { id: "free_cash_flow_conversion", label: "Free cash flow conversion", kind: "percent" },
  { id: "net_debt_to_equity", label: "Net debt to equity", kind: "multiple" },
];

export const UNIVERSES: readonly { id: Universe; label: string; hint: string }[] = [
  { id: "covered", label: "Covered issuers", hint: "Every issuer the app holds filed statements for" },
  { id: "watchlist", label: "Watchlist", hint: "Issuers on your watchlist with filed statements" },
  { id: "holdings", label: "Holdings", hint: "Issuers in your latest portfolio snapshot with filed statements" },
];

export type FilterOp = "gte" | "lte";
export const OP_LABEL: Record<FilterOp, string> = { gte: "at least", lte: "at most" };

export type FilterDraft = { id: number; metric: MetricId; op: FilterOp; value: string };

export function metricInfo(id: string) {
  return SCREEN_METRICS.find((m) => m.id === id);
}

export function unitSuffix(metric: MetricId): string {
  return metricInfo(metric)?.kind === "multiple" ? "×" : "%";
}

export type BuiltRequest = { request: ScreenRequest | null; errors: string[] };

/** Validates the drafts and converts typed percentages to the fractions the API expects. */
export function buildRequest(input: {
  universe: Universe;
  drafts: readonly FilterDraft[];
  missing: MissingTreatment;
  sortBy: MetricId | null;
  limit?: number;
  reconciledOnly?: boolean;
  valuationGateOnly?: boolean;
}): BuiltRequest {
  const errors: string[] = [];
  const seen = new Set<string>();
  const filters: components["schemas"]["ScreenFilter"][] = [];
  if (input.drafts.length > MAX_FILTERS) errors.push(`Use at most ${MAX_FILTERS} filters.`);
  for (const d of input.drafts) {
    const info = metricInfo(d.metric);
    const label = info?.label ?? d.metric;
    const typed = d.value.trim().replace(",", ".");
    const n = typed === "" ? NaN : Number(typed);
    if (!Number.isFinite(n)) {
      errors.push(`${label}: enter a number.`);
      continue;
    }
    const key = `${d.metric}|${d.op}`;
    if (seen.has(key)) {
      errors.push(`${label}: only one "${OP_LABEL[d.op]}" filter per metric.`);
      continue;
    }
    seen.add(key);
    // Rounded to avoid 0.15000000000000002 from percent conversion.
    const value = info?.kind === "percent" ? Math.round(n * 1e6) / 1e8 : n;
    filters.push({ metric: d.metric, op: d.op, value: String(value) } as components["schemas"]["ScreenFilter"]);
  }
  if (errors.length > 0) return { request: null, errors };
  return {
    request: {
      universe: input.universe,
      filters,
      missing: input.missing,
      sort_by: input.sortBy,
      limit: input.limit ?? 100,
      reconciled_only: input.reconciledOnly ?? false,
      valuation_gate_only: input.valuationGateOnly ?? false,
    },
    errors: [],
  };
}

/** First metric not yet used by a draft, so "Add filter" never starts with a duplicate. */
export function nextMetric(drafts: readonly FilterDraft[]): MetricId {
  const used = new Set(drafts.map((d) => d.metric));
  return (SCREEN_METRICS.find((m) => !used.has(m.id)) ?? SCREEN_METRICS[0]).id;
}

export const PRESETS: readonly { id: string; label: string; description: string; filters: readonly Omit<FilterDraft, "id">[] }[] = [
  {
    id: "quality",
    label: "High returns, low debt",
    description: "ROIC at least 15%, operating margin at least 15%, net debt to equity at most 1×",
    filters: [
      { metric: "roic", op: "gte", value: "15" },
      { metric: "operating_margin", op: "gte", value: "15" },
      { metric: "net_debt_to_equity", op: "lte", value: "1" },
    ],
  },
  {
    id: "cash",
    label: "Strong cash conversion",
    description: "Free cash flow at least 80% of net income, gross margin at least 40%",
    filters: [
      { metric: "free_cash_flow_conversion", op: "gte", value: "80" },
      { metric: "gross_margin", op: "gte", value: "40" },
    ],
  },
];

export type FunnelStep = { id: "matched" | "filtered_out" | "no_data" | "no_period"; label: string; value: number };

/**
 * Where every issuer in the universe ended up. The four counts add up to the universe size, so
 * nothing disappears silently.
 */
export function funnel(r: ScreenResult): FunnelStep[] {
  const noPeriod = Math.max(0, r.universe_size - r.evaluated);
  return [
    { id: "matched", label: "Matched", value: r.matched },
    { id: "filtered_out", label: "Failed a filter", value: r.filtered_out },
    { id: "no_data", label: "Missing a filtered metric", value: r.no_data },
    { id: "no_period", label: "No computable period", value: noPeriod },
  ];
}

export type Cell = { text: string; unknown: boolean; filtered: boolean };

/** One result cell. A null value is "Unknown" (never 0), and a filtered-on metric is marked. */
export function cellFor(row: ScreenRow, metric: MetricId, r: ScreenResult): Cell {
  const raw = row.metrics[metric];
  const n = raw === null || raw === undefined ? null : Number(raw);
  const filtered = r.filters.some((f) => f.metric === metric);
  if (n === null || !Number.isFinite(n)) return { text: "Unknown", unknown: true, filtered };
  return { text: metricInfo(metric)?.kind === "multiple" ? `${n.toFixed(2)}×` : `${(n * 100).toFixed(1)}%`, unknown: false, filtered };
}

/** Filter shown in words, e.g. "Gross margin at least 40%". */
export function describeFilter(f: components["schemas"]["ScreenFilter"]): string {
  const info = metricInfo(f.metric);
  const n = Number(f.value);
  const value = info?.kind === "multiple" ? `${n}×` : `${Math.round(n * 1e6) / 1e4}%`;
  return `${info?.label ?? f.metric} ${OP_LABEL[f.op]} ${value}`;
}
