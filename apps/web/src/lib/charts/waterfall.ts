/**
 * Geometry for a waterfall: `total` steps stand on the zero line, `delta` steps float from the
 * running level. All numbers are plain values; the component maps them to pixels.
 */

export type WaterfallStepInput = {
  id: string;
  label: string;
  value: number;
  kind: "total" | "delta";
};

export type WaterfallBar = WaterfallStepInput & {
  /** Level the bar starts at and ends at. */
  from: number;
  to: number;
  /** Lower and upper edge of the drawn bar. */
  low: number;
  high: number;
  /** Running level after this step. */
  level: number;
};

/**
 * A total resets the running level to its own value (it is a subtotal that already includes the
 * deltas before it); a delta moves the level by its value. Non-finite values are skipped.
 */
export function waterfallBars(steps: readonly WaterfallStepInput[]): WaterfallBar[] {
  const out: WaterfallBar[] = [];
  let level = 0;
  for (const s of steps) {
    if (!Number.isFinite(s.value)) continue;
    const from = s.kind === "total" ? 0 : level;
    const to = s.kind === "total" ? s.value : level + s.value;
    level = to;
    out.push({ ...s, from, to, low: Math.min(from, to), high: Math.max(from, to), level });
  }
  return out;
}

/** Value range the bars span, always including zero. */
export function waterfallExtent(bars: readonly WaterfallBar[]): [number, number] | null {
  if (bars.length === 0) return null;
  return [Math.min(0, ...bars.map((b) => b.low)), Math.max(0, ...bars.map((b) => b.high))];
}
