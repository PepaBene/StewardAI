/*
 * Pure geometry behind ScoreMark (visual spec B4, D.1): five axes at fixed angles, a 0-6 score
 * mapped to 0.18R..R (a zero score keeps a small core, so 0 is never "missing"), the closed
 * Catmull-Rom spline through the five points, and the tone bands.
 */

/** Axis angles in degrees: Value at the top, then clockwise. */
export const AXIS_ANGLES_DEG = [-90, -18, 54, 126, 198] as const;

export const CORE_FRACTION = 0.18;
export const MAX_SCORE = 6;

export type MarkTone = "weak" | "mixed" | "strong";

export type MarkPoint = {
  /** Position of the spline's anchor on the axis. */
  x: number;
  y: number;
  /** Radius used (the core radius for an unknown axis). */
  r: number;
  angle: number;
  /** Score 0-6 or null when unknown / not applicable. */
  score: number | null;
  /** Where the axis meets the outer ring. */
  ringX: number;
  ringY: number;
  /** Where the axis leaves the core. */
  coreX: number;
  coreY: number;
};

/** Radius for a 0-6 score: R * (0.18 + 0.82 * s / 6). Unknown (null) sits on the core. */
export function scoreRadius(score: number | null, R: number): number {
  if (score === null || !Number.isFinite(score)) return R * CORE_FRACTION;
  const s = Math.max(0, Math.min(MAX_SCORE, score));
  return R * (CORE_FRACTION + (1 - CORE_FRACTION) * (s / MAX_SCORE));
}

/** Tone from the mean of the known axis scores: < 2.5 weak, 2.5-4.0 mixed, > 4.0 strong. */
export function scoreTone(mean: number | null): MarkTone | null {
  if (mean === null || !Number.isFinite(mean)) return null;
  if (mean < 2.5) return "weak";
  if (mean <= 4) return "mixed";
  return "strong";
}

/** Mean of the known (non-null) scores, or null when none is known. */
export function meanKnown(scores: readonly (number | null)[]): number | null {
  const known = scores.filter((s): s is number => s !== null && Number.isFinite(s));
  return known.length ? known.reduce((a, b) => a + b, 0) / known.length : null;
}

/** The five anchor points for `scores` (0-6, null = unknown) on a circle of radius R at (cx, cy). */
export function markPoints(scores: readonly (number | null)[], R: number, cx = 0, cy = 0): MarkPoint[] {
  return AXIS_ANGLES_DEG.map((deg, i) => {
    const angle = (deg * Math.PI) / 180;
    const raw = scores[i] ?? null;
    const score = raw === null || !Number.isFinite(raw) ? null : Math.max(0, Math.min(MAX_SCORE, raw));
    const r = scoreRadius(score, R);
    const core = R * CORE_FRACTION;
    return {
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      r,
      angle,
      score,
      ringX: cx + R * Math.cos(angle),
      ringY: cy + R * Math.sin(angle),
      coreX: cx + core * Math.cos(angle),
      coreY: cy + core * Math.sin(angle),
    };
  });
}

const f = (n: number) => String(Math.round(n * 100) / 100);

/**
 * Closed Catmull-Rom spline through `pts` as cubic Béziers. With `tension` t the controls are
 * P1 + (P2 - P0) * t / 3 and P2 - (P3 - P1) * t / 3; t = 0.5 is the uniform Catmull-Rom spline.
 */
export function closedSplinePath(pts: readonly { x: number; y: number }[], tension = 0.5): string {
  const n = pts.length;
  if (n === 0) return "";
  if (n < 3) return `M${f(pts[0].x)},${f(pts[0].y)}${pts.slice(1).map((p) => `L${f(p.x)},${f(p.y)}`).join("")}Z`;
  const k = tension / 3;
  let d = `M${f(pts[0].x)},${f(pts[0].y)}`;
  for (let i = 0; i < n; i++) {
    const p0 = pts[(i - 1 + n) % n];
    const p1 = pts[i];
    const p2 = pts[(i + 1) % n];
    const p3 = pts[(i + 2) % n];
    const c1x = p1.x + (p2.x - p0.x) * k;
    const c1y = p1.y + (p2.y - p0.y) * k;
    const c2x = p2.x - (p3.x - p1.x) * k;
    const c2y = p2.y - (p3.y - p1.y) * k;
    d += `C${f(c1x)},${f(c1y)} ${f(c2x)},${f(c2y)} ${f(p2.x)},${f(p2.y)}`;
  }
  return `${d}Z`;
}
