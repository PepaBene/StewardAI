"use client";

import { useState } from "react";

import { AXIS_TEXT, Legend, SrTable, SVG_BASE, ChartEmpty } from "@/components/charts/shared";
import { linePath, type Point } from "@/lib/charts/path";
import { extent, isFiniteNumber, linearScale, niceTicks } from "@/lib/charts/scale";
import { formatDay, timeTicks } from "@/lib/charts/time";
import { cn } from "@/lib/utils";

/*
 * F25 StepLine: a price (or value) line with the stored fair value drawn over it as a stepped
 * line, which changes only on the days a new fair value was recorded and starts at the first
 * stored value (never back-filled). A vertical cursor marks today; a crosshair reads both values.
 */

export type StepRow = { date: string; value: number | null; fair: number | null };

/** "M x y H x2 V y2 ..." through the steps; a null breaks the line. */
function stepPath(points: readonly (Point | null)[]): string {
  let d = "";
  let prev: Point | null = null;
  for (const p of points) {
    if (!p) {
      prev = null;
      continue;
    }
    d += prev ? ` H${p[0].toFixed(1)} V${p[1].toFixed(1)}` : ` M${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
    prev = p;
  }
  return d.trim();
}

export function StepLineChart({
  rows,
  valueLabel,
  fairLabel,
  format,
  label,
  today,
  className,
}: {
  rows: readonly StepRow[];
  valueLabel: string;
  fairLabel: string;
  format: (v: number) => string;
  label: string;
  today?: string;
  className?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const ext = extent(rows.flatMap((r) => [r.value, r.fair]));
  if (rows.length < 2 || !ext) return <ChartEmpty label="Not enough stored history to draw yet" minHeight={200} className={className} />;
  const W = 720;
  const H = 240;
  const left = 8;
  const right = 60;
  const bottom = H - 22;
  const ticks = niceTicks(ext[0], ext[1], 4);
  const domain: [number, number] = [Math.min(ext[0], ticks[0]), Math.max(ext[1], ticks[ticks.length - 1])];
  const x = linearScale([0, rows.length - 1], [left, W - right]);
  const y = linearScale(domain, [bottom, 10]);
  const valuePts = rows.map((r, i) => (isFiniteNumber(r.value) ? ([x(i), y(r.value)] as Point) : null));
  const fairPts = rows.map((r, i) => (isFiniteNumber(r.fair) ? ([x(i), y(r.fair)] as Point) : null));
  const todayIdx = today ? rows.findIndex((r) => r.date >= today) : -1;
  const xt = timeTicks(rows.map((r) => r.date), 8);
  const h = hover !== null ? rows[hover] : null;
  const firstFair = rows.findIndex((r) => r.fair !== null);

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={SVG_BASE}
        role="img"
        aria-label={label}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const i = Math.round(((px - left) / (W - right - left)) * (rows.length - 1));
          setHover(Math.max(0, Math.min(rows.length - 1, i)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} className="stroke-chart-grid" />
            <text x={W - right + 6} y={y(t) + 4} fontSize={11} className={AXIS_TEXT}>
              {format(t)}
            </text>
          </g>
        ))}
        {xt.map((t) => (
          <text key={t.index} x={x(t.index)} y={H - 4} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
            {t.label}
          </text>
        ))}
        {firstFair > 0 ? (
          <text x={x(firstFair)} y={bottom - 4} fontSize={10} className="fill-text-faint">
            Fair value recorded from here
          </text>
        ) : null}
        <path d={linePath(valuePts)} fill="none" className="stroke-chart-line" strokeWidth={2} strokeLinejoin="round" />
        <path d={stepPath(fairPts)} fill="none" className="stroke-chart-fair-value" strokeWidth={2} />
        {todayIdx >= 0 ? <line x1={x(todayIdx)} x2={x(todayIdx)} y1={6} y2={bottom} className="stroke-text-faint" strokeDasharray="2 3" /> : null}
        {h && hover !== null ? (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={6} y2={bottom} className="stroke-text-muted" />
            {isFiniteNumber(h.value) ? <circle cx={x(hover)} cy={y(h.value)} r={4} className="fill-chart-line stroke-surface" strokeWidth={2} /> : null}
            {isFiniteNumber(h.fair) ? <circle cx={x(hover)} cy={y(h.fair)} r={4} className="fill-chart-fair-value stroke-surface" strokeWidth={2} /> : null}
          </g>
        ) : null}
      </svg>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend
          items={[
            { label: valueLabel, swatch: "line", className: "bg-chart-line" },
            { label: fairLabel, swatch: "line", className: "bg-chart-fair-value" },
          ]}
        />
        <p className="num text-xs text-text-muted" aria-live="polite">
          {h ? `${formatDay(h.date)}: ${valueLabel} ${isFiniteNumber(h.value) ? format(h.value) : "—"}, ${fairLabel} ${isFiniteNumber(h.fair) ? format(h.fair) : "not yet recorded"}` : " "}
        </p>
      </div>
      <SrTable caption={label} columns={["Date", valueLabel, fairLabel]} rows={rows.map((r) => [r.date, isFiniteNumber(r.value) ? format(r.value) : "—", isFiniteNumber(r.fair) ? format(r.fair) : "—"])} />
    </figure>
  );
}

/** Row-sized price line with the fair-value steps, for Check-In and the watchlist. */
export function StepSparkline({ rows, label, className }: { rows: readonly StepRow[]; label?: string; className?: string }) {
  const ext = extent(rows.flatMap((r) => [r.value, r.fair]));
  if (rows.length < 2 || !ext) return <span className={cn("text-xs text-text-faint", className)}>—</span>;
  const W = 96;
  const H = 28;
  const x = linearScale([0, rows.length - 1], [2, W - 2]);
  const y = linearScale(ext[0] === ext[1] ? [ext[0] - 1, ext[1] + 1] : ext, [H - 3, 3]);
  const v = rows.map((r, i) => (isFiniteNumber(r.value) ? ([x(i), y(r.value)] as Point) : null));
  const f = rows.map((r, i) => (isFiniteNumber(r.fair) ? ([x(i), y(r.fair)] as Point) : null));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={cn("h-7 w-24", className)} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <path d={linePath(v)} fill="none" className="stroke-chart-line" strokeWidth={1.5} />
      <path d={stepPath(f)} fill="none" className="stroke-chart-fair-value" strokeWidth={1.5} />
    </svg>
  );
}
