import type { DataState } from "@/lib/data-state/states";

import type { BudgetUsage, CoverageCell, CoverageRow, ProviderId, SourceCoverageReport } from "./types";

/*
 * Illustrative fixture for the source-coverage view. Which endpoints are paid-only or
 * unverified follows the plan's provider table (e.g. FMP analyst estimates, Finnhub ownership
 * and Twelve Data /splits are documented premium features); the counts, dates and spend are
 * synthetic. Phase 0's real probe results replace this through `loadSourceCoverage`.
 */

const NOW = "2026-10-02T07:30:00Z";
const HELD = 24;
const WATCH = 31;

function cell(
  state: DataState,
  covered: number,
  total: number,
  extra: Partial<CoverageCell> = {},
): CoverageCell {
  const verified = covered > 0 && state !== "access_unverified" && state !== "paid_only" && state !== "not_covered";
  return {
    state,
    covered,
    total,
    lastVerifiedAt: verified ? "2026-10-01T06:00:00Z" : null,
    lastAttemptAt: state === "access_unverified" ? null : NOW,
    refreshFailing: false,
    reason: null,
    ...extra,
  };
}

const none = (state: DataState, total: number, reason: string): CoverageCell =>
  cell(state, 0, total, { reason, lastVerifiedAt: null });

type RowSeed = Omit<CoverageRow, "id" | "failedJobId"> & { failedJobId?: number | null };
const row = (seed: RowSeed): CoverageRow => ({ failedJobId: null, ...seed, id: `${seed.provider}:${seed.dataset}` });

const ROWS: CoverageRow[] = [
  row({
    provider: "alpha_vantage",
    dataset: "Company overview",
    endpoint: "OVERVIEW",
    jobType: "valuation_collect",
    held: cell("partial", 21, HELD),
    watchlist: cell("partial", 9, WATCH, { reason: "Held names are served first; 22 watchlist names wait for the next daily reset." }),
  }),
  row({
    provider: "alpha_vantage",
    dataset: "News sentiment",
    endpoint: "NEWS_SENTIMENT",
    jobType: null,
    held: none("access_unverified", HELD, "Not yet probed with the free key."),
    watchlist: none("access_unverified", WATCH, "Not yet probed with the free key."),
  }),
  row({
    provider: "eodhd",
    dataset: "End-of-day bars (1 year)",
    endpoint: "/eod/{symbol}",
    jobType: "price_fallback",
    held: cell("verified", 3, 3),
    watchlist: cell("quota_exhausted", 4, 11, { reason: "20 of 20 calls used today; resets 00:00 UTC.", refreshFailing: true }),
  }),
  row({
    provider: "world_bank",
    dataset: "Country indicators",
    endpoint: "/v2/country/{iso}/indicator/{id}",
    jobType: "macro_world_bank",
    held: cell("verified", 14, 14),
    watchlist: cell("partial", 22, 26, { reason: "4 countries lack the latest annual release." }),
  }),
  row({
    provider: "oecd",
    dataset: "Labour, prices and productivity",
    endpoint: "SDMX dataflows",
    jobType: "macro_oecd",
    held: cell("partial", 9, 14, { reason: "5 non-OECD countries are not covered." }),
    watchlist: cell("partial", 17, 26, { reason: "9 non-OECD countries are not covered." }),
  }),
  row({
    provider: "fmp",
    dataset: "Company profile",
    endpoint: "/stable/profile",
    jobType: null,
    held: cell("partial", 13, HELD, { reason: "Free key returns US-listed symbols only." }),
    watchlist: cell("partial", 14, WATCH, { reason: "Free key returns US-listed symbols only." }),
  }),
  row({
    provider: "fmp",
    dataset: "Analyst estimates",
    endpoint: "/stable/analyst-estimates",
    jobType: null,
    held: none("paid_only", HELD, "HTTP 402: premium subscription required. Not retried."),
    watchlist: none("paid_only", WATCH, "HTTP 402: premium subscription required. Not retried."),
  }),
  row({
    provider: "diffbot",
    dataset: "Organization relationships",
    endpoint: "Knowledge Graph (organization)",
    jobType: "graph_diffbot",
    held: cell("partial", 12, HELD, { reason: "Monthly credits cover the oldest-checked names first." }),
    watchlist: none("not_covered", WATCH, "Held names take priority; watchlist waits for spare credits."),
  }),
  row({
    provider: "people_data_labs",
    dataset: "Company enrichment (exceptions only)",
    endpoint: "/company/enrich",
    jobType: "identity_pdl",
    held: cell("verified", 2, 2, { reason: "Two unresolved identities; no contact data requested." }),
    watchlist: none("not_covered", WATCH, "Spent only on held identity conflicts."),
  }),
  row({
    provider: "epo_ops",
    dataset: "Patent families and legal status",
    endpoint: "published-data/search",
    jobType: "patents_epo",
    held: cell("stale", 6, 8, { reason: "Weekly refresh missed; showing last verified families.", lastVerifiedAt: "2026-09-18T06:00:00Z", refreshFailing: true }),
    watchlist: none("not_covered", WATCH, "Selected issuers only."),
    failedJobId: 4182,
  }),
  row({
    provider: "finnhub",
    dataset: "Earnings calendar and basic metrics",
    endpoint: "/calendar/earnings, /stock/metric",
    jobType: "earnings_check",
    held: cell("verified", 24, 24),
    watchlist: cell("verified", 31, 31),
  }),
  row({
    provider: "finnhub",
    dataset: "Institutional ownership, ETF holdings",
    endpoint: "/institutional/ownership, /etf/holdings",
    jobType: null,
    held: none("paid_only", HELD, "HTTP 403: \"You don't have access to this resource.\""),
    watchlist: none("paid_only", WATCH, "HTTP 403: \"You don't have access to this resource.\""),
  }),
  row({
    provider: "twelve_data",
    dataset: "Quotes and FX cross-check",
    endpoint: "/quote, /exchange_rate",
    jobType: "price_crosscheck",
    held: cell("disputed", 22, HELD, { reason: "2 listings differ from the primary close beyond 0.5%." }),
    watchlist: cell("not_covered", 0, WATCH, { reason: "Global trial symbols are not accessible on the free key." }),
  }),
  row({
    provider: "twelve_data",
    dataset: "Dividends and splits",
    endpoint: "/dividends, /splits",
    jobType: null,
    held: none("paid_only", HELD, "Paid-tier endpoint. Corporate actions come from issuers and the broker ledger."),
    watchlist: none("paid_only", WATCH, "Paid-tier endpoint."),
  }),
  row({
    provider: "bis",
    dataset: "Credit, debt service, banking claims",
    endpoint: "SDMX v2 data/{dataflow}",
    jobType: "macro_bis",
    held: cell("verified", 14, 14),
    watchlist: cell("verified", 26, 26),
  }),
  row({
    provider: "ecb",
    dataset: "Money, lending rates, yield curves",
    endpoint: "data-api.ecb.europa.eu",
    jobType: "macro_ecb",
    held: cell("verified", 3, 3),
    watchlist: cell("verified", 3, 3),
  }),
  row({
    provider: "un_comtrade",
    dataset: "Bilateral product trade",
    endpoint: "/data/v1/get/C/A/HS",
    jobType: "macro_comtrade",
    held: cell("partial", 8, 14, { reason: "Slices fetched for countries with a mapped exposure only." }),
    watchlist: none("not_covered", 26, "No mapped exposure; not a bulk mirror."),
  }),
];

const seq = (n: number, f: (i: number) => number): number[] => Array.from({ length: n }, (_, i) => f(i));

const BUDGETS: BudgetUsage[] = [
  {
    provider: "alpha_vantage", unit: "calls", period: "day", limit: 25, used: 22, reserve: 3, deferred: 22,
    resetsAt: "2026-10-03T00:00:00Z", history: seq(14, (i) => 18 + ((i * 7) % 8)), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "eodhd", unit: "calls", period: "day", limit: 20, used: 20, reserve: 2, deferred: 7,
    resetsAt: "2026-10-03T00:00:00Z", history: seq(14, (i) => 9 + ((i * 5) % 12)), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "fmp", unit: "calls", period: "day", limit: 250, used: 31, reserve: 25, deferred: 0,
    resetsAt: "2026-10-03T00:00:00Z", history: seq(14, (i) => 20 + ((i * 3) % 18)), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "diffbot", unit: "credits", period: "month", limit: 10000, used: 6150, reserve: 1000, deferred: 0,
    resetsAt: "2026-11-01T00:00:00Z", history: seq(14, (i) => 3000 + i * 240), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "people_data_labs", unit: "credits", period: "month", limit: 100, used: 4, reserve: 10, deferred: 0,
    resetsAt: "2026-11-01T00:00:00Z", history: seq(14, (i) => Math.min(4, Math.floor(i / 3))), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "epo_ops", unit: "GB", period: "week", limit: 4, used: 0.6, reserve: 0.4, deferred: 0,
    resetsAt: "2026-10-05T00:00:00Z", history: seq(14, (i) => 0.3 + ((i * 3) % 7) / 10), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "un_comtrade", unit: "calls", period: "day", limit: 500, used: 118, reserve: 50, deferred: 0,
    resetsAt: "2026-10-03T00:00:00Z", history: seq(14, (i) => 60 + ((i * 17) % 120)), overageEvents: 0, accessState: "verified",
  },
  {
    provider: "finnhub", unit: "calls", period: "minute", limit: null, used: 38, reserve: 0, deferred: 0,
    resetsAt: null, history: seq(14, (i) => 20 + ((i * 11) % 30)), overageEvents: 0, accessState: "access_unverified",
  },
  {
    provider: "twelve_data", unit: "credits", period: "day", limit: null, used: 6, reserve: 0, deferred: 0,
    resetsAt: null, history: seq(14, (i) => 2 + (i % 5)), overageEvents: 0, accessState: "access_unverified",
  },
  ...(["world_bank", "oecd", "bis", "ecb"] as ProviderId[]).map<BudgetUsage>((provider, i) => ({
    provider, unit: "calls", period: "day", limit: null, used: 12 + i * 9, reserve: 0, deferred: 0,
    resetsAt: null, history: seq(14, (j) => 6 + ((j * (i + 2)) % 14)), overageEvents: 0, accessState: "verified",
  })),
];

export const SOURCE_COVERAGE_FIXTURE: SourceCoverageReport = {
  generatedAt: NOW,
  rows: ROWS,
  budgets: BUDGETS,
  spendUsd: 0,
};
