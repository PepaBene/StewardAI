/**
 * View models for the country/industry context and the commodity/FX input panels.
 * `/macro/context/{ticker}` and `/holdings/{ticker}/exposure-context` are both typed (the
 * latter's assumption rows stay open-ended). A missing value stays null, never zero.
 */
import type { components } from "@/lib/api/schema";
import { toNumber } from "@/lib/reference/peers";

export type MacroContext = components["schemas"]["MacroContext"];
type ContextPoint = components["schemas"]["ContextPoint"];

export type RangePosition = { low: number; high: number; value: number; pos: number };

export type ContextCard = {
  key: string;
  seriesId: string;
  geography: string;
  source: string;
  unit: string;
  value: number | null;
  previousValue: number | null;
  period: string;
  previousPeriod: string | null;
  releaseAt: string | null;
  sourceUrl: string;
  /** Where the value sits within its five-year range, null when the range is unknown or flat. */
  fiveYear: RangePosition | null;
  tenYear: RangePosition | null;
  /** The provider returned no value (denied, not covered, ...) and why. */
  accessState: string;
};

export function rangePosition(value: number | null, low: number | null, high: number | null): RangePosition | null {
  if (value === null || low === null || high === null || high <= low) return null;
  const clamped = Math.min(Math.max(value, low), high);
  return { low, high, value, pos: ((clamped - low) / (high - low)) * 100 };
}

function card(p: ContextPoint): ContextCard {
  const value = toNumber(p.value);
  return {
    key: `${p.source}|${p.series_id}|${p.geography}`,
    seriesId: p.series_id,
    geography: p.geography,
    source: p.source,
    unit: p.unit,
    value,
    previousValue: toNumber(p.previous_value),
    period: p.period,
    previousPeriod: p.previous_period ?? null,
    releaseAt: p.release_at,
    sourceUrl: p.source_url,
    fiveYear: rangePosition(value, toNumber(p.five_year_low), toNumber(p.five_year_high)),
    tenYear: rangePosition(value, toNumber(p.ten_year_low), toNumber(p.ten_year_high)),
    accessState: p.access_state,
  };
}

export type ContextEffect = {
  key: string;
  segment: string;
  geography: string | null;
  hsCode: string | null;
  basis: string;
  weight: number | null;
  unknownWeight: number | null;
  explanation: string;
  relatedSeries: string[];
};

export type ContextView = {
  cards: ContextCard[];
  effects: ContextEffect[];
  /** Coverage rows that are not "available", each with the provider's reason. */
  gaps: { key: string; provider: string; dataset: string; geography: string; state: string; reason: string | null }[];
  exposureUnknown: boolean;
  asOf: string;
};

/** Cards are ordered by geography then series so a country's figures sit together. */
export function contextView(ctx: MacroContext): ContextView {
  const cards = ctx.observations.map(card).sort((a, b) => a.geography.localeCompare(b.geography) || a.seriesId.localeCompare(b.seriesId));
  const effects = ctx.effects.map((e, i) => ({
    key: `${e.segment}|${e.geography ?? ""}|${e.hs_code ?? ""}|${i}`,
    segment: e.segment,
    geography: e.geography,
    hsCode: e.hs_code,
    basis: e.basis,
    weight: toNumber(e.weight),
    unknownWeight: toNumber(e.unknown_weight),
    explanation: e.explanation,
    relatedSeries: e.related_series,
  }));
  const gaps = ctx.coverage
    .filter((c) => c.state !== "available")
    .map((c) => ({ key: `${c.provider}|${c.dataset}|${c.geography}`, provider: c.provider, dataset: c.dataset, geography: c.geography, state: c.state, reason: c.reason }));
  return { cards, effects, gaps, exposureUnknown: ctx.exposure_unknown, asOf: ctx.as_of };
}

/** Change against the previous release, null when either value is missing. */
export function change(c: ContextCard): number | null {
  return c.value !== null && c.previousValue !== null ? c.value - c.previousValue : null;
}

export const BASIS_LABEL: Record<string, string> = {
  reported: "Reported by the company",
  estimated: "Estimated",
  user_assigned: "Assigned by you",
};

export function formatUnitValue(value: number | null, unit: string): string {
  if (value === null) return "No value";
  const pct = /percent|^%$|rate/i.test(unit);
  const text = Math.abs(value) >= 1000 ? value.toLocaleString("en-US", { maximumFractionDigits: 0 }) : value.toFixed(Math.abs(value) < 10 ? 2 : 1);
  return pct && !text.endsWith("%") ? `${text}%` : `${text}${pct ? "" : unit ? ` ${unit}` : ""}`;
}

// --- Commodity and FX inputs ------------------------------------------------------------

export type InputSeries = {
  metric: string;
  unit: string | null;
  currency: string | null;
  /** Oldest to newest. */
  points: { date: string; value: number }[];
  sourceUrl: string | null;
  knownAt: string | null;
};

export type InputAssumption = {
  kind: string;
  name: string;
  share: number | null;
  /** Who decided: "ai" until you edit or remove the row; "owner" otherwise. */
  origin: "ai" | "owner";
  sourceUrl: string | null;
  period: string | null;
  quote: string | null;
};

export type ExposureInputs = {
  /** Series fetched on request (Alpha Vantage) and the stored series linked to an exposure. */
  series: InputSeries[];
  assumptions: InputAssumption[];
  /** "kind:name" of exposures with no stored series. */
  unlinked: string[];
  limitations: string[];
};

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function parseExposureInputs(payload: components["schemas"]["ExposureContextResponse"]): ExposureInputs {
  const byMetric = new Map<string, InputSeries>();
  for (const o of payload.observations) {
    // value is a JSON number (or null when the provider gave only text): null never becomes a point.
    const value = o.value ?? null;
    const date = o.period_end ?? null;
    if (value === null || !date || !o.metric) continue;
    let s = byMetric.get(o.metric);
    if (!s) {
      s = { metric: o.metric, unit: o.unit ?? null, currency: o.currency ?? null, points: [], sourceUrl: o.source_url || null, knownAt: o.known_at || null };
      byMetric.set(o.metric, s);
    }
    s.points.push({ date, value });
    if (o.known_at && (!s.knownAt || o.known_at > s.knownAt)) s.knownAt = o.known_at;
  }
  const series = [...byMetric.values()].map((s) => ({ ...s, points: dedupe(s.points) })).sort((a, b) => a.metric.localeCompare(b.metric));
  // `exposure_assumptions` rows are still free-form on the wire (the model leaves them open).
  const assumptions: InputAssumption[] = payload.exposure_assumptions.map((e) => ({
    kind: str(e.kind) ?? "other",
    name: str(e.name) ?? "Unnamed",
    share: toNumber(e.share),
    origin: e.origin === "ai" ? "ai" : "owner",
    sourceUrl: str(e.source_url),
    period: str(e.period),
    quote: str(e.quote),
  }));
  for (const s of payload.linked_series ?? []) {
    if (s.points.length === 0) continue;
    series.push({
      metric: `${s.label} (for ${s.exposure_name})`,
      unit: s.unit ?? null,
      currency: s.currency ?? null,
      points: s.points.map((p) => ({ date: p.date, value: p.value })),
      sourceUrl: s.source_url ?? null,
      knownAt: null,
    });
  }
  return { series, assumptions, unlinked: payload.unlinked ?? [], limitations: payload.limitations };
}

/** One value per date (the last one wins), oldest first. */
function dedupe(points: { date: string; value: number }[]): { date: string; value: number }[] {
  const map = new Map<string, number>();
  for (const p of points) map.set(p.date, p.value);
  return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([date, value]) => ({ date, value }));
}

/** Total change over the window, null with fewer than two points or a zero start. */
export function windowChange(s: InputSeries): number | null {
  if (s.points.length < 2) return null;
  const first = s.points[0].value;
  if (first === 0) return null;
  return (s.points[s.points.length - 1].value - first) / Math.abs(first);
}

export const ASSUMPTION_LABEL: Record<string, string> = {
  revenue_currency: "Revenue currency",
  revenue_region: "Revenue region",
  commodity_input: "Commodity input",
  other: "Other",
};
