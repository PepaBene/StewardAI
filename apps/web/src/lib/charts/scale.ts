/*
 * Scales and ticks for the SVG chart kit (components/charts). Pure functions, no DOM.
 */

/** True for a real, finite number (not null, undefined or NaN). */
export function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** [min, max] of the finite values, or null when there are none. */
export function extent(values: Iterable<number | null | undefined>): [number, number] | null {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (!isFiniteNumber(v)) continue;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  return min === Infinity ? null : [min, max];
}

/**
 * Maps `domain` linearly onto `range`. A degenerate domain (min === max) maps every value
 * to the middle of the range, so a flat series draws as a flat line, not a division by zero.
 */
export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0 || !Number.isFinite(span)) {
    const mid = (r0 + r1) / 2;
    return () => mid;
  }
  const k = (r1 - r0) / span;
  return (value: number) => r0 + (value - d0) * k;
}

/** A "nice" step (1, 2, 2.5 or 5 × 10^k) that splits `span` into roughly `count` parts. */
export function niceStep(span: number, count: number): number {
  if (!(span > 0) || !(count > 0)) return 1;
  const raw = span / count;
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const unit = raw / power;
  const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
  return nice * power;
}

/** Rounds away floating-point dust (0.30000000000000004 -> 0.3). */
function clean(value: number): number {
  return Number.parseFloat(value.toPrecision(12));
}

/**
 * Round tick values covering [min, max] with about `count` intervals. The first tick is
 * ≤ min and the last ≥ max, so ticks double as a padded domain (see niceDomain).
 */
export function niceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min > max) [min, max] = [max, min];
  if (min === max) {
    const pad = min === 0 ? 1 : Math.abs(min) * 0.01;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min, count);
  const start = Math.floor(clean(min / step)) * step;
  const end = Math.ceil(clean(max / step)) * step;
  const ticks: number[] = [];
  for (let v = start, i = 0; v <= end + step * 1e-9 && i < 1000; v += step, i++) {
    ticks.push(clean(v));
  }
  return ticks;
}

/** The [first, last] of niceTicks: a domain that ends on round numbers. */
export function niceDomain(min: number, max: number, count = 4): [number, number] {
  const ticks = niceTicks(min, max, count);
  if (ticks.length === 0) return [0, 1];
  return [ticks[0], ticks[ticks.length - 1]];
}

/**
 * Evenly spaced bands for `count` bars across `range`. `paddingInner` is the share of each
 * step left empty between bars; `maxBandwidth` caps the bar so a few bars never fill the
 * slot (the leftover is air, centred in the step).
 */
export function bandScale(
  count: number,
  range: readonly [number, number],
  { paddingInner = 0.2, maxBandwidth = Infinity }: { paddingInner?: number; maxBandwidth?: number } = {},
): { step: number; bandwidth: number; x: (index: number) => number } {
  const [r0, r1] = range;
  const step = count > 0 ? (r1 - r0) / count : 0;
  const bandwidth = Math.max(0, Math.min(step * (1 - paddingInner), maxBandwidth));
  const offset = (step - bandwidth) / 2;
  return { step, bandwidth, x: (index: number) => r0 + index * step + offset };
}
