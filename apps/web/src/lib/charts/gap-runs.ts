/*
 * The area between a series and its benchmark, split into runs where the series is above
 * (ahead) or below (behind). A run is cut exactly where the lines cross, so the shading never
 * spills across a crossing. Points are screen coordinates [x, y]; a smaller y is higher.
 */

export type ScreenPoint = readonly [number, number];
export type GapRun = { ahead: boolean; d: string };

const fmt = (n: number) => Math.round(n * 10) / 10;

function polygon(top: ScreenPoint[], bottom: ScreenPoint[]): string {
  const pts = [...top, ...[...bottom].reverse()];
  return `M${pts.map(([x, y]) => `${fmt(x)},${fmt(y)}`).join("L")}Z`;
}

export function gapRuns(main: readonly (ScreenPoint | null)[], bench: readonly (ScreenPoint | null)[]): GapRun[] {
  const runs: GapRun[] = [];
  let top: ScreenPoint[] = [];
  let bottom: ScreenPoint[] = [];
  let ahead: boolean | null = null;

  const close = () => {
    if (ahead !== null && top.length >= 2) runs.push({ ahead, d: polygon(top, bottom) });
    top = [];
    bottom = [];
    ahead = null;
  };

  for (let i = 0; i < main.length; i++) {
    const m = main[i];
    const b = bench[i];
    if (!m || !b) {
      close();
      continue;
    }
    const diff = b[1] - m[1]; // > 0: series drawn above the benchmark, i.e. ahead
    const side = diff >= 0;
    if (ahead === null) {
      ahead = side;
      top.push(m);
      bottom.push(b);
      continue;
    }
    if (side !== ahead) {
      const pm = top[top.length - 1];
      const pb = bottom[bottom.length - 1];
      const d0 = pb[1] - pm[1];
      const t = d0 / (d0 - diff);
      const cross: ScreenPoint = [pm[0] + (m[0] - pm[0]) * t, pm[1] + (m[1] - pm[1]) * t];
      top.push(cross);
      bottom.push(cross);
      runs.push({ ahead, d: polygon(top, bottom) });
      top = [cross, m];
      bottom = [cross, b];
      ahead = side;
      continue;
    }
    top.push(m);
    bottom.push(b);
  }
  close();
  return runs;
}

/** Share of the points where the series is ahead of the benchmark, for the chart's reading. */
export function aheadShare(main: readonly (number | null)[], bench: readonly (number | null)[]): number | null {
  let n = 0;
  let ahead = 0;
  main.forEach((v, i) => {
    const b = bench[i];
    if (v === null || b === null || b === undefined || !Number.isFinite(v) || !Number.isFinite(b)) return;
    n += 1;
    if (v >= b) ahead += 1;
  });
  return n ? ahead / n : null;
}
