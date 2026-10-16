/*
 * Concentration maths for the Lorenz chart (spec B31) and its gauge: cumulative weight held by
 * the largest names, the Herfindahl index and the effective number of holdings (1 / HHI).
 */

export type LorenzPoint = { x: number; y: number };

const clean = (weights: readonly number[]) => weights.filter((w) => Number.isFinite(w) && w > 0);

/**
 * Cumulative share of value (y) against cumulative share of names (x), largest names first,
 * starting at (0,0) and ending at (1,1). Weights need not sum to one. Empty input -> [].
 */
export function lorenzPoints(weights: readonly number[]): LorenzPoint[] {
  const w = clean(weights).sort((a, b) => b - a);
  const total = w.reduce((s, v) => s + v, 0);
  if (w.length === 0 || total <= 0) return [];
  const out: LorenzPoint[] = [{ x: 0, y: 0 }];
  let cum = 0;
  w.forEach((v, i) => {
    cum += v;
    out.push({ x: (i + 1) / w.length, y: cum / total });
  });
  return out;
}

/** Herfindahl-Hirschman index of the normalised weights, 0..1 (1 = one holding). */
export function hhi(weights: readonly number[]): number | null {
  const w = clean(weights);
  const total = w.reduce((s, v) => s + v, 0);
  if (w.length === 0 || total <= 0) return null;
  return w.reduce((s, v) => s + (v / total) ** 2, 0);
}

/** Effective number of holdings, 1 / HHI. */
export function effectiveHoldings(weights: readonly number[]): number | null {
  const h = hhi(weights);
  return h === null || h === 0 ? null : 1 / h;
}

/** How many of the largest names hold `share` of the value, and their exact combined share. */
export function namesForShare(weights: readonly number[], share = 0.5): { names: number; share: number } | null {
  const pts = lorenzPoints(weights);
  if (pts.length === 0) return null;
  const n = pts.length - 1;
  for (let i = 1; i < pts.length; i++) if (pts[i].y >= share - 1e-9) return { names: i, share: pts[i].y };
  return { names: n, share: 1 };
}

/** Share of value held by the top `fraction` of names (rounded up to whole names). */
export function topShare(weights: readonly number[], fraction = 0.2): { names: number; share: number } | null {
  const pts = lorenzPoints(weights);
  if (pts.length === 0) return null;
  const n = pts.length - 1;
  const k = Math.max(1, Math.ceil(n * fraction));
  return { names: k, share: pts[k].y };
}
