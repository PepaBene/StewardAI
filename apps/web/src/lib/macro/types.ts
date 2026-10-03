import type { DataState } from "@/lib/data-state/states";

/*
 * Domain types of the macro, financial-cycle and trade views, built from what the backend
 * returns (apps/backend/src/api/macro_routes.py, research_context_routes.py,
 * data_context_routes.py). The API returns the latest point of each series with its
 * previous period and 5/10-year range, not a vintage history, so there is no first-release
 * series type: nothing is modelled that the API does not send. Decimals arrive as numbers or
 * strings and are parsed into `number | null`, where null is "missing", never zero.
 */

export type ExposureBasis = "reported" | "estimated" | "user_assigned";
/** A basis string the backend sent that this client does not know: shown as such, never relabelled. */
export type BasisLabel = ExposureBasis | "unclassified";

export type Range = { low: number; high: number };

/** One country series at its latest period known by the as-of time (GET /macro/context/{ticker}). */
export type IndicatorPoint = {
  seriesId: string;
  source: string;
  /** Geography as stored (ISO alpha-2 by convention). */
  geography: string;
  unit: string;
  /** Last day of the period the value describes. */
  period: string;
  value: number | null;
  releaseAt: string | null;
  knownAt: string;
  sourceUrl: string;
  previousPeriod: string | null;
  previousValue: number | null;
  fiveYear: Range | null;
  tenYear: Range | null;
  state: DataState;
  /** Why the state is not simply verified, in the backend's words where it gave any. */
  stateReason: string | null;
};

export type CoverageRow = {
  provider: string;
  dataset: string;
  geography: string;
  state: DataState;
  rawState: string;
  reason: string | null;
  checkedAt: string;
};

export type EffectRow = {
  ticker: string;
  issuerId: number;
  segment: string;
  basis: BasisLabel;
  weight: number | null;
  geography: string | null;
  hsCode: string | null;
  /** 1 minus the mapped weight of this dimension: the share nothing explains. */
  unknownWeight: number | null;
  explanation: string;
  dimension: string;
  relatedSeries: readonly string[];
};

export type SeriesEffect = {
  seriesId: string;
  source: string;
  unit: string;
  weightedChange: number | null;
  /** Share of the issuer the contributing geographies represent; the rest is unknown. */
  coveredWeight: number | null;
  geographies: readonly string[];
};

/** Latest value of a series for any geography the store holds (the cross-country comparison input). */
export type ComparisonPoint = {
  seriesId: string;
  source: string;
  geography: string;
  unit: string;
  period: string;
  value: number | null;
  releaseAt: string | null;
  knownAt: string;
  sourceUrl: string;
};

export type HoldingMacroContext = {
  asOf: string;
  observations: readonly IndicatorPoint[];
  coverage: readonly CoverageRow[];
  effects: readonly EffectRow[];
  comparisons: readonly ComparisonPoint[];
  /** No exposure mapping was known by the as-of time, so no country can be attributed. */
  exposureUnknown: boolean;
  seriesEffects: readonly SeriesEffect[];
};

// ---------------------------------------------------------------------------------------
// Portfolio country exposure
// ---------------------------------------------------------------------------------------

export type Share = {
  numerator: number | null;
  denominator: number | null;
  ratio: number | null;
  denominatorBasis: string;
};

export type CountryExposureRow = {
  /** The geography key as stored: alpha-2, "EU" or something unrecognised. */
  key: string;
  exposedValue: number | null;
  shareOfPortfolio: Share;
  shareOfMapped: Share | null;
  /** Exposed value by stored basis (reported / estimated / user_assigned). */
  byBasis: Readonly<Record<string, number>>;
  holdings: number;
};

export type PortfolioConcentration = {
  mapped: Share;
  unmappedValue: number | null;
  unmappedTickers: readonly string[];
  rows: readonly CountryExposureRow[];
  hhi: number | null;
  hhiDenominator: number | null;
  note: string;
};

export type PortfolioGap = { domain: string; state: string; reason: string };

export type PortfolioCountryContext = {
  snapshotDate: string | null;
  knownAsOf: string;
  baseCurrency: string | null;
  totalValue: number | null;
  positionCount: number;
  country: PortfolioConcentration | null;
  product: PortfolioConcentration | null;
  gaps: readonly PortfolioGap[];
};

/** One line of GET /macro/coverage/portfolio `by_exposure` (an untyped object in the OpenAPI). */
export type CoverageExposure = {
  exposureType: "geography" | "hs_product";
  exposureKey: string;
  hsRevision: string | null;
  basis: BasisLabel;
  mappedValue: number | null;
  holdingCount: number | null;
  latestPeriod: string | null;
  availableSeries: number | null;
};

export type PortfolioMacroCoverage = {
  asOf: string;
  baseCurrency: string;
  totalValue: number | null;
  mappedValue: number | null;
  unknownValue: number | null;
  mappedWeight: number | null;
  unknownWeight: number | null;
  byExposure: readonly CoverageExposure[];
  gaps: readonly string[];
};

// ---------------------------------------------------------------------------------------
// Exposure mappings
// ---------------------------------------------------------------------------------------

export type ExposureMapping = {
  id: number;
  issuerId: number;
  segment: string;
  geography: string | null;
  hsRevision: string | null;
  hsCode: string | null;
  basis: BasisLabel;
  /** Fraction 0..1. */
  weight: number | null;
  validFrom: string;
  validTo: string | null;
};

// ---------------------------------------------------------------------------------------
// Trade and research context
// ---------------------------------------------------------------------------------------

export type TradeContext = {
  reporter: string;
  hsCode: string;
  hsRevision: string;
  partner: string | null;
  periods: number;
  latestPeriod: string | null;
  /** Latest-period value by flow ("export" / "import"), in `currency`. */
  latestByFlow: Readonly<Record<string, number>>;
  currency: string;
  metrics: Readonly<Record<string, number | null>>;
  sourceUrl: string | null;
  knownAt: string | null;
  notice: string;
};

export type ClaimDomain = "macro" | "trade" | "financial" | "forward" | "company_research" | "business_network" | "patents";

export type Claim = {
  claimId: string;
  domain: ClaimDomain;
  subject: string;
  metric: string;
  label: string;
  value: number | null;
  textValue: string | null;
  unit: string | null;
  currency: string | null;
  period: string | null;
  knownAsOf: string;
  source: string;
  provenance: string;
  sourceUrl: string | null;
  note: string | null;
};

export type ResearchContext = {
  scope: "country" | "industry" | "company";
  subject: string;
  asOf: string;
  claims: readonly Claim[];
  gaps: readonly { domain: string; reason: string }[];
  method: string;
};

/** A trade claim whose natural key (reporter, partner, flow, revision, HS code, period) was read from its claim id. */
export type TradeRow = {
  claim: Claim;
  reporter: string;
  partner: string;
  flow: string;
  hsRevision: string;
  hsCode: string;
  period: string;
  valueUsd: number | null;
};
