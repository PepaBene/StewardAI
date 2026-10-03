import type { DataState } from "@/lib/data-state/states";
import { toDataState } from "@/lib/data-state/states";
import { asOfParam, isIsoDate } from "@/lib/network/logic";

import type { CoverageEntry, Disagreement, EvidenceSummary, FinancialHistory, Gap, SectionCoverage } from "./types";

/** Decimals arrive as numbers or numeric strings; anything else is missing (null), never 0. */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** The `as_of` value to send for a picked day (end of day UTC), or undefined for "now". */
export function asOfQuery(day: string): string | undefined {
  return asOfParam(day);
}

/** A picked day that the backend would reject (it refuses an `as_of` in the future). */
export function isFutureDay(day: string, today: string): boolean {
  return isIsoDate(day) && isIsoDate(today) && day > today;
}

/** Backend coverage-section state -> the shared vocabulary. */
export function sectionState(c: SectionCoverage): DataState {
  return c.state === "covered" ? "verified" : c.state;
}

/** Gap state -> the shared vocabulary. `missing` is "unavailable": nothing is stored, nothing is assumed. */
export function gapState(state: Gap["state"]): DataState {
  switch (state) {
    case "missing":
      return "unavailable";
    case "unresolved":
      return "disputed";
    default:
      return state;
  }
}

/** Both responses' gaps, one entry per domain+reason, in a stable order (domain, then reason). */
export function mergeGaps(...lists: readonly (readonly Gap[] | null | undefined)[]): Gap[] {
  const seen = new Map<string, Gap>();
  for (const list of lists) {
    for (const g of list ?? []) {
      const key = `${g.domain}\u0000${g.reason}`;
      if (!seen.has(key)) seen.set(key, g);
    }
  }
  return [...seen.values()].sort((a, b) => a.domain.localeCompare(b.domain) || a.reason.localeCompare(b.reason));
}

export type HistorySummary = {
  statements: { name: string; count: number }[];
  provenance: { cls: string; count: number; share: number | null }[];
  /** Fact rows the provenance shares are divided by (the response's `total_rows`). */
  denominator: number;
  returned: number;
  truncated: boolean;
};

export function summariseHistory(h: FinancialHistory): HistorySummary {
  const statements = Object.entries(h.statements)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const total = h.total_rows;
  const provenance = Object.entries(h.provenance)
    .map(([cls, count]) => ({ cls, count, share: total > 0 ? count / total : null }))
    .sort((a, b) => b.count - a.count || a.cls.localeCompare(b.cls));
  return { statements, provenance, denominator: total, returned: h.returned_rows, truncated: h.returned_rows < total };
}

export type DisagreementRow = {
  id: number;
  metric: string;
  status: string;
  periodEnd: string;
  preferred: { source: string | null; provenance: string; value: number | null; knownAt: string | null };
  alternative: { source: string; provenance: string; value: number | null; knownAt: string };
  difference: number | null;
  tolerance: number | null;
  /** True only when both numbers and the tolerance exist and the difference exceeds it. */
  exceedsTolerance: boolean | null;
  /** primary-vs-vendor: the two sides come from different provenance classes. */
  crossClass: boolean;
  resolution: string | null;
};

/** Largest absolute difference first; unknown differences last; ties by metric then id. */
export function disagreementRows(list: readonly Disagreement[]): DisagreementRow[] {
  const rows = list.map((d): DisagreementRow => {
    const difference = toNumber(d.difference);
    const tolerance = toNumber(d.tolerance);
    return {
      id: d.conflict_id,
      metric: d.metric,
      status: d.status,
      periodEnd: d.period_end,
      preferred: { source: d.preferred_source, provenance: d.preferred_provenance, value: toNumber(d.preferred_value), knownAt: d.preferred_known_at },
      alternative: { source: d.alternative_source, provenance: d.alternative_provenance, value: toNumber(d.alternative_value), knownAt: d.alternative_known_at },
      difference,
      tolerance,
      exceedsTolerance: difference !== null && tolerance !== null ? Math.abs(difference) > tolerance : null,
      crossClass: d.preferred_provenance !== d.alternative_provenance,
      resolution: d.resolution,
    };
  });
  return rows.sort((a, b) => {
    const da = a.difference === null ? -1 : Math.abs(a.difference);
    const db = b.difference === null ? -1 : Math.abs(b.difference);
    return db - da || a.metric.localeCompare(b.metric) || a.id - b.id;
  });
}

/** Coverage entry -> state and "available of expected periods" (null denominators stay null). */
export function coverageEntryView(e: CoverageEntry): { state: DataState; covered: number | null; total: number | null } {
  const total = e.expected_periods !== null && e.expected_periods > 0 ? e.expected_periods : null;
  const covered = total !== null && e.available_periods !== null ? e.available_periods : null;
  return { state: toDataState(e.state), covered, total };
}

const DAY = /^\d{4}-\d{2}-\d{2}/;

export type ClaimKnownAsOf = {
  /** Latest `as_of` (YYYY-MM-DD) among the cited sources that carry a date; null when none. */
  date: string | null;
  /** Oldest dated cited source (YYYY-MM-DD); equals `date` when all share one day. */
  oldest: string | null;
  cited: number;
  /** Cited sources with no usable date: the claim's known-as-of is then not fully known. */
  undated: number;
};

/**
 * The report payload has no per-claim known-at, only each source's `as_of`. The honest
 * per-claim date is the latest dated cited source, with undated citations counted, not hidden.
 */
export function claimKnownAsOf(sourceIds: readonly string[], sources: ReadonlyMap<string, { as_of?: string | null }>): ClaimKnownAsOf {
  let date: string | null = null;
  let oldest: string | null = null;
  let undated = 0;
  let cited = 0;
  for (const id of new Set(sourceIds)) {
    const s = sources.get(id);
    if (!s) continue;
    cited += 1;
    const m = s.as_of ? DAY.exec(s.as_of) : null;
    if (!m) {
      undated += 1;
      continue;
    }
    if (date === null || m[0] > date) date = m[0];
    if (oldest === null || m[0] < oldest) oldest = m[0];
  }
  return { date, oldest, cited, undated };
}

/** Earliest and latest dated source of a whole report, or nulls when none is dated. */
export function sourceDateRange(sources: readonly { as_of?: string | null }[]): { min: string | null; max: string | null; undated: number } {
  let min: string | null = null;
  let max: string | null = null;
  let undated = 0;
  for (const s of sources) {
    const m = s.as_of ? DAY.exec(s.as_of) : null;
    if (!m) {
      undated += 1;
      continue;
    }
    if (min === null || m[0] < min) min = m[0];
    if (max === null || m[0] > max) max = m[0];
  }
  return { min, max, undated };
}

export type EvidenceCounts = { domain: string; label: string; state: DataState; reason: string; items: number }[];

export function evidenceCounts(s: EvidenceSummary): EvidenceCounts {
  const channelItems = s.positions.control.stakes.length + (s.positions.institutional.ownership?.top_holders.length ?? 0) + s.positions.funds.positions.positions.length;
  return [
    { domain: "filed-notes", label: "Filed notes", state: sectionState(s.filed_notes.coverage), reason: s.filed_notes.coverage.reason, items: s.filed_notes.notes.length },
    { domain: "positions", label: "Control, institutional and fund positions", state: sectionState(worstCoverage([s.positions.control.coverage, s.positions.institutional.coverage, s.positions.funds.coverage])), reason: [s.positions.control.coverage.reason, s.positions.institutional.coverage.reason, s.positions.funds.coverage.reason].join(" "), items: channelItems },
    { domain: "insider-activity", label: "Insider activity", state: sectionState(s.insider_activity.coverage), reason: s.insider_activity.coverage.reason, items: s.insider_activity.net_discretionary_derived_from.length },
    { domain: "dependencies", label: "Dependencies", state: sectionState(s.dependencies.coverage), reason: s.dependencies.coverage.reason, items: s.dependencies.dependencies.length },
    { domain: "deals", label: "Deals", state: sectionState(s.deals.coverage), reason: s.deals.coverage.reason, items: s.deals.deals.length },
    { domain: "consensus", label: "Consensus vintages", state: sectionState(s.consensus.coverage), reason: s.consensus.coverage.reason, items: s.consensus.series.length },
  ];
}

/** A group is only "covered" when every channel is; any partial makes it partial; else not covered. */
export function worstCoverage(list: readonly SectionCoverage[]): SectionCoverage {
  if (list.every((c) => c.state === "covered")) return { state: "covered", reason: "" };
  if (list.every((c) => c.state === "not_covered")) return { state: "not_covered", reason: "" };
  return { state: "partial", reason: "" };
}

/** Latest ISO instant among the given ones, ignoring null/invalid; null when there is none. */
export function latestKnownAt(values: readonly (string | null | undefined)[]): string | null {
  let best: string | null = null;
  let bestT = -Infinity;
  for (const v of values) {
    if (!v) continue;
    const t = Date.parse(v);
    if (Number.isNaN(t)) continue;
    if (t > bestT) {
      bestT = t;
      best = v;
    }
  }
  return best;
}
