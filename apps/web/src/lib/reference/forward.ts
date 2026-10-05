/**
 * The estimates card's view model, from `/holdings/{ticker}/forward-data/consensus`
 * (untyped in OpenAPI). The four kinds are never blended: published consensus is charted by
 * vintage; guidance, owner scenarios and calendar estimates are listed under their own label.
 */
import { toNumber } from "./peers";

export type ForwardKind = "company_guidance" | "owner_scenario" | "published_analyst_consensus" | "event_calendar_estimate";

export const KIND_LABEL: Record<ForwardKind, string> = {
  company_guidance: "Company guidance",
  owner_scenario: "Your scenario",
  published_analyst_consensus: "Published consensus",
  event_calendar_estimate: "Calendar estimate",
};

export type Vintage = {
  knownAt: string;
  value: number | null;
  low: number | null;
  high: number | null;
  analystCount: number | null;
};

export type ConsensusSeries = {
  key: string;
  metric: string;
  fiscalPeriod: string;
  provider: string | null;
  currency: string | null;
  basis: string | null;
  /** Oldest to newest. */
  vintages: Vintage[];
  revision: number | null;
};

export type OtherEstimate = {
  id: number;
  kind: Exclude<ForwardKind, "published_analyst_consensus">;
  metric: string;
  fiscalPeriod: string | null;
  provider: string | null;
  currency: string | null;
  basis: string | null;
  value: number | null;
  low: number | null;
  high: number | null;
  knownAt: string | null;
  sourceUrl: string | null;
};

export type ForwardView = { consensus: ConsensusSeries[]; others: OtherEstimate[] };

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const OTHER_KINDS = ["company_guidance", "owner_scenario", "event_calendar_estimate"] as const;

export function parseForward(payload: unknown): ForwardView | null {
  if (!isRecord(payload)) return null;
  const consensus: ConsensusSeries[] = [];
  for (const s of Array.isArray(payload.consensus_series) ? payload.consensus_series : []) {
    if (!isRecord(s)) continue;
    const vintages: Vintage[] = (Array.isArray(s.vintages) ? s.vintages : []).filter(isRecord).map((v) => ({
      knownAt: str(v.known_at) ?? "",
      value: toNumber(v.value),
      low: toNumber(v.low),
      high: toNumber(v.high),
      analystCount: toNumber(v.analyst_count),
    }));
    vintages.sort((a, b) => a.knownAt.localeCompare(b.knownAt));
    const metric = str(s.metric) ?? "unknown";
    const fiscalPeriod = str(s.fiscal_period) ?? "";
    const provider = str(s.provider);
    consensus.push({
      key: `${metric}|${fiscalPeriod}|${provider ?? ""}|${str(s.currency) ?? ""}|${str(s.basis) ?? ""}`,
      metric,
      fiscalPeriod,
      provider,
      currency: str(s.currency),
      basis: str(s.basis),
      vintages,
      revision: toNumber(s.revision_since_previous),
    });
  }
  const others: OtherEstimate[] = [];
  for (const o of Array.isArray(payload.observations) ? payload.observations : []) {
    if (!isRecord(o)) continue;
    const kind = OTHER_KINDS.find((k) => k === o.kind);
    if (!kind) continue;
    others.push({
      id: toNumber(o.id) ?? others.length,
      kind,
      metric: str(o.metric) ?? "unknown",
      fiscalPeriod: str(o.fiscal_period),
      provider: str(o.provider),
      currency: str(o.currency),
      basis: str(o.basis),
      value: toNumber(o.value),
      low: toNumber(o.low),
      high: toNumber(o.high),
      knownAt: str(o.known_at),
      sourceUrl: str(o.source_url),
    });
  }
  return { consensus, others };
}

export function latestVintage(series: ConsensusSeries): Vintage | null {
  return series.vintages[series.vintages.length - 1] ?? null;
}

/** Spread of the analyst range as a share of the mean, null unless the range and a non-zero value exist. */
export function dispersion(v: Vintage | null): number | null {
  if (!v || v.low === null || v.high === null || v.value === null || v.value === 0) return null;
  return (v.high - v.low) / Math.abs(v.value);
}

/** Fiscal periods with a consensus, oldest first, for a period switch. */
export function consensusPeriods(view: ForwardView): string[] {
  return [...new Set(view.consensus.map((s) => s.fiscalPeriod))].sort();
}

export function metricLabel(metric: string): string {
  return metric.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}
