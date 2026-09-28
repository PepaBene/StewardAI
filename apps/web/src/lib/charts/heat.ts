/*
 * Diverging heat buckets for heatmaps and treemaps: three steps each side of a neutral
 * midpoint, matching the heat-down-strong .. heat-up-strong tokens.
 */

import { isFiniteNumber } from "./scale";

export type HeatLevel = "down-strong" | "down" | "down-soft" | "flat" | "up-soft" | "up" | "up-strong";

/** Levels in scale order, most negative first (for legends). */
export const HEAT_LEVELS: readonly HeatLevel[] = [
  "down-strong",
  "down",
  "down-soft",
  "flat",
  "up-soft",
  "up",
  "up-strong",
];

/**
 * The bucket for `value` on a scale saturating at ±`cap`: |value| up to ⅓ cap is soft, up to
 * ⅔ is the middle step, beyond is strong. Exactly zero is flat. Null/non-finite -> null
 * (render as an empty cell, never as a colour).
 */
export function heatLevel(value: number | null | undefined, cap: number): HeatLevel | null {
  if (!isFiniteNumber(value)) return null;
  if (value === 0 || !(cap > 0)) return value === 0 ? "flat" : value > 0 ? "up" : "down";
  const m = Math.abs(value) / cap;
  const step = m <= 1 / 3 ? "soft" : m <= 2 / 3 ? "mid" : "strong";
  const side = value > 0 ? "up" : "down";
  return step === "mid" ? side : (`${side}-${step}` as HeatLevel);
}

/** Strong steps are saturated fills that need reversed (light) text on top. */
export function heatNeedsLightText(level: HeatLevel | null): boolean {
  return level === "up-strong" || level === "down-strong";
}
