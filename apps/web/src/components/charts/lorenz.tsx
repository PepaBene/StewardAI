import { formatPct } from "@/lib/charts/format";
import { lorenzPoints, topShare } from "@/lib/charts/concentration";
import { cn } from "@/lib/utils";

import { AXIS_TEXT, ChartEmpty, Legend, SrTable, SVG_BASE } from "./shared";

export type LorenzChartProps = {
  /** Holding weights (any scale; normalised here). */
  weights: readonly number[];
  /** Names in the same order as `weights`, for the table twin. */
  names?: readonly string[];
  width?: number;
  height?: number;
  ariaLabel?: string;
  className?: string;
};

/**
 * Concentration curve (spec B31): cumulative share of value against cumulative share of names,
 * largest first. The dashed diagonal is a perfectly even portfolio; the shaded gap between it
 * and the curve is how concentrated the portfolio is. Annotated at 20% of names.
 */
export function LorenzChart({ weights, names, width = 360, height = 240, ariaLabel, className }: LorenzChartProps) {
  const pts = lorenzPoints(weights);
  if (pts.length < 3) return <ChartEmpty minHeight={height / 2} className={className} />;
  const pad = { l: 36, r: 12, t: 12, b: 28 };
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const X = (x: number) => pad.l + x * w;
  const Y = (y: number) => pad.t + (1 - y) * h;
  const curve = pts.map((p, i) => `${i === 0 ? "M" : "L"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const area = `${curve}L${X(1)},${Y(1)}L${X(0)},${Y(0)}Z`;
  const top = topShare(weights, 0.2);
  const n = pts.length - 1;
  const ticks = [0, 0.5, 1];
  const summary = top ? `Top ${top.names} of ${n} names hold ${formatPct(top.share, 0)} of the value` : "";
  const sorted = names
    ? weights.map((v, i) => ({ v, name: names[i] ?? "" })).filter((r) => r.v > 0).sort((a, b) => b.v - a.v)
    : [];
  const total = sorted.reduce((s, r) => s + r.v, 0);

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className={SVG_BASE} role="img" aria-label={ariaLabel ?? `Concentration curve. ${summary}.`}>
        {ticks.map((t) => (
          <g key={t}>
            <text x={pad.l - 6} y={Y(t) + 3} textAnchor="end" className={cn(AXIS_TEXT, "text-[10px]")}>
              {Math.round(t * 100)}%
            </text>
            <text x={X(t)} y={height - 8} textAnchor="middle" className={cn(AXIS_TEXT, "text-[10px]")}>
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        <path d={area} className="fill-accent/10" />
        <line x1={X(0)} y1={Y(0)} x2={X(1)} y2={Y(1)} strokeDasharray="6 4" className="stroke-chart-benchmark" strokeWidth={1.5} />
        <path d={curve} fill="none" className="stroke-accent" strokeWidth={2} strokeLinejoin="round" />
        {top ? (
          <g>
            <circle cx={X(top.names / n)} cy={Y(top.share)} r={4} className="fill-accent stroke-surface" strokeWidth={2} />
            <text x={X(top.names / n) + 8} y={Y(top.share) + 14} className="fill-text-muted text-[11px]">
              Top {top.names} {top.names === 1 ? "name" : "names"} = {formatPct(top.share, 0)} of value
            </text>
          </g>
        ) : null}
      </svg>
      <Legend
        items={[
          { label: "Your portfolio", swatch: "line", className: "bg-accent" },
          { label: "Evenly spread", swatch: "dashed", className: "border-chart-benchmark" },
        ]}
      />
      {sorted.length > 0 ? (
        <SrTable
          caption="Holdings by share of value, largest first"
          columns={["Holding", "Share of value"]}
          rows={sorted.map((r) => [r.name, formatPct(r.v / total, 1)])}
        />
      ) : null}
    </figure>
  );
}
