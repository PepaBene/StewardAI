/**
 * View models for the Funds page, from `/funds/overlap`, `/funds/aggregate`,
 * `/funds/look-through` and `/funds/{isin}/factsheet`. The rules the backend enforces stay
 * visible: weights are shares of total portfolio value (the denominator is always named), a
 * fund's unknown weight is its own bucket and is never redistributed, and a missing figure is
 * null, shown as "Unknown" or "Not published", never 0.
 */
import { arr, isRecord, num, recs, str, type Rec } from "@/lib/parse-untyped";

const strings = (v: unknown): string[] => arr(v).filter((x): x is string => typeof x === "string");

// --- Overlap ------------------------------------------------------------------------------

export type FundCoverage = {
  isin: string;
  label: string;
  asOf: string | null;
  lagDays: number | null;
  stale: boolean;
  equityWeight: number | null;
  cashOtherWeight: number | null;
  /** 1 - sum of the published rows; unknown, not zero. */
  unknownWeight: number | null;
  effectiveN: number | null;
  top10Share: number | null;
  issuers: number | null;
};

export type FundPair = { a: string; b: string; overlap: number | null; shared: number | null; sameDate: boolean };

export type Overlap = { asOf: string | null; funds: FundCoverage[]; pairs: FundPair[]; notes: string[] };

export function parseOverlap(payload: unknown): Overlap | null {
  if (!isRecord(payload)) return null;
  const funds = recs(payload.funds)
    .map((f): FundCoverage | null => {
      const isin = str(f.isin);
      if (!isin) return null;
      return {
        isin,
        label: str(f.label) ?? isin,
        asOf: str(f.as_of),
        lagDays: num(f.lag_days),
        stale: f.stale === true,
        equityWeight: num(f.equity_weight),
        cashOtherWeight: num(f.cash_derivative_other_weight),
        unknownWeight: num(f.unknown_weight),
        effectiveN: num(f.effective_n),
        top10Share: num(f.top10_share),
        issuers: num(f.issuers),
      };
    })
    .filter((f): f is FundCoverage => f !== null);
  const pairs = recs(payload.pairs)
    .map((p): FundPair | null => {
      const a = str(p.fund_a);
      const b = str(p.fund_b);
      return a && b ? { a, b, overlap: num(p.overlap), shared: num(p.shared_issuers), sameDate: p.same_date === true } : null;
    })
    .filter((p): p is FundPair => p !== null);
  return { asOf: str(payload.as_of), funds, pairs, notes: strings(payload.notes) };
}

export type OverlapMatrix = {
  isins: string[];
  labels: string[];
  /** Symmetric; the diagonal and any pair the backend did not report are null. */
  cells: (number | null)[][];
  /** Pairs whose two snapshots are from different dates (their overlap mixes dates). */
  mixedDatePairs: number;
};

export function overlapMatrix(o: Overlap): OverlapMatrix {
  const isins = o.funds.map((f) => f.isin);
  const index = new Map(isins.map((i, n) => [i, n]));
  const cells: (number | null)[][] = isins.map(() => isins.map(() => null));
  let mixed = 0;
  for (const p of o.pairs) {
    const i = index.get(p.a);
    const j = index.get(p.b);
    if (i === undefined || j === undefined || i === j) continue;
    cells[i][j] = p.overlap;
    cells[j][i] = p.overlap;
    if (!p.sameDate) mixed += 1;
  }
  return { isins, labels: o.funds.map((f) => f.label), cells, mixedDatePairs: mixed };
}

/** Largest overlapping pairs, biggest first; pairs without a figure sort last. */
export function topPairs(o: Overlap, n = 5): FundPair[] {
  return [...o.pairs].sort((x, y) => (y.overlap ?? -1) - (x.overlap ?? -1)).slice(0, n);
}

// --- Aggregate ----------------------------------------------------------------------------

export type Bucket = { key: string; label: string; kind: string; weight: number | null };
export type ExposureView = { dimension: string; buckets: Bucket[]; reconciles: boolean | null };
export type IssuerRow = {
  key: string;
  label: string;
  weight: number | null;
  directWeight: number | null;
  fundWeight: number | null;
  overlapWeight: number | null;
  channels: number;
  sector: string | null;
  country: string | null;
};
export type FundAsOf = {
  isin: string;
  label: string;
  weight: number | null;
  status: string;
  holdingsAsOf: string | null;
  lagDays: number | null;
  unknownWeight: number | null;
  expenseRatio: number | null;
  warnings: string[];
};
export type Aggregate = {
  asOf: string | null;
  available: boolean;
  method: string | null;
  baseCurrency: string | null;
  funds: FundAsOf[];
  issuers: IssuerRow[];
  exposures: ExposureView[];
  unknownFundWeight: number | null;
  notLookedThroughWeight: number | null;
  annualFeeDrag: number | null;
  unpricedFeeWeight: number | null;
  notes: string[];
};

export function parseAggregate(payload: unknown): Aggregate | null {
  if (!isRecord(payload)) return null;
  const unknown = isRecord(payload.unknown) ? payload.unknown : null;
  const fees = isRecord(payload.fees) ? payload.fees : null;
  return {
    asOf: str(payload.as_of),
    available: payload.available === true,
    method: str(payload.method),
    baseCurrency: str(payload.base_currency),
    funds: recs(payload.funds).map((f) => ({
      isin: str(f.isin) ?? "",
      label: str(f.label) ?? str(f.isin) ?? "Fund",
      weight: num(f.weight),
      status: str(f.status) ?? "missing",
      holdingsAsOf: str(f.holdings_as_of),
      lagDays: num(f.lag_days),
      unknownWeight: num(f.unknown_weight),
      expenseRatio: num(f.expense_ratio),
      warnings: strings(f.warnings),
    })),
    issuers: recs(payload.issuers).map((i) => ({
      key: str(i.key) ?? "",
      label: str(i.label) ?? str(i.key) ?? "Issuer",
      weight: num(i.weight),
      directWeight: num(i.direct_weight),
      fundWeight: num(i.fund_weight),
      overlapWeight: num(i.overlap_weight),
      channels: arr(i.channels).length,
      sector: str(i.sector),
      country: str(i.country),
    })),
    exposures: recs(payload.exposures).map((e: Rec) => ({
      dimension: str(e.dimension) ?? "exposure",
      reconciles: typeof e.reconciles === "boolean" ? e.reconciles : null,
      buckets: recs(e.buckets).map((b) => ({
        key: str(b.key) ?? "",
        label: str(b.label) ?? str(b.key) ?? "Other",
        kind: str(b.kind) ?? "classified",
        weight: num(b.weight),
      })),
    })),
    unknownFundWeight: unknown ? num(unknown.unknown_fund_weight) : null,
    notLookedThroughWeight: unknown ? num(unknown.not_looked_through_weight) : null,
    annualFeeDrag: fees ? num(fees.annual_drag) : null,
    unpricedFeeWeight: fees ? num(fees.unpriced_weight) : null,
    notes: strings(payload.notes),
  };
}

/**
 * Part-to-whole segments for one exposure dimension. Buckets with an unknown weight are
 * dropped from the bar, and the known rest is never rescaled.
 */
export function bucketSegments(view: ExposureView): { label: string; value: number; kind: string }[] {
  return view.buckets
    .filter((b): b is Bucket & { weight: number } => b.weight !== null && b.weight > 0)
    .sort((a, b) => b.weight - a.weight)
    .map((b) => ({ label: b.label, value: b.weight, kind: b.kind }));
}

/** Sum of the known bucket weights (100% of total portfolio value when the view reconciles). */
export function bucketTotal(view: ExposureView): number {
  return view.buckets.reduce((s, b) => s + (b.weight ?? 0), 0);
}

// --- Look-through -------------------------------------------------------------------------

export type UnderlyingRow = { key: string; name: string; weight: number | null; sector: string | null; country: string | null; viaFunds: number };
export type LookThrough = {
  asOf: string | null;
  snapshotIds: number[];
  available: boolean;
  portfolioShareLookedThrough: number | null;
  top: UnderlyingRow[];
  effectiveN: number | null;
  top10Share: number | null;
  notes: string[];
};

export function parseLookThrough(payload: unknown): LookThrough | null {
  if (!isRecord(payload) || !isRecord(payload.report)) return null;
  const r = payload.report;
  const cov = isRecord(r.coverage) ? r.coverage : null;
  const conc = isRecord(r.concentration) ? r.concentration : null;
  return {
    asOf: str(payload.as_of),
    snapshotIds: arr(payload.snapshot_ids).map(num).filter((n): n is number => n !== null),
    available: r.available === true,
    portfolioShareLookedThrough: cov ? num(cov.portfolio_share_looked_through) : null,
    top: recs(r.top_holdings).map((h) => ({
      key: str(h.key) ?? str(h.name) ?? "",
      name: str(h.name) ?? str(h.key) ?? "Unnamed",
      weight: num(h.weight),
      sector: str(h.sector),
      country: str(h.country),
      viaFunds: arr(h.via).length,
    })),
    effectiveN: conc ? num(conc.effective_n) : null,
    top10Share: conc ? num(conc.top10_share) : null,
    notes: strings(r.notes),
  };
}

// --- Factsheet ----------------------------------------------------------------------------

export type Factsheet = {
  asOf: string | null;
  source: string | null;
  shareClass: string | null;
  totalAssets: number | null;
  totalAssetsCurrency: string | null;
  /** Fraction per year (0.0007 = 0.07%). */
  expenseRatio: number | null;
  benchmark: string | null;
  distribution: string | null;
  navBasis: string | null;
  fieldNamesVerified: boolean;
  warnings: string[];
  charges: { name: string; value: number | null }[];
  note: string | null;
};

export function parseFactsheets(payload: unknown): Factsheet[] {
  return recs(payload).map((f) => ({
    asOf: str(f.as_of),
    source: str(f.source),
    shareClass: str(f.share_class),
    totalAssets: num(f.total_assets),
    totalAssetsCurrency: str(f.total_assets_currency),
    expenseRatio: num(f.expense_ratio),
    benchmark: str(f.benchmark_name),
    distribution: str(f.distribution_policy),
    navBasis: str(f.nav_basis),
    fieldNamesVerified: f.field_names_verified !== false,
    warnings: strings(f.extraction_warnings),
    charges: isRecord(f.charges) ? Object.entries(f.charges).map(([name, value]) => ({ name, value: num(value) })) : [],
    note: str(f.note),
  }));
}

/** Expense-ratio history, oldest first: rows without a ratio are skipped, never plotted as 0. */
export function expenseSeries(rows: readonly Factsheet[]): { date: string; value: number }[] {
  const seen = new Set<string>();
  const out: { date: string; value: number }[] = [];
  for (const r of rows) {
    if (!r.asOf || r.expenseRatio === null || seen.has(r.asOf)) continue;
    seen.add(r.asOf);
    out.push({ date: r.asOf.slice(0, 10), value: r.expenseRatio });
  }
  return out.sort((a, b) => a.date.localeCompare(b.date));
}

export const ISIN_RE = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;
