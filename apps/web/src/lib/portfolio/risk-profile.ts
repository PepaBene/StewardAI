import { concentrationScalePosition } from "./concentration";
import { LOOK_THROUGH_HHI_RIM, lookThroughConcentrationPosition } from "./look-through";

/*
 * The Overview risk radar: only axes the data can actually support, each normalised to
 * 0..1 (the rim) on a fixed, stated scale so the shape means the same thing from one day to
 * the next. 1 is always "more": more volatile, more market-sensitive, deeper drawdown, more
 * concentrated, more foreign-currency.
 *
 * Extension point: the radar draws `RISK_AXIS_DEFS` in order. A new axis (e.g. valuation) is
 * one more entry: a key, a label, the scale note it adds under the chart, and a `compute`
 * that returns the normalised portfolio value (and the benchmark's when known) or null when
 * the inputs cannot support it. Extra inputs go into `RiskInputs` as optional fields.
 */

/** Where each axis reaches the rim. Shown to the user under the chart. */
export const RISK_SCALE = {
  /** Annualised volatility at the rim. */
  volatility: 0.3,
  /** Beta at the rim. */
  beta: 2,
  /** Maximum drawdown (as a positive fraction) at the rim. */
  drawdown: 0.4,
  /** Look-through HHI at the rim (as concentrated as 10 equal holdings). */
  lookThroughHhi: LOOK_THROUGH_HHI_RIM,
} as const;

export type RiskAxisKey = "volatility" | "beta" | "drawdown" | "concentration" | "fx" | (string & {});

export type RiskAxis = {
  key: RiskAxisKey;
  label: string;
  /** Normalised 0..1. */
  portfolio: number;
  /** Normalised benchmark value, when the benchmark has one for this axis. */
  benchmark: number | null;
  /** What the rim means on this axis, e.g. "30% volatility". */
  rim: string;
};

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

export type RiskInputs = {
  volatility: number | null;
  beta: number | null;
  /** ≤ 0, e.g. −0.118. */
  maxDrawdown: number | null;
  /** Herfindahl index of the invested positions and their count. */
  hhi: number | null;
  positions: number;
  /** Share of value in a currency other than the base currency, 0..1. */
  fxShare: number | null;
  benchmarkVolatility?: number | null;
  benchmarkMaxDrawdown?: number | null;
  /**
   * From the ETF look-through (GET /portfolio/look-through): HHI over the underlying
   * securities, and the foreign-currency share with the funds expanded, for the portfolio
   * and its benchmark proxy. When present they replace the position-level figures.
   */
  lookThrough?: {
    hhi: number | null;
    benchmarkHhi: number | null;
    fxShare: number | null;
    benchmarkFxShare: number | null;
  } | null;
  /**
   * From GET /portfolio/valuation via `valuationAxis()` (lib/portfolio/valuation.ts): the
   * portfolio's and the benchmark's P/E, already normalised 0..1 on the valuation scale.
   * The portfolio value is null when too little of the portfolio has a P/E.
   */
  valuation?: { portfolio: number | null; benchmark: number | null; rim: string } | null;
};

export type RiskAxisDef = {
  key: RiskAxisKey;
  label: string;
  compute: (input: RiskInputs) => { portfolio: number; benchmark: number | null; rim: string } | null;
};

const volatilityAxis: RiskAxisDef = {
  key: "volatility",
  label: "Volatility",
  compute: (i) =>
    i.volatility === null
      ? null
      : {
          portfolio: clamp01(i.volatility / RISK_SCALE.volatility),
          benchmark: i.benchmarkVolatility == null ? null : clamp01(i.benchmarkVolatility / RISK_SCALE.volatility),
          rim: "30% volatility",
        },
};

const betaAxis: RiskAxisDef = {
  key: "beta",
  label: "Beta",
  // The benchmark's beta against itself is 1 by definition.
  compute: (i) =>
    i.beta === null ? null : { portfolio: clamp01(i.beta / RISK_SCALE.beta), benchmark: 1 / RISK_SCALE.beta, rim: "beta 2" },
};

const drawdownAxis: RiskAxisDef = {
  key: "drawdown",
  label: "Drawdown",
  compute: (i) =>
    i.maxDrawdown === null
      ? null
      : {
          portfolio: clamp01(-i.maxDrawdown / RISK_SCALE.drawdown),
          benchmark: i.benchmarkMaxDrawdown == null ? null : clamp01(-i.benchmarkMaxDrawdown / RISK_SCALE.drawdown),
          rim: "40% drawdown",
        },
};

const concentrationAxis: RiskAxisDef = {
  key: "concentration",
  label: "Concentration",
  compute: (i) => {
    const lt = i.lookThrough;
    if (lt && lt.hhi !== null) {
      return {
        portfolio: lookThroughConcentrationPosition(lt.hhi),
        benchmark: lt.benchmarkHhi === null ? null : lookThroughConcentrationPosition(lt.benchmarkHhi),
        rim: "look-through HHI 0.10 (10 equal holdings)",
      };
    }
    if (i.hhi === null || i.positions <= 0) return null;
    return { portfolio: concentrationScalePosition(i.hhi, i.positions), benchmark: null, rim: "one single position" };
  },
};

const fxAxis: RiskAxisDef = {
  key: "fx",
  label: "FX exposure",
  compute: (i) => {
    const lt = i.lookThrough;
    if (lt && lt.fxShare !== null) {
      return { portfolio: clamp01(lt.fxShare), benchmark: lt.benchmarkFxShare === null ? null : clamp01(lt.benchmarkFxShare), rim: "all value in foreign currency" };
    }
    return i.fxShare === null ? null : { portfolio: clamp01(i.fxShare), benchmark: null, rim: "all value in foreign currency" };
  },
};

const valuationRadarAxis: RiskAxisDef = {
  key: "valuation",
  label: "Valuation",
  compute: (i) =>
    i.valuation?.portfolio == null
      ? null
      : { portfolio: i.valuation.portfolio, benchmark: i.valuation.benchmark, rim: i.valuation.rim },
};

/** The radar's axes, clockwise from 12 o'clock. Append to add an axis. */
export const RISK_AXIS_DEFS: readonly RiskAxisDef[] = [
  volatilityAxis,
  betaAxis,
  drawdownAxis,
  concentrationAxis,
  fxAxis,
  valuationRadarAxis,
];

/** The axes that have a portfolio value, in radar order (clockwise from 12 o'clock). */
export function riskAxes(input: RiskInputs, defs: readonly RiskAxisDef[] = RISK_AXIS_DEFS): RiskAxis[] {
  const axes: RiskAxis[] = [];
  for (const def of defs) {
    const value = def.compute(input);
    if (value) axes.push({ key: def.key, label: def.label, ...value });
  }
  return axes;
}

/** "Rim = 30% volatility, beta 2, …; centre = none." for the axes actually drawn. */
export function riskScaleNote(axes: readonly RiskAxis[]): string {
  if (axes.length === 0) return "";
  return `Rim = ${axes.map((a) => a.rim).join(", ")}; centre = none.`;
}

/** The benchmark polygon's values: null on axes where the benchmark has no value. */
export function benchmarkValues(axes: readonly RiskAxis[]): (number | null)[] | null {
  return axes.some((a) => a.benchmark !== null) ? axes.map((a) => a.benchmark) : null;
}

/**
 * Share of the weight basis held in currencies other than `base`, from the currency
 * dimension's buckets (instrument trading currency). Cash counts as base currency when the
 * account is in it. Null when there is no currency dimension.
 */
export function foreignCurrencyShare(
  buckets: readonly { bucket: string; value_base: number }[] | undefined,
  base: string,
  total: number,
): number | null {
  if (!buckets || buckets.length === 0 || !(total > 0)) return null;
  const foreign = buckets.filter((b) => b.bucket !== base && b.bucket !== "Unclassified").reduce((s, b) => s + b.value_base, 0);
  return foreign / total;
}
