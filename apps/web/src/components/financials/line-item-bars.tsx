import { SVG_BASE, AXIS_TEXT } from "@/components/charts/shared";
import { bandScale, extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { formatNumber } from "@/lib/charts/format";
import type { LinePoint } from "@/lib/financials/logic";

const W = 240;
const H = 96;

/**
 * One statement line across fiscal periods as bars from a zero baseline. A restated value
 * carries an "R" and an outline (shape, not just colour); a period with no value under the
 * chosen version (not yet filed on the as-of date) is a dashed empty slot, never a zero bar.
 */
export function LineItemBars({ points, unit, label, format = (v) => formatNumber(v, 0) }: { points: readonly LinePoint[]; unit: string; label: string; format?: (v: number) => string }) {
  const ext = extent([...points.map((p) => p.value), 0]);
  if (!ext || !points.some((p) => isFiniteNumber(p.value))) {
    return <div className="flex h-24 items-center justify-center border border-dashed border-border text-xs text-text-muted">Not filed yet under this view</div>;
  }
  const top = 14;
  const bottom = 18;
  const y = linearScale([ext[0], ext[1] === ext[0] ? ext[0] + 1 : ext[1]], [H - bottom, top]);
  const band = bandScale(points.length, [0, W], { paddingInner: 0.28, maxBandwidth: 32 });
  const summary = `${label} (${unit}): ${points.map((p) => `${p.period} ${p.value === null ? "not filed" : format(p.value)}${p.restated ? " restated" : ""}`).join(", ")}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={summary}>
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="stroke-border" strokeWidth={1} shapeRendering="crispEdges" />
      {points.map((p, i) => {
        const bx = band.x(i);
        const cx = bx + band.bandwidth / 2;
        if (!isFiniteNumber(p.value)) {
          return (
            <g key={p.period}>
              <rect x={bx} y={top} width={band.bandwidth} height={H - bottom - top} className="fill-none stroke-border" strokeDasharray="2 2" />
              <text x={cx} y={H - bottom - 4} textAnchor="middle" fontSize={9} className="fill-text-muted">
                —
              </text>
              <text x={cx} y={H - 4} textAnchor="middle" fontSize={9} className={AXIS_TEXT}>
                {p.period.slice(2)}
              </text>
            </g>
          );
        }
        const yTop = Math.min(y(p.value), y(0));
        const h = Math.max(1, Math.abs(y(p.value) - y(0)));
        return (
          <g key={p.period}>
            <rect
              x={bx}
              y={yTop}
              width={band.bandwidth}
              height={h}
              className={p.restated ? "fill-accent-soft stroke-accent" : "fill-accent"}
              strokeWidth={p.restated ? 1.5 : 0}
            >
              <title>{`${p.period}: ${format(p.value)} ${unit}${p.restated ? ` (restated, version ${p.version})` : ""}`}</title>
            </rect>
            <text x={cx} y={(p.value >= 0 ? yTop : yTop + h) + (p.value >= 0 ? -3 : 10)} textAnchor="middle" fontSize={9} className="fill-text-muted num">
              {format(p.value)}
              {p.restated ? " R" : ""}
            </text>
            <text x={cx} y={H - 4} textAnchor="middle" fontSize={9} className={AXIS_TEXT}>
              {p.period.slice(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
