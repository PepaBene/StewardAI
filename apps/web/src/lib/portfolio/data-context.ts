/**
 * View models for the Overview's data-context section, from `/portfolio/data-context`
 * (typed). The rules the backend enforces are kept visible here: every percentage carries
 * its denominator, mapped and unmapped value stay apart, reported and inferred sensitivities are
 * never merged, and unknown fund weight is never spread over the known positions.
 */
import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type PortfolioDataContext = Schemas["PortfolioDataContext"];

export function num(v: number | string | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export type Ratio = { numerator: number | null; denominator: number | null; ratio: number | null; basis: string };

export function ratioOf(s: Schemas["Share"] | null | undefined): Ratio | null {
  if (!s) return null;
  return { numerator: num(s.numerator), denominator: num(s.denominator), ratio: num(s.ratio), basis: s.denominator_basis };
}

// --- Concentration ----------------------------------------------------------------------

export type ConcentrationRow = {
  key: string;
  /** Share of the whole portfolio, the figure shown by default. */
  ofPortfolio: number | null;
  /** Share of the mapped value only. */
  ofMapped: number | null;
  value: number | null;
  holdings: number;
  /** Exposed value by how the weight was obtained. */
  byBasis: { basis: string; value: number }[];
};

export type ConcentrationView = {
  dimension: "country" | "product";
  mapped: Ratio | null;
  unmappedValue: number | null;
  unmappedTickers: string[];
  rows: ConcentrationRow[];
  hhi: number | null;
  note: string;
};

export function concentrationView(c: Schemas["PortfolioConcentration"] | null): ConcentrationView | null {
  if (!c) return null;
  const rows = c.rows
    .map((r) => ({
      key: r.key,
      ofPortfolio: num(r.share_of_portfolio.ratio),
      ofMapped: r.share_of_mapped ? num(r.share_of_mapped.ratio) : null,
      value: num(r.exposed_value),
      holdings: r.holdings,
      byBasis: Object.entries(r.by_basis)
        .map(([basis, value]) => ({ basis, value: num(value) ?? 0 }))
        .filter((b) => b.value > 0),
    }))
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || a.key.localeCompare(b.key));
  return {
    dimension: c.dimension,
    mapped: ratioOf(c.mapped),
    unmappedValue: num(c.unmapped_value),
    unmappedTickers: c.unmapped_tickers,
    rows,
    hhi: num(c.hhi),
    note: c.note,
  };
}

// --- Macro sensitivities ----------------------------------------------------------------

export type SensitivityLeg = { weightedChange: number | null; coverage: Ratio | null; geographies: string[] };
export type SensitivityRow = {
  key: string;
  seriesId: string;
  source: string;
  unit: string;
  reported: SensitivityLeg;
  inferred: SensitivityLeg;
  latestMin: string | null;
  latestMax: string | null;
};

function leg(b: Schemas["SensitivityBasis"]): SensitivityLeg {
  return { weightedChange: num(b.weighted_change), coverage: ratioOf(b.coverage), geographies: b.geographies };
}

export function sensitivityRows(list: readonly Schemas["MacroSensitivity"][]): SensitivityRow[] {
  return list
    .map((s) => ({
      key: `${s.source}|${s.series_id}`,
      seriesId: s.series_id,
      source: s.source,
      unit: s.unit,
      reported: leg(s.reported),
      inferred: leg(s.inferred),
      latestMin: s.latest_period_min,
      latestMax: s.latest_period_max,
    }))
    .sort((a, b) => a.seriesId.localeCompare(b.seriesId));
}

// --- Adjusted-return coverage -----------------------------------------------------------

export type ReturnBasisRow = {
  basis: string;
  complete: Ratio | null;
  partialValue: number | null;
  missingValue: number | null;
  partialTickers: string[];
  missingTickers: string[];
};

export function returnCoverageRows(c: Schemas["AdjustedReturnCoverage"] | null): ReturnBasisRow[] {
  if (!c) return [];
  return c.bases.map((b) => ({
    basis: b.adjustment_basis,
    complete: ratioOf(b.complete),
    partialValue: num(b.partial_value),
    missingValue: num(b.missing_value),
    partialTickers: b.partial_tickers,
    missingTickers: b.missing_tickers,
  }));
}

// --- Fund overlap -----------------------------------------------------------------------

export type FundRow = {
  isin: string;
  label: string;
  asOf: string;
  lagDays: number;
  stale: boolean;
  unknownWeight: number;
  cashOther: number;
  top10: number | null;
  effectiveN: number | null;
};

export type PairRow = { a: string; b: string; overlap: number; shared: number; sameDate: boolean };

export function fundRows(report: Schemas["FundOverlapReport"] | null): { funds: FundRow[]; pairs: PairRow[]; notes: string[] } {
  if (!report) return { funds: [], pairs: [], notes: [] };
  const label = new Map(report.funds.map((f) => [f.isin, f.label]));
  return {
    funds: report.funds.map((f) => ({
      isin: f.isin,
      label: f.label,
      asOf: f.as_of,
      lagDays: f.lag_days,
      stale: f.stale,
      unknownWeight: f.unknown_weight,
      cashOther: f.cash_derivative_other_weight,
      top10: f.top10_share,
      effectiveN: f.effective_n,
    })),
    pairs: report.pairs
      .map((p) => ({ a: label.get(p.fund_a) ?? p.fund_a, b: label.get(p.fund_b) ?? p.fund_b, overlap: p.overlap, shared: p.shared_issuers, sameDate: p.same_date }))
      .sort((x, y) => y.overlap - x.overlap),
    notes: report.notes,
  };
}

export type UnknownFundWeight = {
  unknown: number;
  notLookedThrough: number;
  residual: number;
  /** Fund value (base currency) behind the unknown share. */
  value: number;
};

export function unknownFund(u: Schemas["Unknown"] | null): UnknownFundWeight | null {
  if (!u) return null;
  return { unknown: u.unknown_fund_weight, notLookedThrough: u.not_looked_through_weight, residual: u.residual_weight, value: u.unknown_fund_value };
}

/** "12.3%" of a 0-1 fraction, "—" for null. */
export function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** Signed change in a series' unit, e.g. "+0.42". */
export function signed(v: number | null, digits = 2): string {
  if (v === null) return "No change computed";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}`;
}
