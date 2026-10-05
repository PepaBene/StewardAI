import type { components } from "@/lib/api/schema";

export type DataCoverage = components["schemas"]["DataCoverage"];
export type SourceAccess = components["schemas"]["SourceAccess"];
export type SourceUsage = components["schemas"]["SourceUsage"];

/** Every state the plan's coverage contract can report, in the order a gap matters to the owner. */
export const COVERAGE_STATES = [
  "available",
  "stale",
  "missing",
  "not_covered",
  "paid_only",
  "access_unverified",
  "quota_exhausted",
  "disputed",
] as const;
export type CoverageState = (typeof COVERAGE_STATES)[number];

export const STATE_LABEL: Record<CoverageState, string> = {
  available: "Available",
  stale: "Stale",
  missing: "Missing",
  not_covered: "Not covered",
  paid_only: "Paid plan only",
  access_unverified: "Access not verified",
  quota_exhausted: "Quota used up",
  disputed: "Sources disagree",
};

/** What each state means for the owner, in plain words (shown in the legend and cell tooltips). */
export const STATE_MEANING: Record<CoverageState, string> = {
  available: "Data is present and recent.",
  stale: "The last good value is kept, but it is older than expected.",
  missing: "The source should have this and returned nothing.",
  not_covered: "The source does not cover this security or country.",
  paid_only: "Only the provider's paid plan returns this. It is never called.",
  access_unverified: "No free-key test has succeeded for this endpoint yet.",
  quota_exhausted: "The free quota for this window is spent. It retries after the reset.",
  disputed: "Two sources disagree beyond tolerance. Review the conflict.",
};

export function isCoverageState(value: string): value is CoverageState {
  return (COVERAGE_STATES as readonly string[]).includes(value);
}

/** Unknown states from a newer backend render as "Missing" rather than disappearing. */
export function normalizeState(value: string): CoverageState {
  return isCoverageState(value) ? value : "missing";
}

const num = (v: number | string | null | undefined): number | null => {
  if (v === null || v === undefined) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};

export type MatrixCell = {
  /** The worst state among the cell's entities (the one the owner has to act on). */
  state: CoverageState;
  entries: number;
  available: number;
  /** Sum of available over sum of expected periods, null when no entry reports expected periods. */
  ratio: number | null;
  /** Oldest latest_period among the entries, for "how stale" in the tooltip. */
  oldestLatest: string | null;
  reasons: string[];
};

export type MatrixRow = { provider: string; dataset: string; cell: MatrixCell };

/** Severity order: lower index is worse. `available` is last so any gap in a cell is visible. */
const WORST_FIRST: readonly CoverageState[] = [
  "disputed",
  "quota_exhausted",
  "paid_only",
  "access_unverified",
  "missing",
  "not_covered",
  "stale",
  "available",
];

export function worstState(states: readonly CoverageState[]): CoverageState {
  let worst: CoverageState = "available";
  for (const s of states) if (WORST_FIRST.indexOf(s) < WORST_FIRST.indexOf(worst)) worst = s;
  return worst;
}

/**
 * Collapses per-entity coverage into one row per provider and dataset. The cell shows the
 * worst state so a single gap is never averaged away, with the available/total counts beside it.
 */
export function coverageMatrix(rows: readonly DataCoverage[]): MatrixRow[] {
  const groups = new Map<string, DataCoverage[]>();
  for (const r of rows) {
    const key = `${r.provider}\u0000${r.dataset}`;
    const list = groups.get(key);
    if (list) list.push(r);
    else groups.set(key, [r]);
  }
  const out: MatrixRow[] = [];
  for (const list of groups.values()) {
    const states = list.map((r) => normalizeState(r.state));
    let expected = 0;
    let available = 0;
    let anyExpected = false;
    for (const r of list) {
      const e = num(r.expected_periods);
      const a = num(r.available_periods);
      if (e !== null && e > 0 && a !== null) {
        expected += e;
        available += a;
        anyExpected = true;
      }
    }
    const latest = list.map((r) => r.latest_period).filter((d): d is string => !!d).sort();
    out.push({
      provider: list[0].provider,
      dataset: list[0].dataset,
      cell: {
        state: worstState(states),
        entries: list.length,
        available: states.filter((s) => s === "available").length,
        ratio: anyExpected ? available / expected : null,
        oldestLatest: latest[0] ?? null,
        reasons: [...new Set(list.map((r) => r.reason).filter((x): x is string => !!x))].slice(0, 3),
      },
    });
  }
  return out.sort((a, b) => a.provider.localeCompare(b.provider) || a.dataset.localeCompare(b.dataset));
}

export function stateCounts(rows: readonly DataCoverage[]): Record<CoverageState, number> {
  const counts = Object.fromEntries(COVERAGE_STATES.map((s) => [s, 0])) as Record<CoverageState, number>;
  for (const r of rows) counts[normalizeState(r.state)] += 1;
  return counts;
}

export type BudgetRow = {
  key: string;
  provider: string;
  endpoint: string;
  period: string | null;
  used: number | null;
  quota: number | null;
  /** Fraction of the quota spent; null when the quota or the window is unknown. */
  burn: number | null;
  /** Why there is no burn figure; shown instead of a misleading empty bar. */
  unknownReason: string | null;
  denial: string | null;
  freshness: SourceAccess["freshness"];
};

/** Provider endpoints with a tracked quota first, most-spent first; untracked ones after. */
export function budgetRows(access: readonly SourceAccess[]): BudgetRow[] {
  const rows = access.map((a): BudgetRow => {
    const quota = num(a.quota);
    const used = num(a.quota_used);
    const tracked = quota !== null && quota > 0 && used !== null;
    return {
      key: `${a.provider}/${a.endpoint}`,
      provider: a.provider,
      endpoint: a.endpoint,
      period: a.quota_period,
      used,
      quota,
      burn: tracked ? Math.min(used / quota, 1) : null,
      unknownReason: tracked ? null : (a.headroom_reason ?? "No quota is recorded for this endpoint."),
      denial: a.last_denial_state,
      freshness: a.freshness,
    };
  });
  return rows.sort((x, y) => {
    if ((x.burn === null) !== (y.burn === null)) return x.burn === null ? 1 : -1;
    return (y.burn ?? 0) - (x.burn ?? 0) || x.key.localeCompare(y.key);
  });
}

export const FRESHNESS_LABEL: Record<SourceAccess["freshness"], string> = {
  recent: "Recent success",
  stale: "Last success is old",
  never_succeeded: "Never succeeded",
};

/** Endpoints that need an owner decision: denied, never worked or hard-blocked. */
export function needsAttention(a: SourceAccess): boolean {
  return a.hard_blocked || a.last_denial_at !== null || a.freshness === "never_succeeded";
}
