import type { DataState } from "@/lib/data-state/states";

/*
 * Typed contracts for the Phase 4 views, built from the real backend responses:
 * as-filed statement facts (`GET /holdings/{ticker}/financial-history`) with restatement
 * lineage and reconciliation state, the forward-data taxonomy (`/forward-data/consensus`),
 * actual-versus-consensus, peer set and peer metrics. Nothing here is a sample.
 */

// ---------------------------------------------------------------------------------------
// Statement facts (as filed)
// ---------------------------------------------------------------------------------------

export type Reconciliation = "reconciled" | "failed" | "unchecked";

/** The statements the view draws; the API calls the cash flow `cash_flow` and balance sheet `balance_sheet`. */
export type StatementKind = "income" | "balance_sheet" | "cash_flow";

export const STATEMENT_LABELS: Record<StatementKind, string> = {
  income: "Income statement",
  balance_sheet: "Balance sheet",
  cash_flow: "Cash flow",
};

export type FactMember = { axis: string; member: string };

/** One row of `financial_statement_fact`, as the API returns it. */
export type FactRow = {
  id: number;
  concept: string;
  label: string | null;
  statement: string;
  lineItem: string | null;
  periodStart: string | null;
  periodEnd: string;
  fiscalPeriod: string | null;
  basis: string | null;
  value: number | null;
  unit: string | null;
  currency: string | null;
  /** A breakdown (segment) fact: never the consolidated figure. */
  dimensional: boolean;
  members: readonly FactMember[];
  source: string | null;
  sourceUrl: string | null;
  publishedAt: string | null;
  /** When the filing carrying the value became known: the point-in-time key. */
  knownAt: string;
  revision: number | null;
  amendsFactId: number | null;
  amendsAccession: string | null;
  isAmendment: boolean;
  accession: string | null;
  form: string | null;
  reconciliation: Reconciliation;
  /** Why a check failed or did not apply (from `validation`). */
  checkNote: string | null;
  textBlock: boolean;
};

export type ParsedFacts = { rows: FactRow[]; dropped: number };

export type PeriodBasis = "annual" | "quarter";

/** One filed value of a line item for a period; a restatement adds a later version. */
export type LineVersion = {
  /** 1 for the first filing that carried the value. */
  version: number;
  value: number;
  knownAt: string;
  accession: string | null;
  form: string | null;
  /** This version amends an earlier filed fact. */
  restates: boolean;
  reconciliation: Reconciliation;
  concept: string;
  sourceUrl: string | null;
};

export type StatementPeriod = {
  /** Period end date: the axis key. */
  end: string;
  /** "FY2025" when the filing says so, else the end date. */
  label: string;
  /** Oldest first; empty when no value was filed for the period (never drawn as zero). */
  versions: readonly LineVersion[];
};

export type StatementLine = {
  id: string;
  label: string;
  statement: StatementKind;
  unit: string;
  concept: string;
  periods: readonly StatementPeriod[];
};

export type StatementView = {
  kind: StatementKind;
  basis: PeriodBasis;
  lines: readonly StatementLine[];
  periods: readonly { end: string; label: string }[];
  /** Facts behind the drawn lines, by reconciliation state. */
  counts: { reconciled: number; unchecked: number; failed: number; total: number };
};

/** Which version of each value a view shows. */
export type VersionMode = { kind: "latest" } | { kind: "original" };

export type CashWalk = {
  label: string;
  periodStart: string;
  periodEnd: string;
  /** Currency (or unit) of every figure; one unit only. */
  currency: string;
  netIncome: number;
  /** Filed non-cash lines between net income and operating cash flow. */
  adjustments: readonly { id: string; label: string; value: number }[];
  /** Reported cash from operations, from the filed cash-flow statement. */
  cfo: number;
  /** Absolute capital expenditure; null when none was filed. */
  capex: number | null;
  knownAt: string;
  accessions: readonly string[];
  source: string | null;
  sourceUrl: string | null;
};

export type DebtTranche = { key: string; label: string; amount: number };

export type LeaseFact = { id: string; label: string; value: number; periodEnd: string };

export type DebtSchedule = {
  periodEnd: string;
  currency: string;
  tranches: readonly DebtTranche[];
  /** Balance-sheet debt at the same date; null when the filing facts do not give one. */
  reportedTotal: number | null;
  /** How the reported total was built, e.g. "LongTermDebt" or "current + non-current". */
  totalBasis: string | null;
  leases: readonly LeaseFact[];
  knownAt: string;
  accession: string | null;
  source: string | null;
  sourceUrl: string | null;
};

// ---------------------------------------------------------------------------------------
// Segments
// ---------------------------------------------------------------------------------------

export type SegmentRowView = {
  name: string;
  member: string | null;
  revenue: number;
  priorRevenue: number | null;
  operatingProfit: number | null;
  /** The XBRL concept of the segment profit: margins compare only when these match. */
  profitDefinition: string | null;
};

export type SegmentSet = {
  axis: string;
  period: string;
  periodEnd: string;
  currency: string;
  segments: readonly SegmentRowView[];
  totalRevenue: number | null;
  /** Subtotal rows the backend flagged: listed, never drawn, so nothing is counted twice. */
  subtotalsLeftOut: number;
};

// ---------------------------------------------------------------------------------------
// Forward data (the estimates boundary)
// ---------------------------------------------------------------------------------------

export type ForwardKind = "company_guidance" | "owner_scenario" | "published_analyst_consensus" | "event_calendar_estimate";

export const FORWARD_KINDS: readonly ForwardKind[] = ["company_guidance", "owner_scenario", "published_analyst_consensus", "event_calendar_estimate"];

export const FORWARD_LABELS: Record<ForwardKind | "actual", string> = {
  actual: "Actual (filed)",
  company_guidance: "Company guidance",
  owner_scenario: "Owner scenario",
  published_analyst_consensus: "Published analyst consensus",
  event_calendar_estimate: "Event-calendar estimate",
};

export type ForwardObs = {
  id: number;
  kind: ForwardKind;
  provider: string | null;
  metric: string;
  basis: string | null;
  fiscalPeriod: string;
  periodEnd: string | null;
  currency: string | null;
  value: number | null;
  low: number | null;
  high: number | null;
  /** Null when the provider does not say; never inferred. */
  analystCount: number | null;
  vintage: string | null;
  publishedAt: string | null;
  knownAt: string;
  sourceUrl: string | null;
};

export type ConsensusSeries = {
  metric: string;
  fiscalPeriod: string;
  provider: string | null;
  currency: string | null;
  basis: string | null;
  /** Oldest first. */
  vintages: readonly ForwardObs[];
  revisionSincePrevious: number | null;
};

export type ForwardPayload = {
  observations: readonly ForwardObs[];
  series: readonly ConsensusSeries[];
  knownAsOf: string | null;
  dropped: number;
};

export type ActualPoint = { period: string; value: number; knownAt: string };

export type RevisionSlope = {
  period: string;
  from: { vintage: string; mean: number; analystCount: number | null };
  to: { vintage: string; mean: number; analystCount: number | null };
  snapshots: number;
  change: number;
  pct: number | null;
  days: number;
  perMonthPct: number | null;
};

export type SeriesOption = { id: string; provider: string | null; currency: string | null; basis: string | null; periods: number };

export type EstimateModel = {
  metric: string;
  /** Currency of the plotted figures; null when no observation states one. */
  currency: string | null;
  seriesOptions: readonly SeriesOption[];
  seriesId: string | null;
  periods: readonly string[];
  actuals: readonly ActualPoint[];
  /** Why actuals are not drawn on this axis (different currency or scale), if so. */
  actualsNote: string | null;
  /** Latest point per kind/provider/basis/period, in the plotted currency. */
  points: readonly ForwardObs[];
  /** Observations not plotted, with the reason. */
  unplotted: readonly { obs: ForwardObs; reason: string }[];
  revisions: readonly { period: string; slope: RevisionSlope | null; reason: string | null }[];
  /** Periods that have other kinds of figure but no consensus in the selected series. */
  consensusGaps: readonly string[];
};

export type AvcState = string;

export type ActualVsConsensus = {
  state: AvcState;
  reason: string | null;
  metric: string | null;
  fiscalPeriod: string | null;
  actual: { value: number; unit: string | null; currency: string | null; knownAt: string | null; accession: string | null; reconciled: boolean } | null;
  consensus: {
    provider: string | null;
    value: number | null;
    low: number | null;
    high: number | null;
    analystCount: number | null;
    currency: string | null;
    basis: string | null;
    vintage: string | null;
    knownAt: string | null;
  } | null;
  surprise: number | null;
  surprisePct: number | null;
};

export type IrRelease = {
  id: number;
  releaseType: string;
  sourceUrl: string;
  publishedAt: string | null;
  knownAt: string | null;
  analystCount: number | null;
  basis: string | null;
  status: string | null;
};

export type IrSource = {
  id: number;
  url: string;
  releaseType: string;
  basis: string | null;
  label: string | null;
  active: boolean;
  lastCheckedAt: string | null;
  lastStatus: string | null;
  lastError: string | null;
};

// ---------------------------------------------------------------------------------------
// Peers
// ---------------------------------------------------------------------------------------

export type PeerStatus = "suggested" | "confirmed" | "rejected";

export type PeerEntry = {
  id: number;
  peerTicker: string;
  basis: string | null;
  status: PeerStatus;
  evidenceId: number | null;
  source: string | null;
  validFrom: string | null;
  validTo: string | null;
  knownAt: string | null;
};

export type PeerMetricRow = {
  id: string;
  /** Mean of the comparable peers (the API gives no per-peer values). */
  mean: number | null;
  coveredPeers: number;
  included: readonly string[];
  excluded: readonly string[];
  excludedReasons: Readonly<Record<string, string>>;
  holdingValue: number | null;
  holdingReason: string | null;
  periodEnd: string | null;
  periodStart: string | null;
  basis: string | null;
};

export type PeerMetricsPayload = {
  confirmedPeers: readonly { peerTicker: string; basis: string | null; source: string | null }[];
  metrics: readonly PeerMetricRow[];
  excludedPeers: readonly { peer: string; reason: string }[];
  holdingPeriod: { periodStart: string | null; periodEnd: string | null; basis: string | null; unit: string | null; reconciliation: string | null; unreconciledInputs: readonly string[] } | null;
  reconciledOnly: boolean;
  formulas: Readonly<Record<string, string>>;
  universe: string | null;
  knownAsOf: string | null;
};

export type StateAndReason = { state: DataState; reason: string | null };
