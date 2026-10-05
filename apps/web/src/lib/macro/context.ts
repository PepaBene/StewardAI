/**
 * View models for the country/industry context and the commodity/FX input panels.
 * `/macro/context/{ticker}` is typed; `/holdings/{ticker}/exposure-context` is not, so that
 * payload is read defensively. A missing value stays null and is shown as missing, never zero.
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

export type InputAssumption = { kind: string; name: string; share: number | null };

export type ExposureInputs = { series: InputSeries[]; assumptions: InputAssumption[]; limitations: string[] };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function parseExposureInputs(payload: unknown): ExposureInputs | null {
  if (!isRecord(payload)) return null;
  const byMetric = new Map<string, InputSeries>();
  for (const o of Array.isArray(payload.observations) ? payload.observations : []) {
    if (!isRecord(o)) continue;
    const value = toNumber(o.value);
    const date = str(o.period_end);
    const metric = str(o.metric);
    if (value === null || !date || !metric) continue;
    let s = byMetric.get(metric);
    if (!s) {
      s = { metric, unit: str(o.unit), currency: str(o.currency), points: [], sourceUrl: str(o.source_url), knownAt: str(o.known_at) };
      byMetric.set(metric, s);
    }
    s.points.push({ date, value });
    const known = str(o.known_at);
    if (known && (!s.knownAt || known > s.knownAt)) s.knownAt = known;
  }
  const series = [...byMetric.values()].map((s) => ({ ...s, points: dedupe(s.points) })).sort((a, b) => a.metric.localeCompare(b.metric));
  const assumptions: InputAssumption[] = (Array.isArray(payload.exposure_assumptions) ? payload.exposure_assumptions : [])
    .filter(isRecord)
    .map((e) => ({ kind: str(e.kind) ?? "other", name: str(e.name) ?? "Unnamed", share: toNumber(e.share) }));
  return {
    series,
    assumptions,
    limitations: Array.isArray(payload.limitations) ? payload.limitations.filter((l): l is string => typeof l === "string") : [],
  };
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
