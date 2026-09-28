import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/*
 * Typed calls for the Budget config and the cost summary. The spend-limit fields of
 * `BudgetConfig` and `GET /costs/summary` are newer than the generated schema, so they are
 * typed here from the backend's models (apps/backend/src/steward/config/budget.py,
 * apps/backend/src/api/budget_routes.py) until `npm run gen:api` picks them up.
 */

type Result<T> = { data?: T; error?: unknown; response: Response };

export type ModelPrice = components["schemas"]["ModelPrice"];
export type AtCap = "alert_only" | "pause_noncritical" | "downgrade_noncritical";

/** Every job type the worker runs (steward/jobs/registry.py). */
export const JOB_TYPES = [
  "portfolio_sync",
  "earnings_check",
  "thesis_verdicts",
  "earnings_calendar",
  "earnings_calendar_full",
  "earnings_due",
  "fact_reconcile",
  "reference_data",
  "price_history",
  "holiday_refresh",
  "macro_collect",
  "event_escalation",
  "monitor",
  "news_ingest",
  "daily_digest",
  "weekly_digest",
  "research",
  "ownership_13f",
  "etf_look_through",
  "monthly_review",
  "chat",
  "sec_xbrl",
  "valuation_refresh",
] as const;
export type JobType = (typeof JOB_TYPES)[number];

export type BudgetConfig = {
  schema_version: 1;
  prices?: ModelPrice[];
  web_search_per_1000?: number;
  cache_write_multiplier?: number;
  cache_read_multiplier?: number;
  batch_multiplier?: number;
  monthly_cap_usd?: number | null;
  warn_at_fraction?: number;
  at_cap?: AtCap;
  downgrade_model?: string;
  critical_job_types?: JobType[];
  per_job_cap_usd?: Partial<Record<JobType, number>>;
  default_job_cap_usd?: number | null;
};

export type BudgetConfigVersion = {
  id: number;
  created_at: string;
  note: string | null;
  body: BudgetConfig;
};

export type CostSummary = {
  month: string;
  total_usd: number;
  cap_usd: number | null;
  fraction: number | null;
  warn_at_fraction: number;
  at_cap: AtCap;
  by_job_type: {
    job_type: string;
    runs: number;
    cost_usd: number;
    job_cap_usd: number | null;
    critical: boolean;
  }[];
  by_model: { model: string; steps: number; cost_usd: number }[];
};

type GeneratedBudget = components["schemas"]["BudgetConfig"];

const api = () => getBrowserApiClient();

/** The same client, for a path the generated schema does not list yet. */
type UntypedGet = {
  GET: (path: string, init?: { params?: { query?: Record<string, string> } }) => Promise<Result<unknown>>;
};

export const budgetApi = {
  load: () => api().GET("/config/budget") as Promise<Result<BudgetConfigVersion>>,
  save: (body: BudgetConfig, query: { note: string | null }) =>
    api().PUT("/config/budget", {
      body: body as GeneratedBudget,
      params: { query },
    }) as Promise<Result<BudgetConfigVersion>>,
  validate: (body: BudgetConfig) =>
    api().POST("/config/budget/validate", {
      body: body as GeneratedBudget,
    }) as Promise<Result<BudgetConfig>>,
  costSummary: (month: string) =>
    (api() as unknown as UntypedGet).GET("/costs/summary", {
      params: { query: { month } },
    }) as Promise<Result<CostSummary>>,
};
