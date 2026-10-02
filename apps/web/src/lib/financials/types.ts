import type { DataState } from "@/lib/data-state/states";

/*
 * Typed contracts for the Phase 4 views: as-filed statement history with restatement
 * lineage, cash-to-earnings walk, debt maturities, segments, the forward-data taxonomy
 * (actual / guidance / consensus / scenario / calendar estimate) and peer comparison.
 */

export type AccountingBasis = "US GAAP" | "IFRS";

export type StatementKind = "income" | "balance" | "cashflow";

export const STATEMENT_LABELS: Record<StatementKind, string> = {
  income: "Income statement",
  balance: "Balance sheet",
  cashflow: "Cash flow",
};

/** One filed value of a line item for a period; a restatement adds a later version. */
export type LineVersion = {
  version: number;
  value: number;
  /** When the filing carrying this value became public: the point-in-time cut-off. */
  filedAt: string;
  accession: string;
  /** This version restates an earlier one. */
  restates: boolean;
};

export type StatementPeriod = {
  /** "FY2025". */
  period: string;
  end: string;
  /** Oldest first; always at least one. */
  versions: readonly LineVersion[];
};

export type StatementLine = {
  id: string;
  label: string;
  statement: StatementKind;
  /** Per-share items use the currency with a "/share" unit. */
  unit: string;
  periods: readonly StatementPeriod[];
};

export type StatementSet = {
  company: string;
  ticker: string;
  basis: AccountingBasis;
  currency: string;
  /** "Fiscal year ends 31 December". */
  fiscalYearNote: string;
  scale: "units" | "thousands" | "millions" | "billions";
  lines: readonly StatementLine[];
  source: string;
  sourceUrl: string | null;
  state: DataState;
  reason: string | null;
};

/** Which version of each value a view shows. */
export type VersionMode =
  | { kind: "latest" }
  | { kind: "original" }
  /** The value as known on a date: the newest version filed on or before it. */
  | { kind: "as_of"; date: string };

export type CashWalk = {
  period: string;
  currency: string;
  netIncome: number;
  /** Non-cash items and working-capital changes between net income and operating cash flow. */
  adjustments: readonly { label: string; value: number }[];
  /** Reported cash from operations, from the filed cash-flow statement. */
  reportedCfo: number;
  capex: number;
  /** Reported or derived free cash flow, from the same filing. */
  reportedFcf: number;
};

export type DebtTranche = {
  /** Calendar year of maturity. */
  year: number;
  currency: string;
  amount: number;
};

export type DebtSchedule = {
  asOf: string;
  baseYear: number;
  tranches: readonly DebtTranche[];
  /** Total debt on the balance sheet, in the reporting currency; maturities may explain less. */
  reportedTotal: number;
  reportingCurrency: string;
  /** Approximate FX used to express non-reporting currencies in the reporting currency. */
  fxToReporting: Readonly<Record<string, number>>;
  citation: string;
};

export type SegmentRow = {
  name: string;
  revenue: number;
  priorRevenue: number | null;
  operatingProfit: number | null;
  /** How the segment's profit is defined: margins compare only when these match. */
  profitDefinition: string | null;
};

export type SegmentSet = {
  period: string;
  currency: string;
  segments: readonly SegmentRow[];
  totalRevenue: number;
  citation: string;
};

export type ForwardKind = "actual" | "company_guidance" | "owner_scenario" | "published_analyst_consensus" | "event_calendar_estimate";

export const FORWARD_LABELS: Record<ForwardKind, string> = {
  actual: "Actual (filed)",
  company_guidance: "Company guidance",
  owner_scenario: "Owner scenario",
  published_analyst_consensus: "Published analyst consensus",
  event_calendar_estimate: "Event-calendar estimate",
};

export type ForwardPoint = {
  kind: Exclude<ForwardKind, "actual">;
  metric: string;
  period: string;
  low: number | null;
  mean: number | null;
  median: number | null;
  high: number | null;
  /** Null when the provider does not say; never inferred. */
  analystCount: number | null;
  basis: string;
  currency: string;
  /** The estimate vintage: the date the snapshot describes. */
  vintage: string;
  provider: string;
  sourceUrl: string | null;
};

export type ActualPoint = { period: string; value: number };

export type EstimateSet = {
  metric: string;
  unit: string;
  actuals: readonly ActualPoint[];
  forward: readonly ForwardPoint[];
  /** Consensus snapshots of the same metric/basis/period over time, for the revision slope. */
  snapshots: readonly ForwardPoint[];
  /** Periods where no forward estimate of any kind exists. */
  missing: readonly { period: string; reason: string; state: DataState }[];
};

export type EarningsEvent = {
  metric: string;
  period: string;
  actual: number;
  consensusLow: number | null;
  consensusMean: number | null;
  consensusHigh: number | null;
  analystCount: number | null;
  currency: string;
  reportedAt: string;
  provider: string | null;
};

export type PeerMetric = { value: number | null; definition: string };

export type PeerRow = {
  name: string;
  ticker: string;
  basis: AccountingBasis;
  /** Fiscal period the figures describe. */
  period: string;
  /** Business-line / region / capitalisation match note. */
  why: string;
  metrics: Readonly<Record<string, PeerMetric>>;
};

export type PeerSet = {
  subject: PeerRow;
  peers: readonly PeerRow[];
  /** The one current definition per metric. */
  definitions: Readonly<Record<string, { label: string; definition: string; unit: "percent" | "ratio" }>>;
};
