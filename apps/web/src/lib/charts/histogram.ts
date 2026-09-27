/*
 * Histogram binning. Bins are half-open [x0, x1) except the last, which is closed so the
 * maximum lands in it.
 */

import { isFiniteNumber } from "./scale";

export type Bin = { x0: number; x1: number; count: number };

export type BinOptions = {
  /** Number of equal-width bins. Default: Sturges' rule, ceil(log2(n)) + 1. */
  count?: number;
  /** Explicit, ascending bin edges (overrides `count` and `domain`); n edges -> n−1 bins. */
  thresholds?: readonly number[];
  /** Range to bin over; values outside it are dropped. Default: the data's extent. */
  domain?: readonly [number, number];
};

/** Counts finite values into bins. Empty input -> []. */
export function binValues(values: readonly (number | null | undefined)[], options: BinOptions = {}): Bin[] {
  const data = values.filter(isFiniteNumber);
  if (data.length === 0) return [];

  let edges: number[];
  if (options.thresholds && options.thresholds.length >= 2) {
    edges = [...options.thresholds].sort((a, b) => a - b);
  } else {
    const [lo, hi] = options.domain ?? [Math.min(...data), Math.max(...data)];
    if (lo === hi) return [{ x0: lo, x1: hi, count: data.filter((v) => v === lo).length }];
    const count = Math.max(1, Math.floor(options.count ?? Math.ceil(Math.log2(data.length)) + 1));
    const width = (hi - lo) / count;
    edges = Array.from({ length: count + 1 }, (_, i) => (i === count ? hi : lo + i * width));
  }

  const bins: Bin[] = edges.slice(0, -1).map((x0, i) => ({ x0, x1: edges[i + 1], count: 0 }));
  const lo = edges[0];
  const hi = edges[edges.length - 1];
  for (const v of data) {
    if (v < lo || v > hi) continue;
    // Binary search for the bin whose [x0, x1) contains v; the max goes in the last bin.
    let a = 0;
    let b = bins.length - 1;
    while (a < b) {
      const mid = (a + b + 1) >> 1;
      if (v >= bins[mid].x0) a = mid;
      else b = mid - 1;
    }
    bins[a].count++;
  }
  return bins;
}
