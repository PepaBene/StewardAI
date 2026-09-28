import { radarLayout } from "@/lib/charts/arc";
import { formatPct } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { ChartEmpty, Legend, SrTable, SVG_BASE, type LegendEntry } from "./shared";

export type RadarSeries = {
  label: string;
  /** One value per axis, on 0..max. A series with any missing value is not drawn. */
  values: readonly (number | null)[];
  /** Draw as a dashed outline (comparison). Default: the second series is dashed. */
  dashed?: boolean;
  /**
   * A dashed comparison that may leave axes null (unknown there): drawn through the known
   * axes only, as open segments between neighbouring known axes plus a small mark on each,
   * never closed through an unknown one. Needs at least one known value.
   */
  partial?: boolean;
};

export type RadarProps = {
  /** Axis names, clockwise from 12 o'clock. Needs at least 3. */
  axes: readonly string[];
  /** One or two series; the first solid (accent, filled), the second a dashed comparison. */
  series: readonly RadarSeries[];
  /** Value at the rim. Default 1. */
  max?: number;
  /** Legend under the chart (shown when two series are drawn). Default true. */
  legend?: boolean;
  formatValue?: (value: number) => string;
  /** Name for the accessible table caption. */
  label?: string;
  className?: string;
};

/**
 * Radar/spider chart for a handful of normalised scores: 4 hairline rings, spokes, a filled
 * primary polygon with dots and an optional dashed comparison. Values are also in a visually
 * hidden table.
 */
export function Radar({
  axes,
  series,
  max = 1,
  legend = true,
  formatValue = (v) => formatPct(v, 0),
  label = "Radar",
  className,
}: RadarProps) {
  const drawable = series
    .slice(0, 2)
    .filter(
      (s, i) =>
        s.values.length === axes.length &&
        (s.values.every(isFiniteNumber) || (i > 0 && s.partial && s.values.some(isFiniteNumber))),
    );
  if (axes.length < 3 || drawable.length === 0 || !(max > 0)) {
    return <ChartEmpty minHeight={180} className={className} />;
  }

  const W = 320;
  const H = 250;
  const r = 92;
  const R = radarLayout(axes.length, W / 2, H / 2, r);
  const frac = (vs: readonly (number | null)[]) => vs.map((v) => (v as number) / max);
  // Open segments between neighbouring known values of a partial series.
  const partialPath = (vs: readonly (number | null)[]) =>
    vs
      .map((v, i) => {
        const next = vs[(i + 1) % vs.length];
        if (!isFiniteNumber(v) || !isFiniteNumber(next)) return "";
        const [x1, y1] = R.point(i, v / max);
        const [x2, y2] = R.point((i + 1) % vs.length, next / max);
        return `M${x1.toFixed(1)},${y1.toFixed(1)}L${x2.toFixed(1)},${y2.toFixed(1)}`;
      })
      .join("");
  const [primary, compare] = drawable;
  const dashedIdx = drawable.map((s, i) => s.dashed ?? i === 1);

  const legendItems: LegendEntry[] =
    drawable.length > 1
      ? drawable.map((s, i) =>
          dashedIdx[i]
            ? { label: s.label, swatch: "dashed" as const, className: "border-text-muted" }
            : { label: s.label, swatch: "square" as const, className: "bg-accent" },
        )
      : [];

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} className={cn(SVG_BASE, "mx-auto max-w-[400px]")} aria-hidden="true">
        {[0.25, 0.5, 0.75, 1].map((k) => (
          <polygon key={k} points={R.ring(k)} fill="none" className="stroke-border" strokeWidth={1} />
        ))}
        {axes.map((_, i) => {
          const [x, y] = R.spoke(i);
          return <line key={i} x1={W / 2} y1={H / 2} x2={x} y2={y} className="stroke-border" strokeWidth={1} />;
        })}
        {drawable.map((s, i) =>
          dashedIdx[i] && !s.values.every(isFiniteNumber) ? (
            <g key={i} className="stroke-text-muted" fill="none">
              <path d={partialPath(s.values)} strokeWidth={1.25} strokeDasharray="3 3" />
              {s.values.map((v, j) => {
                if (!isFiniteNumber(v)) return null;
                const [x, y] = R.point(j, v / max);
                return <circle key={j} cx={x} cy={y} r={2} strokeWidth={1.25} />;
              })}
            </g>
          ) : dashedIdx[i] ? (
            <polygon
              key={i}
              points={R.polygon(frac(s.values))}
              fill="none"
              className="stroke-text-muted"
              strokeWidth={1.25}
              strokeDasharray="3 3"
            />
          ) : (
            <g key={i}>
              <polygon
                points={R.polygon(frac(s.values))}
                className="fill-accent stroke-accent"
                fillOpacity={0.18}
                strokeWidth={2}
                strokeLinejoin="round"
              />
              {frac(s.values).map((f, j) => {
                const [x, y] = R.point(j, f);
                return (
                  <circle key={j} cx={x} cy={y} r={3.5} className="fill-accent">
                    <title>{`${axes[j]} · ${s.label} ${formatValue(s.values[j] as number)}`}</title>
                  </circle>
                );
              })}
            </g>
          ),
        )}
        {axes.map((a, i) => {
          const l = R.label(i, 16);
          return (
            <text key={i} x={l.x} y={l.y + 4} textAnchor={l.anchor} fontSize={12} className="fill-text-muted">
              {a}
            </text>
          );
        })}
      </svg>
      {legend && legendItems.length ? <Legend items={legendItems} /> : null}
      <SrTable
        caption={label}
        columns={["Axis", primary.label, ...(compare ? [compare.label] : [])]}
        rows={axes.map((a, i) => [
          a,
          formatValue(primary.values[i] as number),
          ...(compare ? [isFiniteNumber(compare.values[i]) ? formatValue(compare.values[i] as number) : "—"] : []),
        ])}
      />
    </figure>
  );
}
