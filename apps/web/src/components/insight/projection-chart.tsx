"use client";

import { useState } from "react";

import { AXIS_TEXT, ChartEmpty, Legend, SrTable, SVG_BASE } from "@/components/charts/shared";
import { linePath, type Point } from "@/lib/charts/path";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

/*
 * Filed history against scenario paths (insight I6.6): the filed years as a solid line over a
 * shaded "reported" band, then one curve per scenario from the last filed year. The selected
 * scenario is the solid forecast colour; the others are dashed and muted, each labelled at its end.
 */

type P = { year: number; value: number };

export function ProjectionChart({
  past,
  scenarios,
  format,
  label,
}: {
  past: readonly P[];
  scenarios: readonly { name: string; selected: boolean; points: readonly P[] }[];
  format: (v: number) => string;
  label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const all = [...past, ...scenarios.flatMap((s) => s.points)];
  if (all.length < 2) return <ChartEmpty label="No filed history or projection to draw" minHeight={200} />;
  const years = [...new Set(all.map((p) => p.year))].sort((a, b) => a - b);
  const lo = Math.min(0, ...all.map((p) => p.value));
  const hi = Math.max(...all.map((p) => p.value));
  const ticks = niceTicks(lo, hi, 4);
  const W = 720;
  const H = 240;
  const left = 8;
  const right = 72;
  const bottom = H - 22;
  const x = linearScale([years[0], years[years.length - 1]], [left, W - right]);
  const y = linearScale([Math.min(lo, ticks[0]), Math.max(hi, ticks[ticks.length - 1])], [bottom, 10]);
  const lastPast = past.length ? past[past.length - 1] : null;
  const pts = (ps: readonly P[]): Point[] => ps.map((p) => [x(p.year), y(p.value)]);
  const ordered = [...scenarios].sort((a, b) => Number(a.selected) - Number(b.selected));
  const hy = hover !== null ? years[hover] : null;

  return (
    <figure className="m-0 flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={SVG_BASE}
        role="img"
        aria-label={label}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          let best = 0;
          years.forEach((yr, i) => {
            if (Math.abs(x(yr) - px) < Math.abs(x(years[best]) - px)) best = i;
          });
          setHover(best);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {lastPast ? <rect x={left} y={6} width={Math.max(0, x(lastPast.year) - left)} height={bottom - 6} className="fill-surface-muted" /> : null}
        {lastPast ? (
          <text x={left + 4} y={18} fontSize={11} className="fill-text-muted">
            Reported
          </text>
        ) : null}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={left} x2={W - right} y1={y(t)} y2={y(t)} className="stroke-chart-grid" />
            <text x={W - right + 6} y={y(t) + 4} fontSize={11} className={AXIS_TEXT}>
              {format(t)}
            </text>
          </g>
        ))}
        {years.map((yr) => (
          <text key={yr} x={x(yr)} y={H - 4} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
            {String(yr).slice(2)}
          </text>
        ))}
        <path d={linePath(pts(past))} fill="none" className="stroke-chart-line" strokeWidth={2} />
        {ordered.map((s) => {
          const ps = lastPast ? [lastPast, ...s.points] : s.points;
          const end = s.points[s.points.length - 1];
          return (
            <g key={s.name}>
              <path
                d={linePath(pts(ps))}
                fill="none"
                className={s.selected ? "stroke-chart-forecast" : "stroke-text-faint"}
                strokeWidth={s.selected ? 2 : 1.5}
                strokeDasharray={s.selected ? undefined : "4 3"}
              />
              {end ? (
                <text x={x(end.year) + 4} y={y(end.value) + 4} fontSize={11} className={cn(s.selected ? "fill-text font-semibold" : "fill-text-muted")}>
                  {s.name}
                </text>
              ) : null}
            </g>
          );
        })}
        {hy !== null ? <line x1={x(hy)} x2={x(hy)} y1={6} y2={bottom} className="stroke-text-muted" /> : null}
      </svg>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Legend
          items={[
            { label: "Filed", swatch: "line", className: "bg-chart-line" },
            { label: "Selected scenario", swatch: "line", className: "bg-chart-forecast" },
            { label: "Other scenarios", swatch: "dashed", className: "border-text-faint" },
          ]}
        />
        <p className="num text-[11px] text-text-muted" aria-live="polite">
          {hy !== null
            ? [
                `${hy}:`,
                ...(past.find((p) => p.year === hy) ? [`filed ${format(past.find((p) => p.year === hy)!.value)}`] : []),
                ...scenarios.flatMap((s) => {
                  const p = s.points.find((q) => q.year === hy);
                  return p ? [`${s.name} ${format(p.value)}`] : [];
                }),
              ].join(" ")
            : " "}
        </p>
      </div>
      <SrTable
        caption={label}
        columns={["Year", "Filed", ...scenarios.map((s) => s.name)]}
        rows={years.map((yr) => [
          String(yr),
          past.find((p) => p.year === yr) ? format(past.find((p) => p.year === yr)!.value) : "—",
          ...scenarios.map((s) => {
            const p = s.points.find((q) => q.year === yr);
            return p ? format(p.value) : "—";
          }),
        ])}
      />
    </figure>
  );
}
