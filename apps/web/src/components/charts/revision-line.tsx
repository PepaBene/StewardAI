import { formatNumber } from "@/lib/charts/format";
import { linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { SrTable } from "./shared";

export type RevisionLinePoint = {
  period: string;
  /** First value on record for the period; null when unknown. */
  first: number | null;
  latest: number | null;
  revised: boolean;
};

export type RevisionLineProps = {
  points: readonly RevisionLinePoint[];
  /** Accessible name, e.g. "German CPI, first value against latest". */
  label: string;
  formatValue?: (v: number) => string;
  /** Optional shared [min, max] so small multiples can be compared on one scale. */
  domain?: readonly [number, number];
  width?: number;
  height?: number;
  className?: string;
};

/**
 * One macro series: the latest value as a solid line and the first value on record as a dashed
 * line behind it, with a square marker on every period where the two differ. Where nothing was
 * revised the dashed line sits under the solid one, so a flat chart honestly reads as "no
 * revisions" rather than hiding them. Unknown values are gaps, never zero.
 */
export function RevisionLine({ points, label, formatValue = (v) => formatNumber(v, 2), domain, width = 240, height = 64, className }: RevisionLineProps) {
  const ext = domain ? ([domain[0], domain[1]] as [number, number]) : extent(points.flatMap((p) => [p.first, p.latest]));
  if (points.length === 0 || !ext) return null;

  const padX = 4;
  const padY = 6;
  const x = linearScale([0, Math.max(1, points.length - 1)], [padX, width - padX]);
  const y = linearScale(ext, [height - padY, padY]);
  const series = (pick: (p: RevisionLinePoint) => number | null): (Point | null)[] =>
    points.map((p, i) => {
      const v = pick(p);
      return isFiniteNumber(v) ? [x(i), y(v)] : null;
    });

  return (
    <div className={cn("flex flex-col", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="block h-16 w-full overflow-visible">
        <path d={linePath(series((p) => p.first))} fill="none" className="stroke-chart-benchmark" strokeWidth={1.5} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        <path d={linePath(series((p) => p.latest))} fill="none" className="stroke-chart-line" strokeWidth={1.75} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {points.map((p, i) =>
          p.revised && isFiniteNumber(p.latest) ? <rect key={p.period} x={x(i) - 2.5} y={y(p.latest) - 2.5} width={5} height={5} className="fill-warning-signal" /> : null,
        )}
      </svg>
      <SrTable
        caption={label}
        columns={["Period", "First value", "Latest value", "Revised"]}
        rows={points.map((p) => [
          p.period,
          isFiniteNumber(p.first) ? formatValue(p.first) : "Unknown",
          isFiniteNumber(p.latest) ? formatValue(p.latest) : "Unknown",
          p.revised ? "Yes" : "No",
        ])}
      />
    </div>
  );
}
