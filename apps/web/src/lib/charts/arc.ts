/*
 * Polar geometry: annular sectors for donuts and gauges, stroke-dash rings, radar polygons.
 * Angles are radians, 0 = 3 o'clock, increasing clockwise (SVG's y-down convention).
 */

import { n } from "./path";

export const TAU = Math.PI * 2;

export function polar(cx: number, cy: number, r: number, angle: number): [number, number] {
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

/**
 * Path for the ring slice between radii r0 < r1 from angle a0 to a1 (clockwise). A sweep of a
 * full turn is drawn as two halves, because a single SVG arc cannot start and end on the
 * same point. r0 = 0 gives a pie wedge.
 */
export function annularSector(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const sweep = a1 - a0;
  if (sweep <= 0) return "";
  if (sweep >= TAU - 1e-9) {
    const mid = a0 + Math.PI;
    return `${annularSector(cx, cy, r0, r1, a0, mid)} ${annularSector(cx, cy, r0, r1, mid, a0 + TAU)}`;
  }
  const large = sweep > Math.PI ? 1 : 0;
  const [x0, y0] = polar(cx, cy, r1, a0);
  const [x1, y1] = polar(cx, cy, r1, a1);
  const outer = `M${n(x0)} ${n(y0)} A${n(r1)} ${n(r1)} 0 ${large} 1 ${n(x1)} ${n(y1)}`;
  if (r0 <= 0) return `${outer} L${n(cx)} ${n(cy)} Z`;
  const [x2, y2] = polar(cx, cy, r0, a1);
  const [x3, y3] = polar(cx, cy, r0, a0);
  return `${outer} L${n(x2)} ${n(y2)} A${n(r0)} ${n(r0)} 0 ${large} 0 ${n(x3)} ${n(y3)} Z`;
}

export type DonutSlice = { index: number; value: number; fraction: number; a0: number; a1: number; d: string };

/**
 * Donut slices for non-negative `values`, starting at 12 o'clock. Each slice is inset by
 * `gapPx / 2` of arc length at the outer radius on both sides, so neighbours are separated
 * by a constant `gapPx` surface gap. Zero and non-finite values produce no slice.
 */
export function donutSlices(
  values: readonly number[],
  { cx, cy, r0, r1, gapPx = 2 }: { cx: number; cy: number; r0: number; r1: number; gapPx?: number },
): DonutSlice[] {
  const clean = values.map((v) => (Number.isFinite(v) && v > 0 ? v : 0));
  const total = clean.reduce((s, v) => s + v, 0);
  if (total <= 0) return [];
  const count = clean.filter((v) => v > 0).length;
  const pad = count > 1 ? gapPx / 2 / r1 : 0;
  let angle = -Math.PI / 2;
  const slices: DonutSlice[] = [];
  clean.forEach((value, index) => {
    if (value <= 0) return;
    const fraction = value / total;
    const a0 = angle;
    const a1 = angle + fraction * TAU;
    angle = a1;
    const s0 = a0 + pad;
    const s1 = Math.max(s0 + 1e-4, a1 - pad);
    slices.push({ index, value, fraction, a0, a1, d: annularSector(cx, cy, r0, r1, s0, s1) });
  });
  return slices;
}

/** Clamps a value to [0, 1]; non-finite -> 0. */
export function clamp01(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

/**
 * Half-circle gauge (left to right over the top): the full track and the filled share
 * `fraction`, plus tick marks at 0, ¼, ½, ¾ and 1 just outside the outer radius.
 */
export function gaugeArcs(
  fraction: number,
  { cx, cy, r0, r1 }: { cx: number; cy: number; r0: number; r1: number },
): { track: string; value: string; ticks: { x1: number; y1: number; x2: number; y2: number }[] } {
  const f = clamp01(fraction);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => {
    const a = Math.PI + Math.PI * t;
    const [x1, y1] = polar(cx, cy, r1 + 4, a);
    const [x2, y2] = polar(cx, cy, r1 + 10, a);
    return { x1, y1, x2, y2 };
  });
  return {
    track: annularSector(cx, cy, r0, r1, Math.PI, TAU),
    value: f > 0 ? annularSector(cx, cy, r0, r1, Math.PI, Math.PI + Math.PI * f) : "",
    ticks,
  };
}

/** `stroke-dasharray` that draws `fraction` of a circle of radius r (for progress rings). */
export function ringDash(fraction: number, r: number): string {
  const c = TAU * r;
  return `${n(c * clamp01(fraction))} ${n(c)}`;
}

/** Radar geometry: axis angles (first axis at 12 o'clock), points and ring polygons. */
export function radarLayout(axisCount: number, cx: number, cy: number, r: number) {
  const angle = (i: number) => -Math.PI / 2 + (i * TAU) / axisCount;
  const point = (i: number, fraction: number) => polar(cx, cy, r * clamp01(fraction), angle(i));
  const polygon = (fractions: readonly number[]) =>
    fractions.map((f, i) => point(i, f).map(n).join(",")).join(" ");
  return {
    angle,
    point,
    polygon,
    ring: (k: number) => polygon(Array<number>(axisCount).fill(k)),
    spoke: (i: number) => polar(cx, cy, r, angle(i)),
    /** Label position `offset` beyond the rim and its text-anchor. */
    label: (i: number, offset = 14) => {
      const a = angle(i);
      const [x, y] = polar(cx, cy, r + offset, a);
      const c = Math.cos(a);
      const anchor: "start" | "middle" | "end" = Math.abs(c) < 0.2 ? "middle" : c > 0 ? "start" : "end";
      return { x, y, anchor };
    },
  };
}
