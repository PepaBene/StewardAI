/**
 * Diversification/concentration (Ledger II §5.3): the standard finance concentration
 * metric, computed client-side from weights already on the page — no backend change.
 * Stated as a fact, not an alarm: a concentrated portfolio isn't wrong, it's a fact the
 * owner should be able to see without computing it by hand.
 */

/** Herfindahl-Hirschman Index: sum of squared weights (weights as fractions of 1, not %). */
export function herfindahlIndex(weights: readonly number[]): number {
  return weights.reduce((sum, w) => sum + w * w, 0);
}

/** "As concentrated as N equally-weighted positions" — 1/HHI, the standard companion figure. */
export function effectiveHoldings(hhi: number): number {
  return hhi > 0 ? 1 / hhi : 0;
}

/**
 * Where "concentrated" sits on a 0–1 scale for the diverging-style visual: HHI itself is a
 * fine metric but a poor 0–1 scale (it's dominated by the largest position alone); this
 * normalizes against the range a portfolio actually occupies — 1/N (perfectly even) to 1
 * (one position) — so the marker moves meaningfully across the whole scale.
 */
export function concentrationScalePosition(hhi: number, positions: number): number {
  if (positions <= 1) return 1;
  const even = 1 / positions;
  return Math.max(0, Math.min(1, (hhi - even) / (1 - even)));
}
