import { formatCompact } from "@/lib/charts/format";
import { bandScale, linearScale } from "@/lib/charts/scale";
import { waterfallBars, waterfallExtent, type WaterfallStepInput } from "@/lib/charts/waterfall";
import { cn } from "@/lib/utils";

import { AXIS_TEXT, ChartEmpty, SrTable, SVG_BASE } from "./shared";

export type WaterfallStep = WaterfallStepInput & {
  /** A balancing figure worked out from the others, drawn dashed so it is never read as reported. */
  derived?: boolean;
};

export type WaterfallChartProps = {
  steps: readonly WaterfallStep[];
  width?: number;
  height?: number;
  formatValue?: (value: number) => string;
  /** Accessible name, e.g. "Cash conversion, FY2025". */
  label: string;
  className?: string;
};

/**
 * Waterfall from a starting total through signed steps to an ending total. Totals are solid
 * accent, increases gain, decreases loss-outlined, and a derived (balancing) step is
 * dashed. Direction is also spelled out in the hidden table, so colour is never the only cue.
 */
export function WaterfallChart({ steps, width = 520, height = 200, formatValue = formatCompact, label, className }: WaterfallChartProps) {
  const bars = waterfallBars(steps);
  const ext = waterfallExtent(bars);
  if (!ext || bars.length === 0) return <ChartEmpty minHeight={height / 2} className={className} />;

  const padTop = 16;
  const padBottom = 30;
  const y = linearScale(ext[0] === ext[1] ? [ext[0] - 1, ext[1] + 1] : ext, [height - padBottom, padTop]);
  const band = bandScale(bars.length, [8, width - 8], { paddingInner: 0.3, maxBandwidth: 56 });
  const derived = new Set(steps.filter((s) => s.derived).map((s) => s.id));

  return (
    <div className={cn("flex flex-col", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className={SVG_BASE}>
        <line x1={0} x2={width} y1={y(0)} y2={y(0)} className="stroke-border" strokeWidth={1} />
        {bars.map((b, i) => {
          const x0 = band.x(i);
          const w = band.bandwidth;
          const top = y(b.high);
          const h = Math.max(1, y(b.low) - y(b.high));
          const next = bars[i + 1];
          const up = b.kind === "delta" && b.value >= 0;
          const down = b.kind === "delta" && b.value < 0;
          return (
            <g key={b.id}>
              <rect
                x={x0}
                y={top}
                width={w}
                height={h}
                className={cn(
                  b.kind === "total" ? "fill-accent" : up ? "fill-gain" : "fill-loss-subtle",
                  down && "stroke-loss",
                  derived.has(b.id) && "stroke-text-muted",
                )}
                strokeWidth={down || derived.has(b.id) ? 1.25 : 0}
                strokeDasharray={derived.has(b.id) ? "4 3" : undefined}
              />
              {next ? (
                <line x1={x0 + w} x2={band.x(i + 1)} y1={y(b.level)} y2={y(b.level)} className="stroke-border" strokeDasharray="2 2" />
              ) : null}
              <text x={x0 + w / 2} y={top - 4} textAnchor="middle" className={AXIS_TEXT}>
                {b.kind === "delta" && b.value > 0 ? "+" : ""}
                {formatValue(b.value)}
              </text>
              <text x={x0 + w / 2} y={height - 10} textAnchor="middle" className={AXIS_TEXT}>
                {b.label}
              </text>
            </g>
          );
        })}
      </svg>
      <SrTable
        caption={label}
        columns={["Step", "Amount", "Running total", "Kind"]}
        rows={bars.map((b) => [
          b.label,
          formatValue(b.value),
          formatValue(b.level),
          derived.has(b.id) ? "Balancing figure" : b.kind === "total" ? "Total" : b.value >= 0 ? "Increase" : "Decrease",
        ])}
      />
    </div>
  );
}
