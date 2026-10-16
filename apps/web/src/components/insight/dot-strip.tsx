"use client";

import { useState } from "react";

import { AXIS_TEXT, Legend, SrTable, SVG_BASE, type LegendEntry } from "@/components/charts/shared";
import { extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

/*
 * F20 DotStrip: every item as a dot on one horizontal scale, ranked, with reference lines
 * (portfolio, benchmark, peer median) and optional tinted zones. Dots that would overlap are
 * stacked upward. One highlighted item reads in the accent; the rest are muted. Hover or focus a
 * dot to read its name and value.
 */

export type StripDot = { key: string; label: string; value: number | null; highlight?: boolean };
export type StripRef = { label: string; value: number; dashed?: boolean };
export type StripZone = { from: number; to: number; tone: "gain" | "loss" | "neutral"; label: string };

const ZONE_FILL = { gain: "fill-gain-subtle", loss: "fill-loss-subtle", neutral: "fill-surface-muted" } as const;

export function DotStrip({
  dots,
  refs = [],
  zones = [],
  format,
  label,
  className,
}: {
  dots: readonly StripDot[];
  refs?: readonly StripRef[];
  zones?: readonly StripZone[];
  format: (v: number) => string;
  label: string;
  className?: string;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const known = dots.filter((d): d is StripDot & { value: number } => d.value !== null && Number.isFinite(d.value));
  const missing = dots.filter((d) => d.value === null);
  const ext = extent([...known.map((d) => d.value), ...refs.map((r) => r.value)]);
  if (!ext) return <p className={cn("text-xs text-text-faint", className)}>No values to place.</p>;
  const [lo, hi] = ext[0] === ext[1] ? [ext[0] - 1, ext[1] + 1] : ext;
  const ticks = niceTicks(lo, hi, 5);
  const domain: [number, number] = [Math.min(lo, ticks[0]), Math.max(hi, ticks[ticks.length - 1])];
  const W = 640;
  const x = linearScale(domain, [16, W - 16]);
  // Stack dots that land within one diameter of each other.
  const sorted = [...known].sort((a, b) => a.value - b.value);
  const rows: number[] = [];
  const lastX: number[] = [];
  for (const d of sorted) {
    const px = x(d.value);
    let row = 0;
    while (lastX[row] !== undefined && px - lastX[row] < 11) row++;
    lastX[row] = px;
    rows.push(row);
  }
  const maxRow = Math.max(0, ...rows);
  const base = 40 + maxRow * 11;
  const H = base + 28;
  const hovered = sorted.find((d) => d.key === hover);
  const legend: LegendEntry[] = refs.map((r) => ({ label: `${r.label} ${format(r.value)}`, swatch: r.dashed ? "dashed" : "line", className: r.dashed ? "border-text-muted" : "bg-text" }));

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} aria-hidden="true">
        {zones.map((z) => (
          <rect key={z.label} x={x(Math.max(domain[0], z.from))} y={8} width={Math.max(0, x(Math.min(domain[1], z.to)) - x(Math.max(domain[0], z.from)))} height={base - 2} className={ZONE_FILL[z.tone]} />
        ))}
        <line x1={16} x2={W - 16} y1={base + 6} y2={base + 6} className="stroke-border" />
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={H - 4} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
            {format(t)}
          </text>
        ))}
        {refs.map((r) => (
          <line key={r.label} x1={x(r.value)} x2={x(r.value)} y1={4} y2={base + 10} className={r.dashed ? "stroke-text-muted" : "stroke-text"} strokeWidth={1.5} strokeDasharray={r.dashed ? "4 3" : undefined} />
        ))}
        {sorted.map((d, i) => {
          const cx = x(d.value);
          const cy = base - rows[i] * 11;
          return (
            <circle
              key={d.key}
              cx={cx}
              cy={cy}
              r={d.highlight || hover === d.key ? 5.5 : 4.5}
              className={cn(d.highlight ? "fill-accent" : "fill-chart-benchmark", "stroke-surface")}
              strokeWidth={2}
            />
          );
        })}
        {hovered ? (
          <text x={x(hovered.value)} y={12} textAnchor="middle" fontSize={12} className="fill-text font-semibold">
            {hovered.label} {format(hovered.value)}
          </text>
        ) : null}
      </svg>
      {/* Keyboard and pointer targets for each dot, larger than the dot itself. */}
      <ul className="flex flex-wrap gap-1 text-[11px]">
        {sorted.map((d) => (
          <li key={d.key}>
            <button
              type="button"
              onMouseEnter={() => setHover(d.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(d.key)}
              onBlur={() => setHover(null)}
              className={cn("border px-1.5 py-0.5", d.highlight ? "border-accent text-accent-ink" : "border-border-soft text-text-muted")}
            >
              {d.label}
            </button>
          </li>
        ))}
      </ul>
      {legend.length ? <Legend items={legend} /> : null}
      {missing.length ? <p className="text-[11px] text-text-faint">Not placed (no value): {missing.map((m) => m.label).join(", ")}</p> : null}
      <SrTable caption={label} columns={["Item", "Value"]} rows={sorted.map((d) => [d.label, format(d.value)])} />
    </figure>
  );
}
