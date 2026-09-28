import { formatNumber } from "@/lib/charts/format";
import { areaPath, linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { TONE_FILL, TONE_STROKE, type ChartTone } from "./shared";

export type SparklineProps = {
  /** Values in time order; null is a gap. Fewer than two finite values renders nothing. */
  values: readonly (number | null)[];
  /** Also shade the area under the line (a ~10% wash). */
  area?: boolean;
  /**
   * Colour: a fixed tone, or "direction" for gain when the last value is ≥ the first and loss
   * otherwise. Default "accent".
   */
  tone?: ChartTone | "direction";
  /** viewBox size; the SVG stretches to its box (stroke width stays constant). */
  width?: number;
  height?: number;
  /**
   * Accessible name, e.g. "NVDA, 30 days". When set, the sparkline is an image with a
   * start/end summary; when omitted it is decorative (aria-hidden) — use that when the
   * figure beside it already says the same thing.
   */
  label?: string;
  formatValue?: (value: number) => string;
  /** Sizing classes; default fills the width at 24px tall. */
  className?: string;
};

/** A word-sized line (optionally with area) for tables and stat tiles. */
export function Sparkline({
  values,
  area = false,
  tone = "accent",
  width = 80,
  height = 24,
  label,
  formatValue = (v) => formatNumber(v, 2),
  className,
}: SparklineProps) {
  const finite = values.filter(isFiniteNumber);
  const ext = extent(values);
  if (finite.length < 2 || !ext) return null;

  const first = finite[0];
  const last = finite[finite.length - 1];
  const resolved: ChartTone = tone === "direction" ? (last >= first ? "gain" : "loss") : tone;
  const pad = 2;
  const x = linearScale([0, Math.max(1, values.length - 1)], [pad, width - pad]);
  const y = linearScale(ext, [height - pad, pad]);
  const points: (Point | null)[] = values.map((v, i) => (isFiniteNumber(v) ? [x(i), y(v)] : null));

  const a11y = label
    ? {
        role: "img" as const,
        "aria-label": `${label}: from ${formatValue(first)} to ${formatValue(last)}, ${
          last > first ? "up" : last < first ? "down" : "unchanged"
        }`,
      }
    : { "aria-hidden": true as const };

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={cn("block h-6 w-full", className)}
      {...a11y}
    >
      {area ? (
        <path d={areaPath(points, height)} className={TONE_FILL[resolved]} fillOpacity={0.12} />
      ) : null}
      <path
        d={linePath(points)}
        fill="none"
        className={TONE_STROKE[resolved]}
        strokeWidth={1.75}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/** A sparkline with its area shaded (the stat-tile form in the design). */
export function SparkArea(props: Omit<SparklineProps, "area">) {
  return <Sparkline {...props} area />;
}
