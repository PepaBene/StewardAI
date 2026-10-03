/*
 * Types of the Phase 6 "Funds and ownership" views. Typed responses come straight from the
 * generated OpenAPI schema; the three untyped responses (factsheet history, characteristics
 * audit, point-in-time look-through) have local types parsed by ./parse.ts.
 */
import type { components } from "@/lib/api/schema";

type S = components["schemas"];

export type AggregateReport = S["AggregateReport"];
export type FundAsOf = S["FundAsOf"];
export type IssuerExposure = S["IssuerExposure"];
export type ChannelWeight = S["ChannelWeight"];
export type ExposureView = S["ExposureView"];
export type ExposureBucket = S["Bucket"];
export type FundOverlapReport = S["FundOverlapReport"];
export type FundPairOverlap = S["FundPairOverlap"];
export type FundCoverage = S["FundCoverage"];
export type CoverageLedger = S["CoverageLedger"];
export type LedgerRow = S["LedgerRow"];
export type LedgerTier = LedgerRow["tier"];
export type ControlStakesOut = S["ControlStakesOut"];
export type ControlStake = S["ControlStakeOut"];
export type HoldingFundPositions = S["HoldingFundPositions"];
export type FundPosition = S["FundPosition"];
export type InsiderActivityOut = S["InsiderActivityOut"];
export type InsiderTransactionRow = S["InsiderTransactionRow"];
export type HoldingPdmrOut = S["HoldingPdmrOut"];
export type PdmrNotification = S["PdmrNotificationOut"];
export type HoldingOwnership = S["HoldingOwnership"];
export type OwnershipPeriod = S["OwnershipPeriod"];
export type TopHolder = S["TopHolder"];
export type LookThroughReport = S["LookThroughReport"];

/** One stored factsheet version of a fund (GET /funds/{isin}/factsheet, untyped upstream). */
export type FactsheetRow = {
  fundIsin: string;
  asOf: string;
  source: string;
  provider: string | null;
  shareClass: string | null;
  fundName: string | null;
  totalAssets: number | null;
  totalAssetsCurrency: string | null;
  totalAssetsAsOf: string | null;
  expenseRatio: number | null;
  benchmarkName: string | null;
  distributionPolicy: string | null;
  navBasis: string | null;
  holdingsCount: number | null;
  equityWeight: number | null;
  cashWeight: number | null;
  derivativeWeight: number | null;
  otherWeight: number | null;
  totalWeight: number | null;
  residualWeight: number | null;
  reconciles: boolean | null;
  lookthroughBasis: string | null;
  coverageNote: string | null;
  missingFields: string[];
  fetchedAt: string | null;
  fieldNamesVerified: boolean;
  extractionWarnings: string[];
};

export const AUDIT_VERDICTS = [
  "first_observation",
  "unchanged",
  "normal_update",
  "value_change",
  "methodology_change",
  "parser_drift",
] as const;
export type AuditVerdict = (typeof AUDIT_VERDICTS)[number];

/** One fund-characteristics audit observation (GET /funds/characteristics-audit). */
export type AuditRow = {
  fundKey: string;
  provider: string | null;
  asOf: string;
  fingerprint: string | null;
  basis: string | null;
  rules: Record<string, unknown>;
  /** Rules of the previous observation; only a methodology change carries them. */
  previousRules: Record<string, unknown> | null;
  /** value_change reasons that come with no per-metric comparison. */
  asOfMovedBackwards: boolean;
  restatedForSameAsOf: boolean;
  pe: number | null;
  pb: number | null;
  indexPe: number | null;
  indexPb: number | null;
  verdict: AuditVerdict;
  alert: boolean;
  needsParserReview: boolean;
  /** Metric -> {from, to, relative}; empty when the verdict carries no comparison. */
  changes: { metric: string; from: number | null; to: number | null; relative: number | null }[];
  note: string | null;
  observedAt: string | null;
};

/** A parsed list: rows that passed the shape guard and how many were rejected. */
export type Parsed<T> = { rows: T[]; rejected: number };
