import type { HoldingValuation, PerShareFigure, PortfolioValuation, ValuationRatio } from "@/lib/api/client";

/*
 * Valuation display helpers and the Overview radar's Valuation axis.
 *
 * Every figure comes from GET /holdings/{ticker}/valuation or GET /portfolio/valuation;
 * nothing here estimates. A ratio the API could not compute is shown as "n/m" (not
 * meaningful: negative earnings or book value) or "—", with the API's reason alongside.
 */

/** "23.2×"; "—" for null. */
export function formatMultiple(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}×`;
}

/** "1.52%"; "—" for null. `value` is a fraction. */
export function formatYield(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

/** The ratio's value, or "n/m" when the API marked it not meaningful, else "—". */
export function ratioText(ratio: ValuationRatio | null | undefined, kind: "multiple" | "yield" = "multiple"): string {
  if (!ratio) return "—";
  if (ratio.value !== null && ratio.value !== undefined) {
    return kind === "yield" ? formatYield(ratio.value) : formatMultiple(ratio.value);
  }
  return ratio.reason?.startsWith("n/m") ? "n/m" : "—";
}

/** "USD 2.09" for a per-share figure. */
export function formatPerShare(figure: PerShareFigure | null | undefined): string {
  if (!figure) return "—";
  const digits = Math.abs(figure.value) >= 100 ? 1 : 2;
  return `${figure.currency} ${figure.value.toLocaleString("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** Short tags for a figure: annual-only, stale. */
export function figureFlags(figure: PerShareFigure | null | undefined): string[] {
  if (!figure) return [];
  const flags: string[] = [];
  if (figure.annual_only) flags.push("annual");
  if (figure.stale) flags.push("stale");
  return flags;
}

/** "FY 2025", "TTM to Jun 2026" or "as of 31 Dec 2025" for a figure's period. */
export function periodLabel(figure: PerShareFigure | null | undefined): string | null {
  if (!figure || !figure.period_end) return null;
  const end = new Date(`${figure.period_end}T00:00:00Z`);
  const month = end.toLocaleString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
  if (!figure.period_start) return `as of ${end.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })}`;
  if (figure.annual_only || figure.method.startsWith("full year") || figure.method.startsWith("latest full year")) {
    return `FY to ${month}`;
  }
  return `TTM to ${month}`;
}

/** The label of the figures' source with its basis: "SEC EDGAR XBRL (companyfacts) · US GAAP". */
export function sourceLine(v: HoldingValuation): string | null {
  if (v.fund) return `${v.fund.provider_label} fund figures`;
  const f = v.fundamentals;
  if (!f) return null;
  return [f.source_label, f.basis].filter(Boolean).join(" · ");
}

// ----------------------------------------------------------------------------- radar axis

/**
 * The Overview radar's Valuation axis scale: the rim is a P/E of 40 (the centre 0), so the
 * S&P 500 at 30× sits at 0.75 and 20× at 0.5. 1 is "more expensive", like the other axes'
 * "more risk". Portfolio and benchmark P/Es both come from /portfolio/valuation, computed
 * with iShares' method (negative earnings excluded, P/E capped at 60).
 */
export const VALUATION_SCALE = { pe: 40 } as const;

/** Below this share of invested value with a P/E the portfolio figure is not shown on the radar. */
export const VALUATION_MIN_COVERAGE = 0.5;

export const VALUATION_SCALE_NOTE = `Valuation: rim = P/E ${VALUATION_SCALE.pe}, centre = 0 (weighted harmonic mean, P/E capped at 60).`;

export type ValuationAxis = {
  key: "valuation";
  label: string;
  /** Normalised 0..1, or null when the portfolio P/E is missing or covers too little value. */
  portfolio: number | null;
  /** The benchmark's P/E on the same scale, when a benchmark figure exists. */
  benchmark: number | null;
  /** The raw P/Es behind the two values, for tooltips. */
  portfolioPe: number | null;
  benchmarkPe: number | null;
};

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

/**
 * Pure mapping of GET /portfolio/valuation to the radar's Valuation axis (0..1 on
 * VALUATION_SCALE). Portfolio is null when its P/E is null or covers less than
 * VALUATION_MIN_COVERAGE of invested value; benchmark is null without benchmark figures.
 */
export function valuationAxis(data: PortfolioValuation | null | undefined): ValuationAxis {
  const pe = data?.portfolio.pe ?? null;
  const coverage = data?.portfolio.coverage.pe ?? 0;
  const benchPe = data?.benchmark?.pe ?? null;
  const usable = pe !== null && Number.isFinite(pe) && pe > 0 && coverage >= VALUATION_MIN_COVERAGE;
  return {
    key: "valuation",
    label: "Valuation",
    portfolio: usable ? clamp01(pe / VALUATION_SCALE.pe) : null,
    benchmark: benchPe !== null && benchPe > 0 ? clamp01(benchPe / VALUATION_SCALE.pe) : null,
    portfolioPe: usable ? pe : null,
    benchmarkPe: benchPe,
  };
}

/** Relative gap of the portfolio's figure to the benchmark's: 0.1 = 10 % above. */
export function relativeTo(value: number | null | undefined, benchmark: number | null | undefined): number | null {
  if (value === null || value === undefined || benchmark === null || benchmark === undefined || benchmark === 0) return null;
  return value / benchmark - 1;
}
