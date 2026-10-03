import type { components } from "@/lib/api/schema";
import type { DataState } from "@/lib/data-state/states";

/*
 * Pure logic over GET /portfolio/data-context (typed in the OpenAPI). Decimals arrive as
 * number | string. A value that does not parse is null (never 0), and every ratio is read
 * from its Share so the denominator travels with the percentage.
 */

type S = components["schemas"];
export type PortfolioDataContext = S["PortfolioDataContext"];
export type Share = S["Share"];
export type PortfolioConcentration = S["PortfolioConcentration"];
export type MacroSensitivity = S["MacroSensitivity"];
export type AdjustedReturnCoverage = S["AdjustedReturnCoverage"];
export type FundOverlapReport = S["FundOverlapReport"];
export type ContextGap = S["Gap"];

export function dec(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export type ShareView = {
  /** 0..1; null when the denominator is zero or missing (the ratio is then undefined). */
  ratio: number | null;
  numerator: number | null;
  denominator: number | null;
  basis: string;
};

export function shareView(share: Share | null | undefined): ShareView | null {
  if (!share) return null;
  const numerator = dec(share.numerator);
  const denominator = dec(share.denominator);
  let ratio = dec(share.ratio);
  if (ratio === null && numerator !== null && denominator !== null && denominator > 0) ratio = numerator / denominator;
  if (denominator === null || denominator <= 0) ratio = null;
  return { ratio, numerator, denominator, basis: share.denominator_basis };
}

/** "12.3%", or "n/a" when the ratio is undefined: never "0%" for a missing denominator. */
export function pctText(ratio: number | null, decimals = 1): string {
  if (ratio === null || !Number.isFinite(ratio)) return "n/a";
  return `${(ratio * 100).toFixed(decimals)}%`;
}

export type MoneyFmt = (value: number) => string;

/** "12.3% of 48,200 total portfolio value" — the denominator is always stated. */
export function shareText(share: Share | null | undefined, money: MoneyFmt): string {
  const v = shareView(share);
  if (!v) return "No denominator";
  if (v.denominator === null || v.denominator <= 0) return `n/a: ${v.basis} is zero or unknown`;
  return `${pctText(v.ratio)} of ${money(v.denominator)} ${v.basis}`;
}

/** The overall state of the context: no snapshot → not_covered, any gap or unmapped value → partial. */
export function contextState(ctx: PortfolioDataContext): { state: DataState; reason: string | null } {
  if (ctx.snapshot_date === null || ctx.position_count === 0) {
    return { state: "not_covered", reason: ctx.gaps[0]?.reason ?? "No position snapshot exists." };
  }
  if (ctx.gaps.length > 0) return { state: "partial", reason: ctx.gaps.map((g) => g.reason).join(" ") };
  const identity = shareView(ctx.identity_coverage)?.ratio;
  if (identity !== null && identity !== undefined && identity < 1) {
    return { state: "partial", reason: "Not every holding has a confirmed issuer link." };
  }
  return { state: "verified", reason: null };
}

/** Gap domains (backend `Gap.domain`) that bear on each overview panel. */
export const PANEL_GAP_DOMAINS = {
  concentration: ["exposure_mapping", "identity"],
  sensitivities: ["macro_sensitivities", "exposure_mapping", "identity"],
  returns: ["identity"],
  overlap: ["fund_overlap"],
} as const satisfies Record<string, readonly string[]>;

/**
 * One panel's own state. The section-wide `contextState` is partial when any gap exists, which
 * would label e.g. the fund-overlap panel "partial" because the exposure mapping is missing; and
 * a panel with nothing to draw must never read "verified". A panel is judged by the gaps of its
 * own domains, and by whether it has a value at all.
 */
export function panelState(
  ctx: PortfolioDataContext,
  domains: readonly string[],
  hasValue: boolean,
): { state: DataState; reason: string | null } {
  const overall = contextState(ctx);
  if (overall.state === "not_covered") return overall;
  const gaps = ctx.gaps.filter((g) => domains.includes(g.domain));
  if (gaps.length > 0) return { state: hasValue ? "partial" : "not_covered", reason: gaps.map((g) => g.reason).join(" ") };
  if (!hasValue) return { state: "not_covered", reason: "The API returned no figures for this panel." };
  if (domains.includes("identity")) {
    const identity = shareView(ctx.identity_coverage)?.ratio;
    if (identity !== null && identity !== undefined && identity < 1) return { state: "partial", reason: "Not every holding has a confirmed issuer link." };
  }
  return { state: "verified", reason: null };
}

export type ConcentrationRowView = {
  key: string;
  hsRevision: string | null;
  exposed: number | null;
  ofPortfolio: number | null;
  ofMapped: number | null;
  reported: number | null;
  inferred: number | null;
  holdings: number;
};

/** Top rows by exposed value. `by_basis` keys: "reported" is reported, everything else inferred. */
export function concentrationRows(conc: PortfolioConcentration | null | undefined, limit = 6): ConcentrationRowView[] {
  if (!conc) return [];
  const rows = conc.rows.map((r) => {
    let reported = 0;
    let inferred = 0;
    let parsed = true;
    for (const [basis, raw] of Object.entries(r.by_basis)) {
      const v = dec(raw);
      if (v === null) {
        parsed = false;
        continue;
      }
      if (basis === "reported") reported += v;
      else inferred += v;
    }
    return {
      key: r.key,
      hsRevision: r.hs_revision ?? null,
      exposed: dec(r.exposed_value),
      ofPortfolio: shareView(r.share_of_portfolio)?.ratio ?? null,
      ofMapped: shareView(r.share_of_mapped)?.ratio ?? null,
      reported: parsed ? reported : null,
      inferred: parsed ? inferred : null,
      holdings: r.holdings,
    };
  });
  rows.sort((a, b) => (b.exposed ?? -Infinity) - (a.exposed ?? -Infinity) || a.key.localeCompare(b.key));
  return rows.slice(0, limit);
}

export type SensitivityBasisView = { coverage: ShareView | null; weightedChange: number | null; geographies: readonly string[] };
export type SensitivityRowView = {
  id: string;
  seriesId: string;
  source: string;
  unit: string;
  reported: SensitivityBasisView;
  inferred: SensitivityBasisView;
  periodMin: string | null;
  periodMax: string | null;
};

export function sensitivityRows(list: readonly MacroSensitivity[]): SensitivityRowView[] {
  const basis = (b: MacroSensitivity["reported"]): SensitivityBasisView => ({
    coverage: shareView(b.coverage),
    weightedChange: dec(b.weighted_change),
    geographies: b.geographies,
  });
  return list.map((s) => ({
    id: `${s.series_id}|${s.source}|${s.unit}`,
    seriesId: s.series_id,
    source: s.source,
    unit: s.unit,
    reported: basis(s.reported),
    inferred: basis(s.inferred),
    periodMin: s.latest_period_min,
    periodMax: s.latest_period_max,
  }));
}

export const RETURN_BASIS_LABEL: Readonly<Record<string, string>> = {
  total_return: "Total return",
  price_adjusted: "Price, split/dividend adjusted",
  price_raw: "Price, unadjusted",
};

export type ReturnCoverageRow = {
  basis: string;
  label: string;
  complete: ShareView | null;
  partialValue: number | null;
  missingValue: number | null;
  completeCount: number;
  partialCount: number;
  missingCount: number;
};

export function returnCoverageRows(cov: AdjustedReturnCoverage | null | undefined): ReturnCoverageRow[] {
  if (!cov) return [];
  return cov.bases.map((b) => ({
    basis: b.adjustment_basis,
    label: RETURN_BASIS_LABEL[b.adjustment_basis] ?? b.adjustment_basis,
    complete: shareView(b.complete),
    partialValue: dec(b.partial_value),
    missingValue: dec(b.missing_value),
    completeCount: b.complete_tickers.length,
    partialCount: b.partial_tickers.length,
    missingCount: b.missing_tickers.length,
  }));
}

export type OverlapSummary = {
  funds: number;
  staleFunds: number;
  pairs: { a: string; b: string; isinA: string; isinB: string; overlap: number; shared: number; sameDate: boolean }[];
  unknownWeight: number | null;
};

/** Most overlapping pairs first; overlap is a 0..1 fraction of weight as reported by the backend. */
export function overlapSummary(report: FundOverlapReport | null | undefined, unknownWeight: number | null, limit = 4): OverlapSummary | null {
  if (!report) return null;
  const labels = new Map(report.funds.map((f) => [f.isin, f.label]));
  const pairs = [...report.pairs]
    .sort((x, y) => y.overlap - x.overlap || x.fund_a.localeCompare(y.fund_a))
    .slice(0, limit)
    .map((p) => ({
      a: labels.get(p.fund_a) ?? p.fund_a,
      isinA: p.fund_a,
      isinB: p.fund_b,
      b: labels.get(p.fund_b) ?? p.fund_b,
      overlap: p.overlap,
      shared: p.shared_issuers,
      sameDate: p.same_date,
    }));
  return { funds: report.funds.length, staleFunds: report.funds.filter((f) => f.stale).length, pairs, unknownWeight };
}
