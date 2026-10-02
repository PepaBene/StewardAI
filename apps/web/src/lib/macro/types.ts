import type { DataState } from "@/lib/data-state/states";

/*
 * Typed contracts for the Phase 2 macro, financial-cycle and trade views. The observation
 * shape follows the plan's versioned observation table: period, value, release time and
 * first-release versus latest-vintage values, so a revision never overwrites history.
 */

export type Vintage = "latest" | "first_release";

export type Frequency = "monthly" | "quarterly" | "annual";

export type MacroObservation = {
  /** Last day of the period the value describes, "2025-12-31". */
  period: string;
  /** The current (latest-vintage) value. */
  latest: number;
  /**
   * The value as first published. Null means the first release was not archived: the view
   * shows `vintage_unavailable` for that point rather than substituting the latest value.
   */
  firstRelease: number | null;
  /** When the first release was published. */
  releasedAt: string;
  /** When the value was last revised, if it was. */
  revisedAt: string | null;
};

export type MacroSeries = {
  id: string;
  title: string;
  provider: "World Bank" | "OECD" | "BIS" | "ECB" | "Eurostat" | "FRED" | "ČNB";
  /** ISO 3166-1 alpha-3. */
  country: string;
  countryName: string;
  unit: string;
  frequency: Frequency;
  seasonalAdjustment: "seasonally adjusted" | "not seasonally adjusted" | "not applicable";
  sourceUrl: string | null;
  /** Observations oldest first. */
  observations: readonly MacroObservation[];
  nextRelease: string | null;
  state: DataState;
  reason: string | null;
};

/** A comparable value per country for one indicator and two periods (the slopegraph input). */
export type CountryComparisonRow = {
  country: string;
  countryName: string;
  from: number | null;
  to: number | null;
};

export type CountryComparison = {
  title: string;
  unit: string;
  fromLabel: string;
  toLabel: string;
  /** Higher is listed first. */
  higherIsRanked: "first";
  rows: readonly CountryComparisonRow[];
  provenance: { source: string; sourceUrl: string | null; asOf: string; state: DataState; reason: string | null };
};

export type TradeFlow = {
  year: number;
  reporter: string;
  partner: string;
  /** Trade value in US dollars. */
  valueUsd: number;
};

export type TradeSlice = {
  reporterName: string;
  flow: "exports" | "imports";
  /** HS code and revision, e.g. "HS 8486" and "HS 2022". */
  product: string;
  productName: string;
  hsRevision: string;
  /** Flows by year and partner (mapped partners only). */
  flows: readonly TradeFlow[];
  /** Value reported by the reporter that no partner account explains, by year (USD). */
  unmappedUsd: Readonly<Record<number, number>>;
  valueUnit: "USD";
  sourceUrl: string | null;
};

export type ExposureKind = "reported" | "estimated" | "user_assigned";

export type ExposureWeight = {
  /** Company segment or product line the weight belongs to. */
  segment: string;
  /** Country (or "HS 8486" style product key) the segment's revenue/cost is tied to. */
  target: string;
  targetName: string;
  /** Fraction of the holding's total (0..1). */
  weight: number;
  kind: ExposureKind;
  /** Where the weight comes from: a filing note, an estimate method, an owner decision. */
  evidence: string;
  evidenceUrl: string | null;
};

export type HoldingExposureMap = {
  ticker: string;
  name: string;
  weights: readonly ExposureWeight[];
  /** The fiscal period the reported weights describe. */
  period: string;
};
