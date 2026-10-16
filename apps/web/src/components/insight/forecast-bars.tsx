"use client";

import { useId, useState, type ReactNode } from "react";

import { AXIS_TEXT, Legend, SrTable, SVG_BASE } from "@/components/charts/shared";
import { LimitedState } from "@/components/insight/limited-state";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import type { ForecastBar } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F23 ForecastBars: one bar per period, the known part (received or declared) in the history
 * line colour and the estimated part stacked on top in the forecast colour, with a 2px surface gap.
 * A divider marks where the forecast starts. Hover, focus or tap a bar to pin a panel listing the
 * items behind it (`detail`). Bars are buttons, so every period is reachable by keyboard.
 */

export const MIN_NONZERO_PERIODS = 3;
export const LEAD_PERIODS = 12;

/**
 * Index of the first bar drawn: the first non-zero period, or 12 periods before the first estimate,
 * whichever is earlier. Leading empty history is never drawn.
 */
export function forecastStart(bars: readonly Pick<ForecastBar, "total" | "phase">[]): number {
  const firstNonZero = bars.findIndex((b) => b.total > 0);
  if (firstNonZero < 0) return 0;
  const firstEstimate = bars.findIndex((b) => b.phase !== "history");
  if (firstEstimate < 0) return firstNonZero;
  return Math.min(firstNonZero, Math.max(0, firstEstimate - LEAD_PERIODS));
}

/** A bar path square at the baseline with `r` rounding on the data end (top). */
function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function ForecastBars({
  bars: allBars,
  format,
  label,
  detail,
  className,
}: {
  bars: readonly ForecastBar[];
  format: (v: number) => string;
  label: string;
  /** The items behind one bar, shown in the pinned panel. */
  detail?: (bar: ForecastBar) => ReactNode;
  className?: string;
}) {
  const [pinned, setPinned] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const patternId = useId().replace(/:/g, "");
  const bars = allBars.slice(forecastStart(allBars));
  const active = bars.find((b) => b.key === (hover ?? pinned)) ?? null;
  const nonZero = allBars.filter((b) => b.total > 0).length;
  const twin = <SrTable caption={label} columns={["Period", "Received or declared", "Estimated", "Total"]} rows={allBars.map((b) => [b.label, format(b.known), format(b.estimated), format(b.total)])} />;
  if (nonZero < MIN_NONZERO_PERIODS) {
    return (
      <figure className={cn("m-0", className)}>
        <LimitedState need={{ min: MIN_NONZERO_PERIODS, have: nonZero, unit: "periods with a figure" }} />
        {twin}
      </figure>
    );
  }
  const max = Math.max(0, ...bars.map((b) => b.total));
  const maxKey = bars.find((b) => b.total === max)?.key;
  const lastKey = [...bars].reverse().find((b) => b.total > 0)?.key;
  const ticks = niceTicks(0, max > 0 ? max : 1, 4);
  const top = ticks[ticks.length - 1];
  const W = 720;
  const H = 220;
  const left = 52;
  const bottom = H - 24;
  const y = linearScale([0, top], [bottom, 22]);
  const slot = (W - left - 4) / Math.max(1, bars.length);
  const bw = Math.max(2, Math.min(24, slot - 2));
  const firstForecast = bars.findIndex((b) => b.phase !== "history");
  const labelEvery = Math.ceil(bars.length / 12);
  const dividerX = left + firstForecast * slot;

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} aria-hidden="true">
          <defs>
            <pattern id={patternId} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1={0} y1={0} x2={0} y2={6} className="stroke-chart-forecast" strokeWidth={2} />
            </pattern>
          </defs>
          {ticks.map((t) => (
            <text key={t} x={left - 6} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {format(t)}
            </text>
          ))}
          {firstForecast > 0 ? (
            <g>
              <line x1={dividerX} x2={dividerX} y1={6} y2={bottom} className="stroke-text-faint" strokeWidth={1} />
              <text x={dividerX + 4} y={14} fontSize={11} className="fill-text-muted">
                Forecast
              </text>
            </g>
          ) : null}
          {bars.map((b, i) => {
            const x0 = left + i * slot + (slot - bw) / 2;
            const knownTop = y(b.known);
            const estTop = y(b.total);
            const on = active?.key === b.key;
            const estH = Math.max(0, knownTop - estTop - (b.known > 0 ? 2 : 0));
            return (
              <g key={b.key} opacity={active && !on ? 0.55 : 1}>
                {b.known > 0 ? <path d={barPath(x0, knownTop, bw, Math.max(0, bottom - knownTop), b.estimated > 0 ? 0 : 4)} className="fill-chart-line" /> : null}
                {b.estimated > 0 ? (
                  <path d={barPath(x0, estTop, bw, estH, 4)} fill={`url(#${patternId})`} className="stroke-chart-forecast" strokeWidth={1} />
                ) : null}
                {b.key === maxKey || b.key === lastKey ? (
                  <text x={x0 + bw / 2} y={estTop - 4} textAnchor="middle" fontSize={11} className="fill-text">
                    {format(b.total)}
                  </text>
                ) : null}
                {i % labelEvery === 0 ? (
                  <text x={x0 + bw / 2} y={H - 6} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
                    {b.label}
                  </text>
                ) : null}
              </g>
            );
          })}
          <line x1={left} x2={W} y1={bottom} y2={bottom} className="stroke-border" />
        </svg>
        {/* Hit targets: one full-height button per bar. */}
        <div className="absolute inset-0 flex" style={{ paddingLeft: `${(left / W) * 100}%`, paddingRight: `${(4 / W) * 100}%` }}>
          {bars.map((b) => (
            <button
              key={b.key}
              type="button"
              aria-label={`${b.label}: ${format(b.total)}${b.estimated > 0 ? `, of which ${format(b.estimated)} estimated` : ""}`}
              aria-pressed={pinned === b.key}
              onMouseEnter={() => setHover(b.key)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(b.key)}
              onBlur={() => setHover(null)}
              onClick={() => setPinned(pinned === b.key ? null : b.key)}
              className="h-full flex-1 focus-visible:outline-2 focus-visible:outline-accent"
            />
          ))}
        </div>
      </div>
      <Legend
        items={[
          { label: "Received or declared", className: "bg-chart-line" },
          { label: "Estimated", className: "hatch-forecast border border-chart-forecast" },
        ]}
      />
      {active ? (
        <div className="border border-border bg-surface-raised p-3 text-sm" aria-live="polite">
          <p className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="font-semibold">{active.label}</span>
            <span className="num">
              {format(active.total)}
              {active.estimated > 0 ? <span className="text-text-muted"> ({format(active.estimated)} estimated)</span> : null}
            </span>
          </p>
          {detail ? <div className="mt-2">{detail(active)}</div> : null}
          {pinned === active.key ? <p className="mt-1 text-xs text-text-faint">Pinned. Select the bar again to release.</p> : null}
        </div>
      ) : (
        <p className="text-xs text-text-faint">Point at or select a bar to list its payments.</p>
      )}
      {twin}
    </figure>
  );
}
