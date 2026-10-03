import type { MetricDef, MetricId } from "./types";

const V = "GET /holdings/{ticker}/valuation";

/** The metrics available to a screen. Each maps to a figure an API endpoint already returns. */
export const METRICS: readonly MetricDef[] = [
  { id: "pe", label: "P/E", unit: "x", source: "valuation", endpoint: V, staleWhen: ["price", "eps"], heldOnly: false },
  { id: "pb", label: "P/B", unit: "x", source: "valuation", endpoint: V, staleWhen: ["price"], heldOnly: false },
  { id: "ps", label: "P/S", unit: "x", source: "valuation", endpoint: V, staleWhen: ["price", "eps"], heldOnly: false },
  { id: "peg", label: "PEG", unit: "x", source: "valuation", endpoint: V, staleWhen: ["price", "eps"], heldOnly: false },
  { id: "dividend_yield", label: "Dividend yield", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["price"], heldOnly: false },
  { id: "gross_margin", label: "Gross margin", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["eps"], heldOnly: false },
  { id: "operating_margin", label: "Operating margin", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["eps"], heldOnly: false },
  { id: "net_margin", label: "Net margin", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["eps"], heldOnly: false },
  { id: "roe", label: "Return on equity", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["eps"], heldOnly: false },
  { id: "roic", label: "Return on invested capital", unit: "pct", source: "valuation", endpoint: V, staleWhen: ["eps"], heldOnly: false },
  { id: "weight", label: "Portfolio weight", unit: "pct", source: "holdings", endpoint: "GET /portfolio/holdings", staleWhen: [], heldOnly: true },
  { id: "ppl_pct", label: "Unrealised P/L", unit: "pct", source: "holdings", endpoint: "GET /portfolio/holdings", staleWhen: [], heldOnly: true },
  { id: "day_change_pct", label: "Latest day change", unit: "pct", source: "prices", endpoint: "GET /portfolio/holdings/prices", staleWhen: [], heldOnly: true },
];

const BY_ID = new Map<string, MetricDef>(METRICS.map((m) => [m.id, m]));

export function metricDef(id: string): MetricDef | null {
  return BY_ID.get(id) ?? null;
}

export function isMetricId(id: string): id is MetricId {
  return BY_ID.has(id);
}

/** The ratio ids that come from the valuation response. */
export const VALUATION_METRICS = METRICS.filter((m) => m.source === "valuation").map((m) => m.id);
