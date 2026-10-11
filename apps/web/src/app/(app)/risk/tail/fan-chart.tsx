import { yearsText } from "@/lib/risk/tail";

type Fan = Record<string, number[]>;

const W = 640;
const H = 240;
const PAD = { l: 44, r: 12, t: 10, b: 24 };

/**
 * Where 100 invested today could stand over the years: the middle half of the simulated futures
 * as a band, the worst and best 5 % as a lighter band, and the median as a line. The start line is
 * what you put in, so anything under it is a loss.
 */
export function FanChart({ fan, stepWeeks, years }: { fan: Fan; stepWeeks: number; years: number }) {
  const mid = fan.p50;
  if (!mid || mid.length < 2 || !fan.p05 || !fan.p95 || !fan.p25 || !fan.p75) return null;
  const n = mid.length;
  const lo = Math.min(...fan.p05, 1);
  const hi = Math.max(...fan.p95, 1);
  const x = (i: number) => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - lo) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
  const band = (a: number[], b: number[]) =>
    `${a.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("")}${[...b]
      .map((v, i) => [v, i] as const)
      .reverse()
      .map(([v, i]) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`)
      .join("")}Z`;
  const line = mid.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const ticks = [lo, 1, (lo + hi) / 2, hi].filter((v, i, a) => a.findIndex((u) => Math.abs(u - v) < (hi - lo) / 12) === i);
  const lastWeek = (n - 1) * stepWeeks;
  const label = `Where 100 invested today could stand over ${yearsText(years)}: middle of the range ${Math.round(mid[n - 1] * 100)}, bad case ${Math.round(fan.p05[n - 1] * 100)}, good case ${Math.round(fan.p95[n - 1] * 100)}.`;
  return (
    <figure className="mt-4 max-w-2xl text-accent">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="h-auto w-full">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="currentColor" strokeOpacity={t === 1 ? 0.55 : 0.12} strokeDasharray={t === 1 ? "4 3" : undefined} />
            <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" fontSize="11" fill="currentColor" fillOpacity={0.7} className="num">
              {Math.round(t * 100)}
            </text>
          </g>
        ))}
        <path d={band(fan.p05, fan.p95)} fill="currentColor" fillOpacity={0.1} />
        <path d={band(fan.p25, fan.p75)} fill="currentColor" fillOpacity={0.2} />
        <path d={line} fill="none" stroke="currentColor" strokeWidth={2} />
        <text x={PAD.l} y={H - 6} fontSize="11" fill="currentColor" fillOpacity={0.7}>today</text>
        <text x={W - PAD.r} y={H - 6} textAnchor="end" fontSize="11" fill="currentColor" fillOpacity={0.7}>
          {Math.round(lastWeek / 52)} years
        </text>
      </svg>
      <figcaption className="mt-1 text-xs text-text-muted">
        Line: the middle future. Dark band: the middle half. Light band: all but the worst and best 5 %. The dashed line is 100, your starting value.
      </figcaption>
    </figure>
  );
}
