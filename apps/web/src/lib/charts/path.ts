/*
 * SVG path strings for lines and areas. A null point breaks the line (a data gap is drawn
 * as a gap, never interpolated across).
 */

export type Point = readonly [number, number];

/** Two-decimal number for path data, trailing zeros trimmed. */
export function n(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/** Splits points into runs of consecutive non-null points. */
export function segments(points: readonly (Point | null)[]): Point[][] {
  const runs: Point[][] = [];
  let run: Point[] = [];
  for (const p of points) {
    if (p) run.push(p);
    else if (run.length) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length) runs.push(run);
  return runs;
}

/** "M x y L x y ..." for each run of points; a single isolated point becomes a zero-length move. */
export function linePath(points: readonly (Point | null)[]): string {
  return segments(points)
    .map((run) => run.map(([x, y], i) => `${i ? "L" : "M"}${n(x)} ${n(y)}`).join(" "))
    .join(" ");
}

/** Closed area between each run of points and a horizontal `baseline` y. */
export function areaPath(points: readonly (Point | null)[], baseline: number): string {
  return segments(points)
    .filter((run) => run.length > 1)
    .map((run) => {
      const top = run.map(([x, y], i) => `${i ? "L" : "M"}${n(x)} ${n(y)}`).join(" ");
      const last = run[run.length - 1];
      const first = run[0];
      return `${top} L${n(last[0])} ${n(baseline)} L${n(first[0])} ${n(baseline)} Z`;
    })
    .join(" ");
}
