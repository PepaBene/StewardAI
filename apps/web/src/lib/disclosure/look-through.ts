import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type LookThrough = S["AnalyticsLookThroughOut"];
export type LookExposure = S["AnalyticsExposureOut"];
export type LookRelationship = S["AnalyticsRelationshipOut"];

const num = (v: string | null | undefined): number | null => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

/** A fraction of the whole portfolio as a percentage; a missing one is "unknown", never 0%. */
export function weightText(v: string | null | undefined, digits = 1): string {
  const n = num(v);
  return n === null ? "unknown" : `${(n * 100).toFixed(digits)}%`;
}

/** Largest total exposure first, so concentration is read from the top; ties by ticker. */
export const exposureOrder = (xs: readonly LookExposure[]): LookExposure[] => [...xs].sort((a, b) => (num(b.total_weight) ?? 0) - (num(a.total_weight) ?? 0) || a.ticker.localeCompare(b.ticker));

/** Known weighted shares first (largest first); relationships whose share is unknown follow and stay unknown. */
export const relationshipOrder = (xs: readonly LookRelationship[]): LookRelationship[] =>
  [...xs].sort((a, b) => {
    const wa = num(a.weighted_share);
    const wb = num(b.weighted_share);
    if (wa === null && wb === null) return a.ticker.localeCompare(b.ticker);
    if (wa === null) return 1;
    if (wb === null) return -1;
    return wb - wa || a.ticker.localeCompare(b.ticker);
  });

/** What the headline totals are measured against, in words a reader can check. */
export function coverageSentence(t: LookThrough["totals"], holdings: number): string {
  const unknown = t.unknown_weight_holdings.length;
  const base = `${t.relationship_covered_holdings} of ${holdings} holdings have a customer or supplier record, covering ${weightText(t.relationship_covered_weight)} of portfolio value.`;
  return unknown > 0 ? `${base} ${unknown} ${unknown === 1 ? "holding has" : "holdings have"} a record with no stated share (${weightText(t.unknown_weight)} of value), which is left unknown rather than counted as zero.` : base;
}

/** Mixed report dates or lagging fund reports change how far the totals can be trusted; say so. */
export function datingWarnings(v: LookThrough): string[] {
  const out: string[] = [];
  if (v.mixed_as_of) out.push("The datasets behind these totals were reported at different dates.");
  if (v.max_fund_lag_days != null && v.max_fund_lag_days > 0) out.push(`The oldest fund report used is ${v.max_fund_lag_days} days old.`);
  return out;
}
