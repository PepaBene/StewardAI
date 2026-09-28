import { AXIS_TEXT, SrTable } from "@/components/charts/shared";
import { formatNumber } from "@/lib/charts/format";
import { areaPath, linePath, type Point } from "@/lib/charts/path";
import { extent, linearScale } from "@/lib/charts/scale";
import { humanizeMetric } from "@/lib/evidence/confidence";
import type { KpiFigure } from "@/lib/research/report";

const W = 720;
const H = 230;
const PAD = { top: 18, right: 12, bottom: 22, left: 12 };

/**
 * A thesis KPI's stored history against its threshold: the line of values with dots and
 * value labels, the threshold dashed in the loss colour with its breach side shaded, and
 * the latest value emphasised. Values are in the KPI's unit.
 */
export function KpiThresholdFigure({ figure, number }: { figure: KpiFigure; number: number }) {
  const { kpi, points } = figure;
  const ceiling = kpi.operator === "<" || kpi.operator === "<=";
  const word = ceiling ? "ceiling" : kpi.operator === "==" ? "target" : "floor";
  const unit = kpi.unit === "%" ? "%" : ` ${kpi.unit}`;
  const fmt = (v: number) => `${formatNumber(v, Math.abs(v) < 10 ? 1 : 0)}${unit}`;
  const ext = extent([...points.map((p) => p.value), kpi.threshold])!;
  const span = ext[1] - ext[0] || Math.abs(ext[1]) || 1;
  const lo = Math.min(ext[0] - span * 0.25, ext[0] >= 0 ? 0 : ext[0]);
  const hi = ext[1] + span * 0.25;
  const y = linearScale([lo, hi], [H - PAD.bottom, PAD.top]);
  const x = linearScale([0, Math.max(1, points.length - 1)], [PAD.left + 14, W - PAD.right - 14]);
  const pts: Point[] = points.map((p, i) => [x(i), y(p.value)]);
  const ty = y(kpi.threshold);
  const bandTop = ceiling ? PAD.top : ty;
  const bandBottom = ceiling ? ty : H - PAD.bottom;
  const title = `Fig. ${number} · ${humanizeMetric(kpi.metric)} vs thesis ${word}`;

  return (
    <figure className="m-0 flex flex-col gap-2.5">
      <figcaption className="font-heading text-lg font-semibold">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full overflow-visible" aria-hidden="true">
        {kpi.operator !== "==" ? (
          <rect x={0} y={bandTop} width={W} height={Math.max(0, bandBottom - bandTop)} className="fill-loss" fillOpacity={0.07} />
        ) : null}
        <path d={areaPath(pts, H - PAD.bottom)} className="fill-chart-line" fillOpacity={0.1} />
        <line x1={0} x2={W} y1={ty} y2={ty} className="stroke-loss" strokeDasharray="4 3" strokeWidth={1} />
        <text x={W} y={ty - 6} textAnchor="end" fontSize={10} className="fill-loss font-semibold">
          Thesis {word} {fmt(kpi.threshold)}
        </text>
        <path d={linePath(pts)} fill="none" className="stroke-chart-line" strokeWidth={2.25} strokeLinejoin="round" />
        {pts.map(([px, py], i) => {
          const last = i === pts.length - 1;
          return (
            <g key={i}>
              <circle cx={px} cy={py} r={3.5} className="fill-surface stroke-chart-line" strokeWidth={2} />
              <text
                x={px}
                y={py - 9}
                textAnchor="middle"
                fontSize={10}
                className={last ? "fill-accent-ink font-bold num" : "fill-text-muted num"}
              >
                {fmt(points[i].value)}
              </text>
              <text x={px} y={H - 6} textAnchor="middle" fontSize={10} className={AXIS_TEXT}>
                {points[i].label}
              </text>
            </g>
          );
        })}
      </svg>
      <SrTable
        caption={`${title}: ${kpi.operator} ${fmt(kpi.threshold)}`}
        columns={["Period", "Value"]}
        rows={points.map((p) => [p.label, fmt(p.value)])}
      />
    </figure>
  );
}
