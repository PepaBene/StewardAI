import { formatNumber } from "@/lib/charts/format";
import { linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { SrTable } from "./shared";

export type VintageFanPoint = {
  /** ISO timestamp the value became known. */
  at: string;
  value: number | null;
  low: number | null;
  high: number | null;
};

export type VintageFanProps = {
  points: readonly VintageFanPoint[];
  /** Accessible name, e.g. "EPS FY2026 consensus by vintage". */
  label: string;
  formatValue?: (v: number) => string;
  width?: number;
  height?: number;
  className?: string;
};

/**
 * One estimate's history: a line through each published value with a pale band for the
 * analyst low-high range where one was published. Vintages are placed by the date they became
 * known, so an uneven revision cadence stays visible. A vintage without a value is a gap, and
 * one without a range simply has no band. The same figures are in a visually hidden table.
 */
export function VintageFan({ points, label, formatValue = (v) => formatNumber(v, 2), width = 220, height = 56, className }: VintageFanProps) {
  const dated = points.map((p) => ({ ...p, t: Date.parse(p.at) })).filter((p) => Number.isFinite(p.t));
  const ext = extent(dated.flatMap((p) => [p.value, p.low, p.high]));
  if (dated.length === 0 || !ext) return null;

  const padX = 4;
  const padY = 5;
  const tExt = extent(dated.map((p) => p.t))!;
  const x = linearScale(tExt, [padX, width - padX]);
  const y = linearScale(ext, [height - padY, padY]);

  const line: (Point | null)[] = dated.map((p) => (isFiniteNumber(p.value) ? [x(p.t), y(p.value)] : null));
  const ranged = dated.filter((p) => isFiniteNumber(p.low) && isFiniteNumber(p.high));
  const band =
    ranged.length >= 2
      ? `M${ranged.map((p) => `${x(p.t)},${y(p.high!)}`).join("L")}L${[...ranged]
          .reverse()
          .map((p) => `${x(p.t)},${y(p.low!)}`)
          .join("L")}Z`
      : null;

  return (
    <div className={cn("flex flex-col", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} className="block h-14 w-full overflow-visible">
        {band ? <path d={band} className="fill-accent" fillOpacity={0.14} /> : null}
        {ranged.length === 1 ? (
          <line
            x1={x(ranged[0].t)}
            x2={x(ranged[0].t)}
            y1={y(ranged[0].high!)}
            y2={y(ranged[0].low!)}
            className="stroke-accent-300"
            strokeWidth={4}
          />
        ) : null}
        <path d={linePath(line)} fill="none" className="stroke-accent" strokeWidth={1.75} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {dated.map((p, i) =>
          isFiniteNumber(p.value) ? <rect key={i} x={x(p.t) - 2.5} y={y(p.value) - 2.5} width={5} height={5} className="fill-accent" /> : null,
        )}
      </svg>
      <SrTable
        caption={label}
        columns={["Known", "Value", "Low", "High"]}
        rows={dated.map((p) => [
          p.at.slice(0, 10),
          isFiniteNumber(p.value) ? formatValue(p.value) : "No value",
          isFiniteNumber(p.low) ? formatValue(p.low) : "Not published",
          isFiniteNumber(p.high) ? formatValue(p.high) : "Not published",
        ])}
      />
    </div>
  );
}
