import { ProvenanceFooter, TableView } from "@/components/data-state";
import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { formatNumber } from "@/lib/charts/format";
import { linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale, niceTicks } from "@/lib/charts/scale";
import { formatDay } from "@/lib/charts/time";
import { dayNumber, latestObservation, revisedObservations, seriesPoints, valueFor, vintageSummary } from "@/lib/macro/logic";
import type { MacroSeries, Vintage } from "@/lib/macro/types";
import { formatDate } from "@/lib/format";

const W = 360;
const H = 140;
const LEFT = 4;
const RIGHT = 40;
const TOP = 10;
const BOTTOM = 22;

function yearTicks(domain: readonly [string, string]): { x: number; label: string }[] {
  const d0 = dayNumber(domain[0]);
  const d1 = dayNumber(domain[1]);
  const y0 = Number(domain[0].slice(0, 4));
  const y1 = Number(domain[1].slice(0, 4));
  const step = y1 - y0 > 9 ? 4 : y1 - y0 > 5 ? 2 : 1;
  const ticks: { x: number; label: string }[] = [];
  for (let y = y0 + 1; y <= y1; y += step) {
    const d = dayNumber(`${y}-01-01`);
    if (d > d0 && d < d1) ticks.push({ x: (d - d0) / (d1 - d0), label: String(y) });
  }
  return ticks;
}

/**
 * One macro series on a time axis shared with its siblings (`domain`), so panels of different
 * frequency line up in time while each keeps its own unit and value axis. Under "latest" the
 * ringed points are values revised since first release; under "first release" the line shows
 * only what was originally published, and a leading stretch with no archived first release is
 * labelled `vintage unavailable` instead of being filled with revised values.
 */
export function SeriesPanel({
  series,
  vintage,
  domain,
}: {
  series: MacroSeries;
  vintage: Vintage;
  domain: readonly [string, string];
}) {
  const d0 = dayNumber(domain[0]);
  const d1 = dayNumber(domain[1]);
  const pts = seriesPoints(series, vintage);
  const both = series.observations.flatMap((o) => [o.latest, o.firstRelease]);
  const ext = extent(both);
  const summary = vintageSummary(series);
  const last = latestObservation(series);
  const lastValue = last ? valueFor(last, vintage) : null;

  const hasAny = pts.some((p) => isFiniteNumber(p.value));
  const ticks = ext ? niceTicks(ext[0], ext[1], 3) : [];
  const yDomain: [number, number] = ticks.length ? [ticks[0], ticks[ticks.length - 1]] : [0, 1];
  const x = linearScale([d0, d1], [LEFT, W - RIGHT]);
  const y = linearScale(yDomain, [H - BOTTOM, TOP]);
  const xy = (iso: string, v: number | null): Point | null => (isFiniteNumber(v) ? [x(dayNumber(iso)), y(v)] : null);
  const line = linePath(pts.map((p) => xy(p.date, p.value)));
  const revised = vintage === "latest" ? revisedObservations(series) : [];

  // The leading run of observations with no archived first release, for the hatched band.
  let unavailableTo = -1;
  if (vintage === "first_release") {
    for (let i = 0; i < series.observations.length && !isFiniteNumber(series.observations[i].firstRelease); i++) unavailableTo = i;
  }
  const bandEnd = unavailableTo >= 0 ? x(dayNumber(series.observations[unavailableTo].period)) : null;

  const label = `${series.title}, ${series.countryName}, ${vintage === "latest" ? "latest vintage" : "first release"}`;
  const ariaLabel =
    hasAny && last
      ? `${label}: ${lastValue !== null ? `${formatNumber(lastValue, 1)} ${series.unit}` : "first release not archived"} at ${formatDay(last.period)}; ${summary.revised} of ${summary.total} values revised`
      : `${label}: no values under this vintage`;

  return (
    <figure className="m-0 flex flex-col gap-2 border border-border-soft p-3" data-series={series.id}>
      <figcaption className="flex flex-wrap items-start justify-between gap-2">
        <span>
          <span className="block font-heading text-[17px] leading-tight font-semibold">{series.title}</span>
          <span className="block text-xs text-text-muted">
            {series.countryName} · {series.unit} · {series.frequency}, {series.seasonalAdjustment}
          </span>
        </span>
        <span className="text-right">
          <span className="num block font-heading text-[24px] leading-none font-semibold">
            {lastValue !== null ? formatNumber(lastValue, 1) : "n/a"}
          </span>
          <span className="num block text-[11px] text-text-muted">{last ? formatDay(last.period) : "no observation"}</span>
        </span>
      </figcaption>

      {hasAny ? (
        <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={ariaLabel}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={W - RIGHT} y1={y(t)} y2={y(t)} className="stroke-chart-grid" strokeWidth={1} shapeRendering="crispEdges" />
              <text x={W} y={y(t) + 4} textAnchor="end" fontSize={10} className={AXIS_TEXT}>
                {formatNumber(t, Number.isInteger(t) ? 0 : 1)}
              </text>
            </g>
          ))}
          {yDomain[0] < 0 && yDomain[1] > 0 ? (
            <line x1={LEFT} x2={W - RIGHT} y1={y(0)} y2={y(0)} className="stroke-text-faint" strokeWidth={1} strokeDasharray="2 3" />
          ) : null}
          {yearTicks(domain).map((t) => (
            <text key={t.label} x={LEFT + t.x * (W - RIGHT - LEFT)} y={H - 6} fontSize={10} className={AXIS_TEXT}>
              {t.label}
            </text>
          ))}
          {bandEnd !== null ? (
            <g>
              <rect x={LEFT} y={TOP} width={Math.max(0, bandEnd - LEFT)} height={H - BOTTOM - TOP} className="fill-border-soft" />
              <text x={LEFT + 4} y={TOP + 12} fontSize={10} className="fill-text-muted">
                Vintage unavailable
              </text>
            </g>
          ) : null}
          <path d={line} fill="none" className="stroke-chart-line" strokeWidth={1.75} strokeLinejoin="round" />
          {revised.map((o) => {
            const first = o.firstRelease!;
            const cx = x(dayNumber(o.period));
            return (
              <g key={o.period}>
                <line x1={cx} x2={cx} y1={y(first)} y2={y(o.latest)} className="stroke-warning-signal" strokeWidth={1} />
                <circle cx={cx} cy={y(o.latest)} r={3.5} className="fill-surface stroke-warning-signal" strokeWidth={1.5}>
                  <title>{`${formatDay(o.period)}: revised from ${formatNumber(first, 2)} to ${formatNumber(o.latest, 2)} (${o.revisedAt ? formatDay(o.revisedAt) : "date unknown"})`}</title>
                </circle>
              </g>
            );
          })}
        </svg>
      ) : (
        <div className="flex min-h-24 items-center justify-center border border-dashed border-border px-3 text-center text-xs text-text-muted">
          No first release was archived for this series, so nothing is drawn under this vintage.
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
        <span className="num">
          {summary.revised} of {summary.total} values revised
          {summary.maxRevision !== null ? ` (largest ${formatNumber(summary.maxRevision, 2)})` : ""}
        </span>
        {summary.unavailable > 0 ? <span className="num">{summary.unavailable} without archived first release</span> : null}
        {series.nextRelease ? (
          <span className="num">
            Next release <time dateTime={series.nextRelease}>{formatDate(series.nextRelease)}</time>
          </span>
        ) : null}
        {vintage === "latest" && revised.length > 0 ? (
          <span className="inline-flex items-center gap-1">
            <svg aria-hidden="true" width="10" height="10">
              <circle cx="5" cy="5" r="3.5" className="fill-surface stroke-warning-signal" strokeWidth="1.5" />
            </svg>
            Revised value
          </span>
        ) : null}
      </div>

      <TableView
        summary="Table view"
        table={{
          caption: `${label}, every observation`,
          columns: ["Period", "Latest", "First release", "Revised"],
          rows: [...series.observations]
            .reverse()
            .map((o) => [
              formatDay(o.period),
              formatNumber(o.latest, 2),
              isFiniteNumber(o.firstRelease) ? formatNumber(o.firstRelease, 2) : "vintage unavailable",
              o.revisedAt ? formatDay(o.revisedAt) : "—",
            ]),
        }}
      />
      <ProvenanceFooter
        className="border-t-0 pt-0"
        provenance={{
          source: `${series.provider} · ${series.title}`,
          sourceUrl: series.sourceUrl,
          asOf: last?.period ?? null,
          knownAt: last?.releasedAt ?? null,
          method: vintage === "latest" ? "Latest vintage; earlier values replaced by revisions" : "First published value of each period",
          state: series.state,
          reason: series.reason,
        }}
      />
    </figure>
  );
}
