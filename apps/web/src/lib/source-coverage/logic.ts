import { DATA_STATES, DATA_STATE_META, type DataState } from "@/lib/data-state/states";

import { providerLabel } from "./providers";
import {
  ENTITY_SCOPES,
  type AccessRow,
  type BudgetItem,
  type BudgetTone,
  type CoverageEntry,
  type EntityScope,
  type EntityScopes,
  type MatrixCell,
  type MatrixRow,
  type ProviderGroup,
  type SourceStatusRow,
  type UsageWindow,
} from "./types";

export type ScopeFilter = EntityScope | "all";
export type StateFilter = DataState | "all";

// ---------------------------------------------------------------------------------------
// Held / watchlist classification
// ---------------------------------------------------------------------------------------

/**
 * The backend marks no entity as held or watchlist: `/data/coverage` only says whether an
 * entity key is a security, a geography or neither. A security key is "held" when it is a
 * held ticker (or price symbol), else "watchlist" when it is a watchlist ticker, else "other".
 * Without the ticker sets (holdings or watchlist failed to load) nothing is guessed: every
 * entry is "other".
 */
export function scopeOf(entry: Pick<CoverageEntry, "entityKind" | "entityKey">, scopes: EntityScopes | null): EntityScope {
  if (!scopes || entry.entityKind !== "security") return "other";
  const key = entry.entityKey.toUpperCase();
  if (scopes.held.has(key)) return "held";
  if (scopes.watchlist.has(key)) return "watchlist";
  return "other";
}

/** Upper-cased ticker set (keys are compared case-insensitively). */
export function tickerSet(values: Iterable<string | null | undefined>): Set<string> {
  const set = new Set<string>();
  for (const v of values) if (typeof v === "string" && v.trim() !== "") set.add(v.trim().toUpperCase());
  return set;
}

// ---------------------------------------------------------------------------------------
// Cells and matrix
// ---------------------------------------------------------------------------------------

/** When no entry is usable, the state that explains most entries; ties go to the more actionable one. */
const GAP_PRIORITY: readonly DataState[] = ["unavailable", "quota_exhausted", "access_unverified", "paid_only", "not_covered"];

/** States that carry a usable value (possibly with a notice). */
const VALUE_STATES: ReadonlySet<DataState> = new Set(DATA_STATES.filter((s) => DATA_STATE_META[s].showsValue && s !== "disputed"));

/**
 * One cell's state from its entries: null without entries (unknown, never zero); disputed if
 * any entry is; all usable -> partial / stale / verified by precedence; some usable -> partial;
 * none usable -> the dominant gap state.
 */
export function cellState(states: readonly DataState[]): DataState | null {
  if (states.length === 0) return null;
  const counts = new Map<DataState, number>();
  for (const s of states) counts.set(s, (counts.get(s) ?? 0) + 1);
  if (counts.has("disputed")) return "disputed";
  const usable = states.filter((s) => VALUE_STATES.has(s)).length;
  if (usable === states.length) return counts.has("partial") ? "partial" : counts.has("stale") ? "stale" : "verified";
  if (usable > 0) return "partial";
  let best: DataState = "unavailable";
  let bestCount = -1;
  for (const s of GAP_PRIORITY) {
    const n = counts.get(s) ?? 0;
    if (n > bestCount) {
      best = s;
      bestCount = n;
    }
  }
  return best;
}

function sumKnown(values: readonly (number | null)[]): number | null {
  const known = values.filter((v): v is number => v !== null);
  return known.length === 0 ? null : known.reduce((a, b) => a + b, 0);
}

function maxIso(values: readonly string[]): string | null {
  let best: string | null = null;
  for (const v of values) if (best === null || new Date(v).getTime() > new Date(best).getTime()) best = v;
  return best;
}

export function buildCell(scope: EntityScope, entries: readonly CoverageEntry[]): MatrixCell {
  const states = entries.map((e) => e.state);
  const usable = entries.filter((e) => VALUE_STATES.has(e.state));
  const gaps = entries.filter((e) => !VALUE_STATES.has(e.state));
  const byState: Partial<Record<DataState, number>> = {};
  for (const s of states) byState[s] = (byState[s] ?? 0) + 1;
  const reasonCounts = new Map<string, number>();
  for (const e of gaps) if (e.reason) reasonCounts.set(e.reason, (reasonCounts.get(e.reason) ?? 0) + 1);
  const ages = entries.map((e) => e.ageDays).filter((a): a is number => a !== null);
  const periods = entries.map((e) => e.latestPeriod).filter((p): p is string => p !== null);
  const withExpected = entries.filter((e) => e.expectedPeriods !== null && e.expectedPeriods > 0);
  return {
    scope,
    state: cellState(states),
    entries: entries.length,
    available: usable.length,
    byState,
    lastCheckedAt: maxIso(entries.map((e) => e.checkedAt)),
    lastVerifiedAt: maxIso(usable.map((e) => e.checkedAt)),
    latestPeriod: periods.length ? periods.reduce((a, b) => (a > b ? a : b)) : null,
    maxAgeDays: ages.length ? Math.max(...ages) : null,
    expectedPeriods: sumKnown(withExpected.map((e) => e.expectedPeriods)),
    availablePeriods: sumKnown(withExpected.map((e) => e.availablePeriods)),
    refreshFailing: entries.some((e) => (e.state === "unavailable" || e.state === "quota_exhausted") && e.lastGoodObservationId !== null),
    reasons: [...reasonCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([r]) => r),
    gapKeys: gaps.map((e) => e.entityKey),
  };
}

/** Provider groups -> dataset rows -> held / watchlist / other cells, in label then dataset order. */
export function buildMatrix(entries: readonly CoverageEntry[], scopes: EntityScopes | null, access: readonly AccessRow[]): ProviderGroup[] {
  const byProvider = new Map<string, Map<string, Record<EntityScope, CoverageEntry[]>>>();
  for (const e of entries) {
    const datasets = byProvider.get(e.provider) ?? new Map<string, Record<EntityScope, CoverageEntry[]>>();
    const cells = datasets.get(e.dataset) ?? { held: [], watchlist: [], other: [] };
    cells[scopeOf(e, scopes)].push(e);
    datasets.set(e.dataset, cells);
    byProvider.set(e.provider, datasets);
  }
  const accessByProvider = new Map<string, AccessRow[]>();
  for (const a of access) accessByProvider.set(a.provider, [...(accessByProvider.get(a.provider) ?? []), a]);
  const providers = new Set([...byProvider.keys(), ...accessByProvider.keys()]);
  return [...providers]
    .map((provider): ProviderGroup => {
      const datasets = byProvider.get(provider) ?? new Map();
      const rows: MatrixRow[] = [...datasets.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([dataset, cells]) => ({
          id: `${provider}:${dataset}`,
          provider,
          providerLabel: providerLabel(provider),
          dataset,
          cells: Object.fromEntries(ENTITY_SCOPES.map((s) => [s, buildCell(s, cells[s])])) as Record<EntityScope, MatrixCell>,
        }));
      return { provider, label: providerLabel(provider), rows, access: accessByProvider.get(provider) ?? [] };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function cellsFor(row: MatrixRow, scope: ScopeFilter): readonly MatrixCell[] {
  return scope === "all" ? ENTITY_SCOPES.map((s) => row.cells[s]) : [row.cells[scope]];
}

/** Groups limited to a provider and to rows with a cell in the state (within the scope filter). Empty groups stay when no state is filtered. */
export function filterGroups(
  groups: readonly ProviderGroup[],
  { scope = "all", state = "all", provider = "all" }: { scope?: ScopeFilter; state?: StateFilter; provider?: string },
): ProviderGroup[] {
  return groups
    .filter((g) => provider === "all" || g.provider === provider)
    .map((g) => ({
      ...g,
      rows: g.rows.filter((r) => {
        const cells = cellsFor(r, scope);
        if (state === "all") return cells.some((c) => c.entries > 0) || scope === "all";
        return cells.some((c) => c.state === state);
      }),
    }))
    .filter((g) => state === "all" || g.rows.length > 0);
}

/** Cells per state across the scope, canonical order, zeros included; empty cells are not counted. */
export function stateCounts(groups: readonly ProviderGroup[], scope: ScopeFilter = "all"): Record<DataState, number> {
  const counts = Object.fromEntries(DATA_STATES.map((s) => [s, 0])) as Record<DataState, number>;
  for (const g of groups) for (const r of g.rows) for (const c of cellsFor(r, scope)) if (c.state) counts[c.state] += 1;
  return counts;
}

/** Access rows per state (canonical order, zeros included). */
export function accessCounts(access: readonly AccessRow[]): Record<DataState, number> {
  const counts = Object.fromEntries(DATA_STATES.map((s) => [s, 0])) as Record<DataState, number>;
  for (const a of access) counts[a.state] += 1;
  return counts;
}

/** "3 of 5 entries available" — the denominator is ledger entries, not the universe. */
export function describeCell(cell: MatrixCell): string {
  return cell.entries === 0 ? "No ledger entries" : `${cell.available} of ${cell.entries} ledger ${cell.entries === 1 ? "entry" : "entries"} available`;
}

// ---------------------------------------------------------------------------------------
// Request budget
// ---------------------------------------------------------------------------------------

const HISTORY_WINDOWS = 14;
/** Below this share of the allowance left, the budget is called low (presentation threshold only). */
export const LOW_HEADROOM = 0.1;

export function budgetTone(b: Pick<BudgetItem, "limit" | "used" | "remaining" | "headroom">): BudgetTone {
  if (b.limit === null) return "uncapped";
  if (b.used === null || b.remaining === null) return "untracked";
  if (b.remaining <= 0) return "exhausted";
  if (b.headroom !== null && b.headroom <= LOW_HEADROOM) return "low";
  return "ok";
}

/**
 * One budget per access endpoint: the cap comes from the access ledger (`quota`/`quota_period`)
 * only. The API publishes no reserve and no deferred demand, so none is shown; an endpoint
 * without a quota is "no published cap", never unlimited.
 */
export function buildBudgets(
  access: readonly AccessRow[],
  usage: readonly UsageWindow[],
  status: readonly SourceStatusRow[] = [],
): BudgetItem[] {
  const statusByKey = new Map(status.map((s) => [`${s.provider}/${s.endpoint}`, s]));
  return access.map((a) => {
    const limit = a.quota !== null && a.quota > 0 ? a.quota : null;
    const windows = usage
      .filter((u) => u.provider === a.provider && u.endpoint === a.endpoint && a.quotaPeriod !== null && u.windowKind === a.quotaPeriod)
      .sort((x, y) => new Date(x.windowStart).getTime() - new Date(y.windowStart).getTime());
    const current =
      a.quotaWindowStart !== null
        ? windows.find((w) => new Date(w.windowStart).getTime() === new Date(a.quotaWindowStart!).getTime())
        : undefined;
    const st = statusByKey.get(a.id);
    const item: BudgetItem = {
      id: a.id,
      provider: a.provider,
      providerLabel: providerLabel(a.provider),
      endpoint: a.endpoint,
      state: a.state,
      period: a.quotaPeriod,
      limit,
      used: limit === null ? null : a.quotaUsed,
      remaining: limit === null ? null : a.quotaRemaining,
      headroom: limit === null ? null : a.quotaHeadroom,
      resetsAt: current?.windowEnd ?? null,
      resetTimezone: a.resetTimezone,
      history: windows.slice(-HISTORY_WINDOWS).map((w) => w.credits),
      spentDay: st ? st.dayCredits : null,
      spentMonth: st ? st.monthCredits : null,
      tone: "uncapped",
      note: a.headroomReason,
    };
    item.tone = budgetTone(item);
    return item;
  });
}

export const TONE_LABEL: Record<BudgetTone, string> = {
  ok: "Within allowance",
  low: "Allowance nearly spent",
  exhausted: "Allowance exhausted",
  uncapped: "No published cap",
  untracked: "Usage not tracked",
};

/** "7 of 25 credits per day used", "No published cap" or the reason usage is untracked. */
export function describeBudget(b: BudgetItem): string {
  if (b.limit === null) return "No published cap";
  if (b.used === null) return `${b.limit} credits per ${b.period ?? "window"}; usage not tracked per window`;
  return `${b.used} of ${b.limit} credits per ${b.period ?? "window"} used`;
}

/** Fraction of the allowance spent (can exceed 1), null without a cap or usage. */
export function usedFraction(b: Pick<BudgetItem, "limit" | "used">): number | null {
  if (b.limit === null || b.limit <= 0 || b.used === null) return null;
  return Math.max(0, b.used / b.limit);
}

/** Endpoints whose recorded use exceeds the allowance: derived from the API's own numbers. */
export function overAllowance(budgets: readonly BudgetItem[]): BudgetItem[] {
  return budgets.filter((b) => b.limit !== null && b.used !== null && b.used > b.limit);
}

// ---------------------------------------------------------------------------------------
// Drill-through to failed jobs
// ---------------------------------------------------------------------------------------

export type FailedJob = { id: number; job_type: string; error: string | null; params: Record<string, unknown> };

/**
 * The newest failed job that names the provider: its `params.provider`, its error text or its
 * job type. The API has no provider -> job link, so this is a textual match and the UI says
 * "may be related" when it relies on it. `jobs` must be newest first.
 */
export function matchFailedJob(provider: string, jobs: readonly FailedJob[]): FailedJob | null {
  const id = provider.toLowerCase();
  const label = providerLabel(provider).toLowerCase();
  for (const j of jobs) {
    const p = typeof j.params?.provider === "string" ? j.params.provider.toLowerCase() : null;
    const error = (j.error ?? "").toLowerCase();
    if (p === id || j.job_type.toLowerCase().includes(id) || error.includes(id) || error.includes(label)) return j;
  }
  return null;
}

export const FAILED_JOBS_HREF = "/activity?status=failed#job-log";

/** The matched failed run, else the failed-jobs filter of the job log. */
export function drillThroughHref(provider: string, jobs: readonly FailedJob[]): { href: string; matched: FailedJob | null } {
  const matched = matchFailedJob(provider, jobs);
  return { href: matched ? `/jobs/${matched.id}` : FAILED_JOBS_HREF, matched };
}
