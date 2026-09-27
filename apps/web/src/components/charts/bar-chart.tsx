import { formatNumber } from "@/lib/charts/format";
import type { Bin } from "@/lib/charts/histogram";
import { binValues } from "@/lib/charts/histogram";
import { bandScale, extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { AXIS_TEXT, ChartEmpty, Legend, SrTable, SVG_BASE } from "./shared";

export type BarDatum = {
  label: string;
  /** Null draws no bar (a "—" sits on the baseline); nothing is invented. */
  value: number | null;
  /** Draw in the full accent while the rest recede to a lighter step. */
  highlight?: boolean;
  /** A claimed / unverified figure: dashed outline, pale fill. */
  claim?: boolean;
};

export type BarChartProps = {
  data: readonly BarDatum[];
  /** Highlight the last bar (e.g. the latest quarter). */
  highlightLast?: boolean;
  /** Dashed reference line drawn over the bars; its label shows as a key above the chart (e.g. "Threshold 60"). */
  threshold?: { value: number; label?: string };
  /** Value on top of each bar. Default true. */
  valueLabels?: boolean;
  /** Category labels under the bars. Default true. */
  xLabels?: boolean;
  /** Maximum bar width in viewBox units. Default 48. */
  maxBarWidth?: number;
  width?: number;
  height?: number;
  formatValue?: (value: number) => string;
  /** Accessible name, e.g. "Net bookings by quarter, €bn". */
  label?: string;
  className?: string;
};

/**
 * Vertical bars from a zero baseline (negative values hang below it), square ends, value on
 * the cap. With any highlighted bar the others step back to the lighter accent. A dashed bar
 * marks a claimed (unverified) value; a dashed line marks a threshold.
 */
export function BarChart({
  data,
  highlightLast = false,
  threshold,
  valueLabels = true,
  xLabels = true,
  maxBarWidth = 48,
  width = 400,
  height = 160,
  formatValue = (v) => formatNumber(v, 1),
  label = "Bar chart",
  className,
}: BarChartProps) {
  const values = data.map((d) => d.value);
  const ext = extent([...values, 0, ...(threshold ? [threshold.value] : [])]);
  if (!data.some((d) => isFiniteNumber(d.value)) || !ext) {
    return <ChartEmpty minHeight={height / 2} className={className} />;
  }

  const top = valueLabels ? 16 : 4;
  const bottom = xLabels ? 20 : 2;
  const y0 = top;
  const y1 = height - bottom;
  const [lo, hi] = ext;
  const y = linearScale([lo, hi === lo ? lo + 1 : hi], [y1, y0]);
  const band = bandScale(data.length, [0, width], { paddingInner: 0.12, maxBandwidth: maxBarWidth });
  const lastIdx = data.length - 1;
  const isHi = (d: BarDatum, i: number) => !!d.highlight || (highlightLast && i === lastIdx);
  const anyHighlight = data.some(isHi);

  return (
    <figure className={cn("m-0 flex flex-col gap-1", className)}>
      {threshold?.label ? (
        <Legend
          items={[{ label: threshold.label, swatch: "dashed", className: "border-text-muted" }]}
          className="self-end"
        />
      ) : null}
      <svg viewBox={`0 0 ${width} ${height}`} className={SVG_BASE} aria-hidden="true">
        {data.map((d, i) => {
          const bx = band.x(i);
          const cx = bx + band.bandwidth / 2;
          if (!isFiniteNumber(d.value)) {
            return (
              <text key={i} x={cx} y={y(0) - 4} textAnchor="middle" fontSize={10} className="fill-text-faint">
                —
              </text>
            );
          }
          const v = d.value;
          const top = Math.min(y(v), y(0));
          const h = Math.max(1, Math.abs(y(v) - y(0)));
          const fill = d.claim
            ? "fill-accent-soft stroke-accent"
            : anyHighlight && !isHi(d, i)
              ? "fill-accent-300"
              : "fill-accent";
          return (
            <g key={i}>
              <rect
                x={bx}
                y={top}
                width={band.bandwidth}
                height={h}
                className={fill}
                strokeWidth={d.claim ? 1.25 : 0}
                strokeDasharray={d.claim ? "3 3" : undefined}
              >
                <title>{`${d.label}: ${formatValue(v)}${d.claim ? " (claimed, unverified)" : ""}`}</title>
              </rect>
              {valueLabels ? (
                <text
                  x={cx}
                  y={v >= 0 ? top - 4 : top + h + 11}
                  textAnchor="middle"
                  fontSize={10}
                  className="fill-text-muted num"
                >
                  {formatValue(v)}
                </text>
              ) : null}
            </g>
          );
        })}
        {threshold ? (
          <line
            x1={0}
            x2={width}
            y1={y(threshold.value)}
            y2={y(threshold.value)}
            className="stroke-text"
            strokeOpacity={0.55}
            strokeWidth={1}
            strokeDasharray="3 3"
          />
        ) : null}
        {lo < 0 ? (
          <line x1={0} x2={width} y1={y(0)} y2={y(0)} className="stroke-border" strokeWidth={1} shapeRendering="crispEdges" />
        ) : null}
        {xLabels
          ? data.map((d, i) => (
              <text
                key={i}
                x={band.x(i) + band.bandwidth / 2}
                y={height - 4}
                textAnchor="middle"
                fontSize={10}
                className={AXIS_TEXT}
              >
                {d.label}
              </text>
            ))
          : null}
      </svg>
      <SrTable
        caption={threshold?.label ? `${label}; ${threshold.label}` : label}
        columns={["Category", "Value"]}
        rows={data.map((d) => [
          d.label,
          isFiniteNumber(d.value) ? `${formatValue(d.value)}${d.claim ? " (claimed, unverified)" : ""}` : "No data",
        ])}
      />
    </figure>
  );
}

export type HistogramProps = {
  /** Raw observations to bin (ignored when `bins` is given). */
  values?: readonly (number | null)[];
  /** Pre-computed bins (from `binValues`). */
  bins?: readonly Bin[];
  /** Equal-width bin count (default: Sturges' rule). */
  binCount?: number;
  /** Explicit bin edges, ascending. */
  thresholds?: readonly number[];
  /** Highlight the bin containing this value (e.g. the latest observation). */
  marker?: number;
  /** Count on top of each bin. Default false. */
  countLabels?: boolean;
  width?: number;
  height?: number;
  /** Formats bin edges on the axis. */
  formatEdge?: (value: number) => string;
  /** Accessible name, e.g. "Time to acknowledge, 90 days". */
  label?: string;
  className?: string;
};

/**
 * Distribution of values in touching bins (2px surface gap), with edge labels at the start,
 * middle and end of the axis. Every bin's range and count is in a visually hidden table.
 */
export function Histogram({
  values,
  bins: binsIn,
  binCount,
  thresholds,
  marker,
  countLabels = false,
  width = 400,
  height = 140,
  formatEdge = (v) => formatNumber(v, Math.abs(v) < 10 && !Number.isInteger(v) ? 1 : 0),
  label = "Histogram",
  className,
}: HistogramProps) {
  const bins = binsIn ?? binValues(values ?? [], { count: binCount, thresholds });
  const maxCount = Math.max(0, ...bins.map((b) => b.count));
  if (bins.length === 0 || maxCount === 0) return <ChartEmpty minHeight={height / 2} className={className} />;

  const top = countLabels ? 14 : 4;
  const y1 = height - 20;
  const y = linearScale([0, maxCount], [y1, top]);
  const step = width / bins.length;
  const gapPx = 2;
  const markerIdx =
    marker === undefined
      ? -1
      : bins.findIndex((b, i) => marker >= b.x0 && (marker < b.x1 || (i === bins.length - 1 && marker <= b.x1)));
  const edgeIdx = [...new Set([0, Math.round(bins.length / 2), bins.length])];
  const edgeValue = (i: number) => (i === bins.length ? bins[bins.length - 1].x1 : bins[i].x0);
  const total = bins.reduce((s, b) => s + b.count, 0);

  return (
    <figure className={cn("m-0", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className={SVG_BASE} aria-hidden="true">
        {bins.map((b, i) => {
          if (b.count === 0) return null;
          const h = y1 - y(b.count);
          return (
            <g key={i}>
              <rect
                x={i * step + gapPx / 2}
                y={y(b.count)}
                width={Math.max(0.5, step - gapPx)}
                height={h}
                className={i === markerIdx ? "fill-accent" : "fill-accent-300"}
              >
                <title>{`${formatEdge(b.x0)}–${formatEdge(b.x1)}: ${b.count}`}</title>
              </rect>
              {countLabels ? (
                <text x={i * step + step / 2} y={y(b.count) - 3} textAnchor="middle" fontSize={10} className="fill-text-muted num">
                  {b.count}
                </text>
              ) : null}
            </g>
          );
        })}
        <line x1={0} x2={width} y1={y1} y2={y1} className="stroke-border" strokeWidth={1} shapeRendering="crispEdges" />
        {edgeIdx.map((i) => (
          <text
            key={i}
            x={i * step}
            y={height - 4}
            textAnchor={i === 0 ? "start" : i === bins.length ? "end" : "middle"}
            fontSize={10}
            className={AXIS_TEXT}
          >
            {formatEdge(edgeValue(i))}
          </text>
        ))}
      </svg>
      <SrTable
        caption={`${label} (${total} values)`}
        columns={["Range", "Count"]}
        rows={bins.map((b) => [`${formatEdge(b.x0)} to ${formatEdge(b.x1)}`, b.count])}
      />
    </figure>
  );
}
