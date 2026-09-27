import { formatPct } from "@/lib/charts/format";
import { areaPath, linePath, type Point } from "@/lib/charts/path";
import { isFiniteNumber, linearScale, niceTicks } from "@/lib/charts/scale";
import { drawdown, maxDrawdown } from "@/lib/charts/series";
import { formatDay, timeTicks } from "@/lib/charts/time";
import { cn } from "@/lib/utils";

import { AXIS_TEXT, ChartEmpty, SVG_BASE } from "./shared";
import type { LinePoint } from "./line-chart";

export type DrawdownChartProps = {
  /** Price or index levels, oldest first. The drawdown is computed from the running peak. */
  data: readonly LinePoint[];
  /** Series name for the accessible summary. */
  label?: string;
  /** Right-side labels (0% and the deepest tick). Default true. */
  yLabels?: boolean;
  /** Month labels under the plot. Default false (the design omits them). */
  xLabels?: boolean;
  /** Label the trough with its value. Default true. */
  markTrough?: boolean;
  width?: number;
  height?: number;
  formatValue?: (fraction: number) => string;
  ariaLabel?: string;
  className?: string;
};

/**
 * Underwater chart: the area between 0 and the drawdown from the running peak, in the loss
 * tone. The deepest point is marked. Fewer than two finite levels -> empty state.
 */
export function DrawdownChart({
  data,
  label = "Drawdown",
  yLabels = true,
  xLabels = false,
  markTrough = true,
  width = 400,
  height = 160,
  formatValue = (f) => formatPct(f, 1),
  ariaLabel,
  className,
}: DrawdownChartProps) {
  const dd = drawdown(data.map((p) => p.value));
  const finite = dd.filter(isFiniteNumber);
  if (finite.length < 2) return <ChartEmpty minHeight={height / 2} className={className} />;

  const worst = maxDrawdown(data.map((p) => p.value))!;
  const right = yLabels ? 48 : 4;
  const bottom = xLabels ? 22 : 4;
  const x0 = 0;
  const x1 = width - right;
  const y0 = 4;
  const y1 = height - bottom;
  const minTick = worst.value < 0 ? niceTicks(worst.value, 0, 3)[0] : -0.01;
  const x = linearScale([0, Math.max(1, data.length - 1)], [x0, x1]);
  const y = linearScale([minTick, 0], [y1, y0]);
  const pts: (Point | null)[] = dd.map((v, i) => (isFiniteNumber(v) ? [x(i), y(v)] : null));
  const current = finite[finite.length - 1];

  const summary =
    ariaLabel ??
    `${label} from running peak: deepest ${formatValue(worst.value)} on ${formatDay(data[worst.index].date)}` +
      `; now ${formatValue(current)}`;

  const tx = x(worst.index);
  // Label beside the trough dot, on whichever side has room, never on the line's descent.
  const anchor = tx > x1 - 70 ? "end" : "start";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn(SVG_BASE, className)}
      role="img"
      aria-label={summary}
    >
      <path d={areaPath(pts, y0)} className="fill-loss" fillOpacity={0.16} />
      <path d={linePath(pts)} fill="none" className="stroke-loss" strokeWidth={1.5} strokeLinejoin="round" />
      <line x1={x0} x2={x1} y1={y0} y2={y0} className="stroke-border" strokeWidth={1} shapeRendering="crispEdges" />
      <line
        x1={x0}
        x2={x1}
        y1={y(minTick)}
        y2={y(minTick)}
        className="stroke-chart-grid"
        strokeWidth={1}
        shapeRendering="crispEdges"
      />
      {yLabels ? (
        <>
          <text x={width} y={y0 + 8} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
            0%
          </text>
          <text x={width} y={y(minTick) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
            {formatPct(minTick, 0)}
          </text>
        </>
      ) : null}
      {xLabels
        ? timeTicks(data.map((p) => p.date)).map((t) => (
            <text key={t.index} x={x(t.index)} y={height - 4} fontSize={11} className={AXIS_TEXT}>
              {t.label}
            </text>
          ))
        : null}
      {markTrough && worst.value < 0 ? (
        <g>
          <circle cx={tx} cy={y(worst.value)} r={3.5} className="fill-loss stroke-surface" strokeWidth={2} />
          <text
            x={anchor === "end" ? tx - 8 : tx + 8}
            y={y(worst.value) + 4}
            textAnchor={anchor}
            fontSize={11}
            className="fill-text font-mono num"
          >
            {formatValue(worst.value)}
          </text>
        </g>
      ) : null}
    </svg>
  );
}
