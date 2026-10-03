import type { components } from "@/lib/api/schema";

/*
 * Types of the holding "data context" and "evidence" read models
 * (apps/backend/src/api/data_context_routes.py). Both responses are typed in OpenAPI; only the
 * `business_network` and `patents` dicts are untyped, so those are narrowed by ./parse.
 */

type Schemas = components["schemas"];
export type HoldingDataContext = Schemas["HoldingDataContext"];
export type EvidenceSummary = Schemas["EvidenceSummary"];
export type Gap = Schemas["Gap"];
export type Disagreement = Schemas["Disagreement"];
export type CoverageEntry = Schemas["CoverageEntry"];
export type FinancialFact = Schemas["FinancialFactOut"];
export type FinancialHistory = Schemas["FinancialHistory"];
export type TradeExposure = Schemas["TradeExposure"];
export type SectionCoverage = Schemas["SectionCoverage"];

export type EvidenceDomain = "filed-notes" | "positions" | "insider-activity" | "dependencies" | "deals" | "consensus";

/** Counts from the untyped company-graph payload; null fields are "not reported", never 0. */
export type NetworkSummary = {
  edges: number;
  /** Edge status in force at the cutoff -> count. */
  byStatus: Readonly<Record<string, number>>;
  confirmed: number;
  /** Everything not confirmed: suggested, disputed, rejected (candidates, not facts). */
  candidates: number;
  events: number;
  disclosures: number;
  limitations: readonly string[];
};

export type PatentSummary = {
  families: number;
  attributed: number;
  byLegalStatus: Readonly<Record<string, number>>;
  latestKnownAt: string | null;
  /** Rows of the trend series, or null when the response carried none in a known shape. */
  trendRows: number | null;
};
