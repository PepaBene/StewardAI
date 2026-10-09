import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type CoverageReport = S["AnalyticsCoverageOut"];
export type CoverageCell = S["AnalyticsCoverageCellOut"];
export type CoverageQuality = S["AnalyticsQualityOut"];
export type CoverageFailure = S["AnalyticsFailureOut"];
export type CoverageStage = S["AnalyticsReleaseStageOut"];
export type CompanyEvidence = S["AnalyticsCompanyEvidenceOut"];
export type CoverageState = S["AnalyticsEvidenceDatasetOut"]["coverage_state"];

export const STATE_LABEL: Record<CoverageState, string> = {
  available: "Records stored",
  stale: "Stored but old",
  unresolved_identity: "No issuer identity",
  not_disclosed: "Not disclosed",
  not_covered: "Not covered",
  paid_only: "Paid source only",
  authentication_failed: "Authentication failed",
  retrieval_failed: "Retrieval failed",
  parse_failed: "Parse failed",
  under_review: "Under review",
  none_known: "None known (not proof of none)",
};

export const QUALITY_LABEL: Record<CoverageQuality["state"], string> = {
  not_sampled: "Not sampled",
  sample_too_small: "Sample too small",
  meets_bar: "Meets the bar",
  below_bar: "Below the bar",
};

export const STAGE_LABEL: Record<CoverageStage["state"], string> = {
  gates_met: "Gates met",
  gates_open: "Gates open",
  not_started: "Not started",
};

const num = (v: string | null | undefined): number | null => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

/** A fraction as a percentage, or "unknown" when there is no denominator: a missing share is never 0%. */
export function pct(share: string | null | undefined, digits = 0): string {
  const n = num(share);
  return n === null ? "unknown" : `${(n * 100).toFixed(digits)}%`;
}

export function money(v: string | null | undefined): string {
  const n = num(v);
  return n === null ? "unknown" : n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function dayLabel(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : "none";
}

export type Rollup = {
  dataset: string;
  label: string;
  tickers: number;
  covered: number;
  uncovered: number;
  stale: number;
  denominator: number;
  coveredValue: number;
  share: number | null;
  /** True when a cell had an unknown denominator or covered value: the share is then not stated. */
  shareUnknown: boolean;
  first: string | null;
  last: string | null;
  unit: string;
  source: string;
  method: string;
};

/** One row per dataset over every jurisdiction: counts and values add, dates take the earliest and latest. */
export function rollup(cells: readonly CoverageCell[]): Rollup[] {
  const by = new Map<string, Rollup>();
  for (const c of cells) {
    const r = by.get(c.dataset) ?? { dataset: c.dataset, label: c.label, tickers: 0, covered: 0, uncovered: 0, stale: 0, denominator: 0, coveredValue: 0, share: null, shareUnknown: false, first: null, last: null, unit: c.unit, source: c.source, method: c.method };
    r.tickers += c.tickers;
    r.covered += c.covered;
    r.uncovered += c.uncovered;
    r.stale += c.stale;
    const den = num(c.denominator_value);
    const cov = num(c.covered_value);
    if (den == null || cov == null) {
      // A cell with an unknown side adds to neither sum, and the share is then not stated at all.
      if (c.tickers > 0) r.shareUnknown = true;
    } else {
      r.denominator += den;
      r.coveredValue += cov;
    }
    if (c.first_known_at && (!r.first || c.first_known_at < r.first)) r.first = c.first_known_at;
    if (c.last_known_at && (!r.last || c.last_known_at > r.last)) r.last = c.last_known_at;
    by.set(c.dataset, r);
  }
  return [...by.values()].map((r) => ({ ...r, share: r.shareUnknown || r.denominator <= 0 ? null : r.coveredValue / r.denominator }));
}

export const jurisdictions = (cells: readonly CoverageCell[]): string[] => [...new Set(cells.map((c) => c.jurisdiction))].sort();

/** The cell for one dataset and jurisdiction; absent when no held ticker is in that jurisdiction. */
export const cellFor = (cells: readonly CoverageCell[], dataset: string, jurisdiction: string): CoverageCell | undefined => cells.find((c) => c.dataset === dataset && c.jurisdiction === jurisdiction);

/** "3 of 5 covered", or "no holdings" when the cell has none; never "0 of 0". */
export const cellSummary = (c: CoverageCell | undefined): string => (!c || c.tickers === 0 ? "no holdings" : `${c.covered} of ${c.tickers} covered`);

/** A sampled error rate with its numerator and denominator; null rate reads as "not sampled". */
export function qualityLine(q: CoverageQuality): string {
  if (q.labelled === 0) return `Not sampled (needs ${q.needed} labels)`;
  return `${q.errors} wrong of ${q.labelled} labelled · error rate ${q.error_rate === null ? "unknown" : `${(Number(q.error_rate) * 100).toFixed(1)}%`} · bar ${(Number(q.bar) * 100).toFixed(0)}% correct, ${q.needed} needed`;
}

/** Every distinct blind spot, once, tagged with its dataset; uncovered-ticker spots keep their jurisdiction. */
export function blindSpots(cells: readonly CoverageCell[]): { dataset: string; text: string }[] {
  const seen = new Set<string>();
  const out: { dataset: string; text: string }[] = [];
  for (const c of cells) {
    for (const t of c.blind_spots) {
      const text = /held with no record/.test(t) ? `${c.jurisdiction}: ${t}` : t;
      const key = `${c.dataset}|${text}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ dataset: c.label, text });
      }
    }
  }
  return out;
}

/** True when a cell has stored records that are all old, or none at all: the two cases to look at first. */
export const needsAttention = (c: CoverageCell): boolean => c.covered === 0 || c.stale > 0;

export function failureLine(f: CoverageFailure): string {
  const what = f.kind === "source" ? `Source ${f.dataset}` : `Dataset ${f.dataset}`;
  return `${what} (${f.jurisdiction}): ${STATE_LABEL[f.state as CoverageState] ?? f.state.replaceAll("_", " ")}${f.count > 1 ? `, ${f.count} entries` : ""}${f.detail ? ` - ${f.detail}` : ""}`;
}
