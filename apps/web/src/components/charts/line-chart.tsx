import { formatNumber } from "@/lib/charts/format";
import { areaPath, linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale, niceTicks } from "@/lib/charts/scale";
import { formatDay, timeTicks } from "@/lib/charts/time";
import { cn } from "@/lib/utils";

import { ChartEmpty, Legend, SVG_BASE, type LegendEntry } from "./shared";

/** A value on a calendar day ("2026-09-27"); null marks a gap. */
export type LinePoint = { date: string; value: number | null };

export type LineChartProps = {
  /** The main series, oldest first (e.g. the portfolio indexed to 100). */
  data: readonly LinePoint[];
  /** Optional comparison drawn dashed; matched to `data` by date (unmatched dates are dropped). */
  benchmark?: readonly LinePoint[];
  /** Series names, used in the legend and the accessible summary. */
  label?: string;
  benchmarkLabel?: string;
  /**
   * "default" draws the accent line on the page; "inverse" draws everything in the accent's
   * foreground (white) for a chart sitting on a solid accent panel, like the Overview hero.
   */
  variant?: "default" | "inverse";
  /** Shade under the main line. Default true. */
  area?: boolean;
  /** Horizontal gridlines at the y ticks. Default true. */
  grid?: boolean;
  /** Right-side y tick labels. Default true. */
  yLabels?: boolean;
  /** Month (or, past two years, year) labels under the plot. Default true. */
  xLabels?: boolean;
  /** Dot on the last value of the main series. Default true. */
  endDot?: boolean;
  /** Show a legend row under the chart (only when a benchmark is present). Default true. */
  legend?: boolean;
  /** Approximate number of y intervals. Default 4. */
  yTickCount?: number;
  /** viewBox size; the SVG scales to its container width. Match `width` to the typical render width so text stays near 11px. */
  width?: number;
  height?: number;
  formatValue?: (value: number) => string;
  /** Overrides the generated accessible summary. */
  ariaLabel?: string;
  className?: string;
};

const STYLES = {
  default: {
    line: "stroke-chart-line",
    area: "fill-chart-line",
    areaOpacity: 0.1,
    bench: "stroke-chart-benchmark",
    benchOpacity: 1,
    grid: "stroke-chart-grid",
    gridOpacity: 1,
    text: "fill-text-faint",
    textOpacity: 1,
    dot: "fill-chart-line stroke-surface",
    halo: null,
    hover: "fill-text",
    legendLine: "bg-chart-line",
    legendDash: "border-chart-benchmark",
    legendText: "text-text-muted",
    legendValue: "text-text",
  },
  inverse: {
    line: "stroke-accent-foreground",
    area: "fill-accent-foreground",
    areaOpacity: 0.12,
    bench: "stroke-accent-foreground",
    benchOpacity: 0.6,
    grid: "stroke-accent-foreground",
    gridOpacity: 0.16,
    text: "fill-accent-foreground",
    textOpacity: 0.72,
    dot: "fill-accent-foreground stroke-accent",
    halo: "fill-accent-foreground",
    hover: "fill-accent-foreground",
    legendLine: "bg-accent-foreground",
    legendDash: "border-accent-foreground/70",
    legendText: "text-accent-foreground/90",
    legendValue: "text-accent-foreground",
  },
} as const;

/**
 * Indexed performance line: optional dashed benchmark on the same axis (never a second
 * y-scale), solid hairline grid, right-side y labels, month x labels and an end dot. Every
 * point has a hover band with a native tooltip. Fewer than two finite values -> empty state.
 */
export function LineChart({
  data,
  benchmark,
  label = "Portfolio",
  benchmarkLabel = "Benchmark",
  variant = "default",
  area = true,
  grid = true,
  yLabels = true,
  xLabels = true,
  endDot = true,
  legend = true,
  yTickCount = 4,
  width = 800,
  height = 260,
  formatValue = (v) => formatNumber(v, 1),
  ariaLabel,
  className,
}: LineChartProps) {
  const values = data.map((p) => p.value);
  const finite = values.filter(isFiniteNumber);
  if (finite.length < 2) return <ChartEmpty minHeight={height / 2} className={className} />;

  const benchByDate = new Map((benchmark ?? []).map((p) => [p.date, p.value]));
  const bench = benchmark ? data.map((p) => benchByDate.get(p.date) ?? null) : null;
  const hasBench = !!bench && bench.filter(isFiniteNumber).length >= 2;

  const s = STYLES[variant];
  const top = 8;
  const right = yLabels ? 44 : 8;
  const bottom = xLabels ? 24 : 6;
  const left = 4;
  const x0 = left;
  const x1 = width - right;
  const y0 = top;
  const y1 = height - bottom;

  const ext = extent([...values, ...(hasBench ? bench! : [])])!;
  const ticks = niceTicks(ext[0], ext[1], yTickCount);
  const domain: [number, number] = [ticks[0], ticks[ticks.length - 1]];
  const x = linearScale([0, data.length - 1], [x0, x1]);
  const y = linearScale(domain, [y1, y0]);
  const pts = (vs: readonly (number | null)[]): (Point | null)[] =>
    vs.map((v, i) => (isFiniteNumber(v) ? [x(i), y(v)] : null));
  const main = pts(values);
  const benchPts = hasBench ? pts(bench!) : null;

  let lastIndex = values.length - 1;
  while (lastIndex >= 0 && !isFiniteNumber(values[lastIndex])) lastIndex--;
  const firstValue = finite[0];
  const lastValue = finite[finite.length - 1];
  const benchFinite = hasBench ? bench!.filter(isFiniteNumber) : [];
  const benchLast = benchFinite[benchFinite.length - 1];

  const summary =
    ariaLabel ??
    `${label} from ${formatDay(data[0].date)} to ${formatDay(data[data.length - 1].date)}: ` +
      `${formatValue(firstValue)} to ${formatValue(lastValue)}` +
      (hasBench ? `; ${benchmarkLabel} ends at ${formatValue(benchLast)}` : "");

  const step = data.length > 1 ? (x1 - x0) / (data.length - 1) : x1 - x0;
  const stride = Math.max(1, Math.ceil(data.length / 300));
  const legendItems: LegendEntry[] = hasBench
    ? [
        { label, swatch: "line", className: s.legendLine, value: formatValue(lastValue) },
        { label: benchmarkLabel, swatch: "dashed", className: s.legendDash, value: formatValue(benchLast) },
      ]
    : [];

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className={SVG_BASE} role="img" aria-label={summary}>
        {grid
          ? ticks.map((t) => (
              <line
                key={t}
                x1={x0}
                x2={x1}
                y1={y(t)}
                y2={y(t)}
                className={s.grid}
                strokeOpacity={s.gridOpacity}
                strokeWidth={1}
                shapeRendering="crispEdges"
              />
            ))
          : null}
        {yLabels
          ? ticks.map((t) => (
              <text
                key={t}
                x={width}
                y={y(t) + 4}
                textAnchor="end"
                fontSize={11}
                className={cn(s.text, "font-mono num")}
                fillOpacity={s.textOpacity}
              >
                {formatNumber(t, Number.isInteger(t) ? 0 : 1)}
              </text>
            ))
          : null}
        {xLabels
          ? timeTicks(data.map((p) => p.date)).map((t) => (
              <text
                key={t.index}
                x={x(t.index)}
                y={height - 4}
                fontSize={11}
                className={cn(s.text, "font-mono")}
                fillOpacity={s.textOpacity}
              >
                {t.label}
              </text>
            ))
          : null}
        {area ? <path d={areaPath(main, y1)} className={s.area} fillOpacity={s.areaOpacity} /> : null}
        {benchPts ? (
          <path
            d={linePath(benchPts)}
            fill="none"
            className={s.bench}
            strokeOpacity={s.benchOpacity}
            strokeWidth={1.5}
            strokeDasharray="4 4"
          />
        ) : null}
        <path
          d={linePath(main)}
          fill="none"
          className={s.line}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {endDot && lastIndex >= 0 ? (
          <>
            {s.halo ? (
              <circle cx={x(lastIndex)} cy={y(values[lastIndex]!)} r={11} className={s.halo} fillOpacity={0.22} />
            ) : null}
            <circle cx={x(lastIndex)} cy={y(values[lastIndex]!)} r={4.5} className={s.dot} strokeWidth={2} />
          </>
        ) : null}
        <g>
          {data.map((p, i) => {
            if (i % stride !== 0) return null;
            const v = p.value;
            const b = bench?.[i];
            return (
              <rect
                key={i}
                x={x(i) - (step * stride) / 2}
                y={y0}
                width={step * stride}
                height={y1 - y0}
                className={cn(s.hover, "opacity-0 hover:opacity-[0.06]")}
              >
                <title>
                  {`${formatDay(p.date)} · ${label} ${isFiniteNumber(v) ? formatValue(v) : "no data"}` +
                    (hasBench ? ` · ${benchmarkLabel} ${isFiniteNumber(b) ? formatValue(b) : "no data"}` : "")}
                </title>
              </rect>
            );
          })}
        </g>
      </svg>
      {legend && legendItems.length ? (
        <Legend items={legendItems} className={cn("text-xs", s.legendText)} valueClassName={s.legendValue} />
      ) : null}
    </figure>
  );
}
