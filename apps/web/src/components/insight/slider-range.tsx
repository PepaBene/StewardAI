"use client";

import { useMemo } from "react";

import { niceRound, tickScale } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F30 SliderRange: a dual-handle range on a per-metric non-linear tick scale (each gap between
 * ticks gets an equal share of the track). Two native range inputs share one track, so arrows,
 * Page Up/Down and screen readers work without custom key handling. Either end can be open.
 */

const STEPS = 1000;

export function SliderRange({
  ticks,
  value,
  onChange,
  format,
  label,
  className,
}: {
  /** Strictly increasing tick values that set the scale. */
  ticks: readonly number[];
  /** [min, max]; null leaves that end open. */
  value: readonly [number | null, number | null];
  onChange: (next: [number | null, number | null]) => void;
  format: (v: number) => string;
  label: string;
  className?: string;
}) {
  const scale = useMemo(() => tickScale(ticks), [ticks]);
  const lo = value[0] === null ? 0 : Math.round(scale.toPos(value[0]) * STEPS);
  const hi = value[1] === null ? STEPS : Math.round(scale.toPos(value[1]) * STEPS);
  const toValue = (pos: number) => niceRound(scale.toValue(pos / STEPS));
  const text = (v: number | null, end: "min" | "max") => (v === null ? (end === "min" ? "Any" : "Any") : format(v));

  return (
    <div className={cn("flex flex-col gap-1", className)} role="group" aria-label={label}>
      <div className="flex justify-between text-xs">
        <span className="num">{text(value[0], "min")}</span>
        <span className="num">{text(value[1], "max")}</span>
      </div>
      <div className="relative h-6">
        <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 bg-surface-muted" />
        <div className="absolute top-1/2 h-1 -translate-y-1/2 bg-accent" style={{ left: `${(lo / STEPS) * 100}%`, width: `${((hi - lo) / STEPS) * 100}%` }} />
        <input
          type="range"
          min={0}
          max={STEPS}
          value={lo}
          aria-label={`${label}, minimum`}
          aria-valuetext={text(value[0], "min")}
          onChange={(e) => {
            const p = Math.min(Number(e.target.value), hi);
            onChange([p <= 0 ? null : toValue(p), value[1]]);
          }}
          className="range-thumb pointer-events-none absolute inset-0 w-full appearance-none bg-transparent"
        />
        <input
          type="range"
          min={0}
          max={STEPS}
          value={hi}
          aria-label={`${label}, maximum`}
          aria-valuetext={text(value[1], "max")}
          onChange={(e) => {
            const p = Math.max(Number(e.target.value), lo);
            onChange([value[0], p >= STEPS ? null : toValue(p)]);
          }}
          className="range-thumb pointer-events-none absolute inset-0 w-full appearance-none bg-transparent"
        />
      </div>
      <div className="relative h-4 text-xs text-text-faint">
        {ticks.map((t, i) => (
          <span key={t} className="num absolute -translate-x-1/2" style={{ left: `${(i / Math.max(1, ticks.length - 1)) * 100}%` }}>
            {format(t)}
          </span>
        ))}
      </div>
    </div>
  );
}
