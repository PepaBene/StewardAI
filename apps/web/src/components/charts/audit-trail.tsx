import { linePath, type Point } from "@/lib/charts/path";
import { isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { SrTable } from "./shared";

export type AuditTrailPoint = {
  /** ISO timestamp of the audit. */
  at: string;
  /** Share in [0, 1]; null when the audit sampled nothing (a gap, never zero). */
  value: number | null;
  /** Sample size behind the value, shown in the hidden table. */
  sample: number | null;
};

export type AuditTrailProps = {
  points: readonly AuditTrailPoint[];
  /** Accessible name, e.g. "Evidence precision by audit". */
  label: string;
  /** Dashed reference line in [0, 1], e.g. the precision a signal must reach to publish. */
  threshold?: number | null;
  width?: number;
  height?: number;
  className?: string;
};

const pct = (v: number) => `${Math.round(v * 100)}%`;

/**
 * Audit results over time on a fixed 0-100% axis. Audits are placed by date, so a long gap
 * between reviews is visible, and each audit is a square marker so a sparse history reads as
 * what it is. Marker area grows with sample size to show how much each audit rests on. A single
 * audit draws one marker and no line; a null value leaves a gap.
 */
export function AuditTrail({ points, label, threshold = null, width = 280, height = 84, className }: AuditTrailProps) {
  const dated = points.map((p) => ({ ...p, t: Date.parse(p.at) })).filter((p) => Number.isFinite(p.t));
  if (dated.length === 0) return null;

  const padX = 8;
  const padTop = 6;
  const padBottom = 14;
  const t0 = Math.min(...dated.map((p) => p.t));
  const t1 = Math.max(...dated.map((p) => p.t));
  const x = (t: number) => (t1 === t0 ? width / 2 : padX + ((t - t0) / (t1 - t0)) * (width - 2 * padX));
  const y = linearScale([0, 1], [height - padBottom, padTop]);
  const maxSample = Math.max(1, ...dated.map((p) => p.sample ?? 0));
  const half = (s: number | null) => 2.5 + 2.5 * Math.sqrt((s ?? 0) / maxSample);

  const line: (Point | null)[] = dated.map((p) => (isFiniteNumber(p.value) ? [x(p.t), y(p.value)] : null));
  const day = (t: number) => new Date(t).toISOString().slice(0, 10);

  return (
    <div className={cn("flex flex-col", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="block h-[84px] w-full overflow-visible">
        {[0, 0.5, 1].map((g) => (
          <line key={g} x1={0} x2={width} y1={y(g)} y2={y(g)} className="stroke-border-soft" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        ))}
        {isFiniteNumber(threshold) ? (
          <line x1={0} x2={width} y1={y(threshold)} y2={y(threshold)} className="stroke-chart-benchmark" strokeWidth={1.25} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
        ) : null}
        {dated.length > 1 ? (
          <path d={linePath(line)} fill="none" className="stroke-chart-line" strokeWidth={1.5} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        ) : null}
        {dated.map((p) =>
          isFiniteNumber(p.value) ? (
            <rect key={`${p.at}`} x={x(p.t) - half(p.sample)} y={y(p.value) - half(p.sample)} width={2 * half(p.sample)} height={2 * half(p.sample)} className="fill-chart-line" />
          ) : (
            <line key={`${p.at}`} x1={x(p.t)} x2={x(p.t)} y1={height - padBottom} y2={height - padBottom - 6} className="stroke-text-faint" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          ),
        )}
        <text x={0} y={height - 2} className="fill-text-faint font-mono text-[9px]">
          {day(t0)}
        </text>
        {t1 !== t0 ? (
          <text x={width} y={height - 2} textAnchor="end" className="fill-text-faint font-mono text-[9px]">
            {day(t1)}
          </text>
        ) : null}
      </svg>
      <SrTable
        caption={label}
        columns={["Audited", "Share correct", "Sample"]}
        rows={dated.map((p) => [day(p.t), isFiniteNumber(p.value) ? pct(p.value) : "Nothing sampled", p.sample === null ? "Unknown" : String(p.sample)])}
      />
    </div>
  );
}
