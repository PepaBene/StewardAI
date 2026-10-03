import type { DataState } from "@/lib/data-state/states";

/**
 * Typed, adapted shapes of the owner-only read APIs behind the source-coverage view:
 * `GET /data/coverage`, `/sources/access`, `/sources/usage`, `/sources/status` and
 * `GET /verification/zero-cost-gate`. Backend state strings are converted once, by
 * `toDataState`, when the rows are adapted (see `parse.ts`); the raw string is kept beside
 * it for display. No field carries a credential or a raw provider payload (the API redacts
 * them before they leave the backend).
 */

/** Filters of `GET /data/coverage` (all optional; `state` is the backend's own state string). */
export type CoverageFilters = {
  provider?: string;
  dataset?: string;
  entityKey?: string;
  /** Backend ledger state, e.g. "available" or "paid_only" (not a DataState). */
  state?: string;
  /** ISO date: entries whose period span contains it. */
  period?: string;
  entityKind?: EntityKind;
};

export type EntityKind = "security" | "geography" | "other";

/** One coverage-ledger entry: (provider, dataset, entity). */
export type CoverageEntry = {
  /** Stable key: provider:dataset:entity. */
  id: string;
  provider: string;
  dataset: string;
  entityKey: string;
  entityKind: EntityKind;
  /** The adapted state; an available entry with a known ratio below 1 is "partial". */
  state: DataState;
  /** The backend's own string (available, temporarily_failed, ...). */
  rawState: string;
  expectedPeriods: number | null;
  availablePeriods: number | null;
  /** available / expected periods; null unless both are known and expected is positive. */
  coverageRatio: number | null;
  earliestPeriod: string | null;
  latestPeriod: string | null;
  /** Days from latestPeriod to the day of the response; null without a period. */
  ageDays: number | null;
  /** Newest check of this entry, successful or not. */
  checkedAt: string;
  lastGoodObservationId: number | null;
  reason: string | null;
};

export type AccessFreshness = "never_succeeded" | "recent" | "stale";

/** One (provider, endpoint) access ledger row from `GET /sources/access`. */
export type AccessRow = {
  id: string;
  provider: string;
  endpoint: string;
  accountTier: string;
  /** Adapted ledger state after freshness: available + stale -> stale, never succeeded -> access_unverified. */
  state: DataState;
  /** The ledger's own state string. */
  rawState: string;
  freshness: AccessFreshness;
  lastSuccessAt: string | null;
  lastSuccessAgeSeconds: number | null;
  staleAfterHours: number;
  testedAt: string | null;
  retryAt: string | null;
  hardBlocked: boolean;
  detail: string | null;
  lastDenialAt: string | null;
  lastDenialState: string | null;
  lastDenialStatusCode: number | null;
  lastDenialDetail: string | null;
  /** Free allowance of this endpoint per `quotaPeriod`; null = no published cap. */
  quota: number | null;
  quotaPeriod: string | null;
  resetTimezone: string | null;
  requestWeight: number | null;
  quotaWindowStart: string | null;
  /** Credits spent in the current window; null when untracked or no quota. */
  quotaUsed: number | null;
  quotaRemaining: number | null;
  /** remaining / quota in 0..1. */
  quotaHeadroom: number | null;
  /** Why headroom is null (no quota, untracked period, ...). */
  headroomReason: string | null;
};

/** One usage-ledger window from `GET /sources/usage`. */
export type UsageWindow = {
  provider: string;
  endpoint: string;
  windowKind: string;
  windowStart: string;
  windowEnd: string;
  credits: number;
  requests: number;
};

/** One row of `GET /sources/status` (the day/month spend view). */
export type SourceStatusRow = {
  provider: string;
  endpoint: string;
  rawState: string;
  state: DataState;
  hardBlocked: boolean;
  quota: number | null;
  quotaPeriod: string | null;
  lastSuccessAt: string | null;
  retryAt: string | null;
  dayCredits: number;
  monthCredits: number;
};

export type GateFinding = {
  provider: string;
  endpoint: string;
  severity: "violation" | "warning";
  code: string;
  message: string;
};

/** `GET /verification/zero-cost-gate`. The API reports credits and findings, not dollars. */
export type ZeroCostGate = {
  passed: boolean;
  checked: number;
  freeAvailable: number;
  disabledOk: number;
  gatedUnverified: number;
  violationCount: number;
  warningCount: number;
  warningsByCode: Record<string, number>;
  findings: readonly GateFinding[];
};

// ---------------------------------------------------------------------------------------
// Matrix
// ---------------------------------------------------------------------------------------

/** Where a coverage entity belongs. `other` = countries, peers, benchmarks, unmatched keys. */
export type EntityScope = "held" | "watchlist" | "other";
export const ENTITY_SCOPES = ["held", "watchlist", "other"] as const satisfies readonly EntityScope[];

/** The held / watchlist ticker sets used to classify security entity keys. */
export type EntityScopes = {
  held: ReadonlySet<string>;
  watchlist: ReadonlySet<string>;
};

/** One (provider, dataset, scope) cell. `state` is null when the ledger has no entry: unknown, not zero. */
export type MatrixCell = {
  scope: EntityScope;
  state: DataState | null;
  /** Ledger entries in this cell. */
  entries: number;
  /** Entries whose backend state is available. */
  available: number;
  byState: Partial<Record<DataState, number>>;
  /** Newest check across the entries. */
  lastCheckedAt: string | null;
  /** Newest checked_at of an available entry: the last verified value's age. */
  lastVerifiedAt: string | null;
  latestPeriod: string | null;
  /** Largest entry age in days (the stalest latest_period); null when no entry has a period. */
  maxAgeDays: number | null;
  expectedPeriods: number | null;
  availablePeriods: number | null;
  /** Some entry is failing/exhausted but keeps its last good observation. */
  refreshFailing: boolean;
  /** Distinct reasons, most frequent first (max 3). */
  reasons: readonly string[];
  /** Entity keys of the entries that are not available (for the drill-down). */
  gapKeys: readonly string[];
};

export type MatrixRow = {
  id: string;
  provider: string;
  providerLabel: string;
  dataset: string;
  cells: Record<EntityScope, MatrixCell>;
};

export type ProviderGroup = {
  provider: string;
  label: string;
  rows: MatrixRow[];
  /** Dataset rows the active filters hid (0 unfiltered); lets a view tell "filtered out" from "no ledger rows". */
  hiddenRows?: number;
  /** Access rows of the provider (from /sources/access). */
  access: readonly AccessRow[];
};

/** One endpoint's request budget. `limit` is null when the API publishes no cap. */
export type BudgetItem = {
  id: string;
  provider: string;
  providerLabel: string;
  endpoint: string;
  state: DataState;
  period: string | null;
  limit: number | null;
  /** null when usage is not tracked per window or no cap is known. */
  used: number | null;
  remaining: number | null;
  headroom: number | null;
  /** End of the current window (from the usage ledger); null when not reported. */
  resetsAt: string | null;
  resetTimezone: string | null;
  /** Credits per past window of the cap's period, oldest first (recorded windows only). */
  history: readonly number[];
  spentDay: number | null;
  spentMonth: number | null;
  tone: BudgetTone;
  /** Why no figure is shown. */
  note: string | null;
};

export type BudgetTone = "ok" | "low" | "exhausted" | "uncapped" | "untracked";
