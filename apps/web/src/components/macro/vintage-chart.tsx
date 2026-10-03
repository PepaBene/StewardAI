import { formatDay } from "@/lib/charts/time";
import { linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { periodTime, vintageSeries, type VintageMode } from "@/lib/macro/logic";
import type { VintagePoint } from "@/lib/macro/types";

import { formatFigure } from "./format";

const W = 320;
const H = 84;
const PAD_X = 6;
const PAD_TOP = 8;
const PAD_BOTTOM = 18;

const yearOf = (t: number) => new Date(t).getUTCFullYear();

/**
 * One small multiple of an indicator's vintage history. Every chart of a panel gets the same
 * `domain` (earliest to latest period over all series), so the x positions line up across the
 * grid. In first-release mode a period whose first release was not archived is a gap in the
 * line (and is counted), never the revised value; a ring marks each period that was revised.
 */
export function VintageChart({ history, mode, domain, label, unit }: { history: readonly VintagePoint[]; mode: VintageMode; domain: readonly [number, number]; label: string; unit: string }) {
  const series = vintageSeries(history, mode);
  const x = linearScale(domain, [PAD_X, W - PAD_X]);
  const finite = series.map((p) => p.value).filter(isFiniteNumber);
  const ext = extent(finite);
  const y = ext ? linearScale(ext, [H - PAD_BOTTOM, PAD_TOP]) : null;
  const pts: (Point | null)[] = series.map((p) => {
    const t = periodTime(p.period);
    return t !== null && y && isFiniteNumber(p.value) ? [x(t), y(p.value)] : null;
  });
  const unavailable = series.filter((p) => p.vintageUnavailable).length;
  const revised = series.filter((p) => p.revised);
  const modeName = mode === "latest" ? "latest vintage" : "first release";
  const aria =
    finite.length === 0
      ? `${label}, ${modeName}: ${mode === "first_release" ? "vintage unavailable for every period" : "no values"}`
      : `${label}, ${modeName}: ${series.length} periods, ${formatFigure(finite[0])} to ${formatFigure(finite[finite.length - 1])} ${unit}; ${revised.length} revised`;

  return (
    <div className="flex flex-col gap-1" data-vintage-chart={mode}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria} className="block h-auto w-full">
        <line x1={PAD_X} x2={W - PAD_X} y1={H - PAD_BOTTOM} y2={H - PAD_BOTTOM} className="stroke-chart-grid" strokeWidth={1} shapeRendering="crispEdges" />
        {y ? <path d={linePath(pts)} fill="none" className="stroke-chart-line" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" /> : null}
        {pts.map((p, i) =>
          p && series[i].revised ? (
            <circle key={series[i].period} cx={p[0]} cy={p[1]} r={3.5} className="fill-surface stroke-text" strokeWidth={1.5} data-revision-marker="">
              <title>{`${formatDay(series[i].period)}: revised ${formatDay(series[i].revisedAt ?? "")}, ${series[i].revisionCount} revision${series[i].revisionCount === 1 ? "" : "s"}`}</title>
            </circle>
          ) : p ? (
            <circle key={series[i].period} cx={p[0]} cy={p[1]} r={1.75} className="fill-chart-line">
              <title>{`${formatDay(series[i].period)}: ${formatFigure(series[i].value)} ${unit}`}</title>
            </circle>
          ) : null,
        )}
        <text x={PAD_X} y={H - 4} fontSize={10} className="num fill-text-muted">{yearOf(domain[0])}</text>
        <text x={W - PAD_X} y={H - 4} fontSize={10} textAnchor="end" className="num fill-text-muted">{yearOf(domain[1])}</text>
        {ext ? (
          <text x={W - PAD_X} y={PAD_TOP + 2} fontSize={10} textAnchor="end" className="num fill-text-muted">{formatFigure(ext[1])}</text>
        ) : null}
      </svg>
      <p className="text-[11px] text-text-muted">
        {mode === "first_release" && finite.length === 0
          ? "Vintage unavailable: no first release was archived for this series, so nothing is drawn (the revised values are not substituted)."
          : mode === "first_release" && unavailable > 0
            ? `Vintage unavailable for ${unavailable} of ${series.length} periods: left blank, not replaced by the revised value.`
            : series.length === 0
              ? "No history was returned for this series."
              : `${series.length} period${series.length === 1 ? "" : "s"}${revised.length ? `; ${revised.length} revised (ringed marker)` : "; none revised"}.`}
      </p>
    </div>
  );
}
