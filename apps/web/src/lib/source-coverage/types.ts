import type { DataState } from "@/lib/data-state/states";

/**
 * Typed contract of the owner-only read APIs `GET /data/coverage`, `GET /sources/access`
 * and `GET /sources/usage` (plan: Concrete storage and API shape). The view consumes these
 * shapes today from a fixture; the loader in `client.ts` is the one seam to swap.
 *
 * No field carries a credential or a raw provider payload.
 */

export const PROVIDERS = [
  "alpha_vantage",
  "eodhd",
  "world_bank",
  "oecd",
  "fmp",
  "diffbot",
  "people_data_labs",
  "epo_ops",
  "finnhub",
  "twelve_data",
  "bis",
  "ecb",
  "un_comtrade",
] as const;

export type ProviderId = (typeof PROVIDERS)[number];

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  alpha_vantage: "Alpha Vantage",
  eodhd: "EODHD",
  world_bank: "World Bank",
  oecd: "OECD",
  fmp: "FMP",
  diffbot: "Diffbot",
  people_data_labs: "People Data Labs",
  epo_ops: "EPO OPS",
  finnhub: "Finnhub",
  twelve_data: "Twelve Data",
  bis: "BIS",
  ecb: "ECB",
  un_comtrade: "UN Comtrade",
};

export type EntityScope = "held" | "watchlist";

/** One cell of the matrix: a provider's dataset for the held or the watchlist universe. */
export type CoverageCell = {
  state: DataState;
  /** Entities with a usable record / the expected universe for this scope. */
  covered: number;
  total: number;
  /** Timestamp of the newest verified observation (the last-good value). */
  lastVerifiedAt: string | null;
  /** Newest refresh attempt, successful or not. */
  lastAttemptAt: string | null;
  /** A refresh is currently failing: the last verified value stays on screen. */
  refreshFailing: boolean;
  /** Why the state is not "verified": a denial message, an HTTP status, the reset time. */
  reason: string | null;
};

export type CoverageRow = {
  /** Stable key: `${provider}:${dataset}`. */
  id: string;
  provider: ProviderId;
  /** Human dataset name, e.g. "Company overview". */
  dataset: string;
  /** Endpoint path or query kind, shown in the drill-through. */
  endpoint: string;
  /** Job type that runs it; drives the Activity job-log link. Null when not scheduled yet. */
  jobType: string | null;
  /** Id of the latest failed job run, for a direct drill-through. */
  failedJobId: number | null;
  held: CoverageCell;
  watchlist: CoverageCell;
};

export type QuotaPeriod = "minute" | "day" | "week" | "month";

/** One provider's free allowance and how much of it the ledger shows spent. */
export type BudgetUsage = {
  provider: ProviderId;
  /** Unit of the allowance: "calls", "credits", "GB". */
  unit: string;
  period: QuotaPeriod;
  /** Null when no cap is published: the access state, not "unlimited", is shown. */
  limit: number | null;
  used: number;
  /** Credits held back for retries and urgent events (configurable after the shadow run). */
  reserve: number;
  /** Demand deferred to the next window because the budget was spent. */
  deferred: number;
  resetsAt: string | null;
  /** Spend per past window, oldest first (14 windows). */
  history: readonly number[];
  /** The ledger proves no paid endpoint or overage was used. */
  overageEvents: number;
  /** Access state of the provider's best endpoint, from the Phase 0 probe. */
  accessState: DataState;
};

export type SourceCoverageReport = {
  generatedAt: string;
  rows: readonly CoverageRow[];
  budgets: readonly BudgetUsage[];
  /** Total spend recorded by the usage ledger, in dollars. Must be 0. */
  spendUsd: number;
};
