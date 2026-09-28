import { concentrationScalePosition } from "./concentration";

/*
 * The Overview risk radar: only axes the data can actually support, each normalised to
 * 0..1 (the rim) on a fixed, stated scale so the shape means the same thing from one day to
 * the next. 1 is always "more": more volatile, more market-sensitive, deeper drawdown, more
 * concentrated, more foreign-currency. No valuation axis: nothing in the API measures it.
 */

/** Where each axis reaches the rim. Shown to the user under the chart. */
export const RISK_SCALE = {
  /** Annualised volatility at the rim. */
  volatility: 0.3,
  /** Beta at the rim. */
  beta: 2,
  /** Maximum drawdown (as a positive fraction) at the rim. */
  drawdown: 0.4,
} as const;

export const RISK_SCALE_NOTE =
  "Rim = 30% volatility, beta 2, 40% drawdown, one single position, all value in foreign currency; centre = none.";

export type RiskAxis = {
  key: "volatility" | "beta" | "drawdown" | "concentration" | "fx";
  label: string;
  /** Normalised 0..1. */
  portfolio: number;
  /** Normalised benchmark value, when the benchmark has one for this axis. */
  benchmark: number | null;
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
};

/** The axes that have a portfolio value, in radar order (clockwise from 12 o'clock). */
export function riskAxes(input: RiskInputs): RiskAxis[] {
  const axes: RiskAxis[] = [];
  if (input.volatility !== null) {
    axes.push({
      key: "volatility",
      label: "Volatility",
      portfolio: clamp01(input.volatility / RISK_SCALE.volatility),
      benchmark: input.benchmarkVolatility == null ? null : clamp01(input.benchmarkVolatility / RISK_SCALE.volatility),
    });
  }
  if (input.beta !== null) {
    // The benchmark's beta against itself is 1 by definition.
    axes.push({ key: "beta", label: "Beta", portfolio: clamp01(input.beta / RISK_SCALE.beta), benchmark: 1 / RISK_SCALE.beta });
  }
  if (input.maxDrawdown !== null) {
    axes.push({
      key: "drawdown",
      label: "Drawdown",
      portfolio: clamp01(-input.maxDrawdown / RISK_SCALE.drawdown),
      benchmark: input.benchmarkMaxDrawdown == null ? null : clamp01(-input.benchmarkMaxDrawdown / RISK_SCALE.drawdown),
    });
  }
  if (input.hhi !== null && input.positions > 0) {
    axes.push({
      key: "concentration",
      label: "Concentration",
      portfolio: concentrationScalePosition(input.hhi, input.positions),
      benchmark: null,
    });
  }
  if (input.fxShare !== null) {
    axes.push({ key: "fx", label: "FX exposure", portfolio: clamp01(input.fxShare), benchmark: null });
  }
  return axes;
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
