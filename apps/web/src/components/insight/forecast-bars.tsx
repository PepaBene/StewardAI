"use client";

import { useState, type ReactNode } from "react";

import { AXIS_TEXT, Legend, SrTable, SVG_BASE } from "@/components/charts/shared";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import type { ForecastBar } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * F23 ForecastBars: one bar per period, the known part (received or declared) in the history
 * line colour and the estimated part stacked on top in the forecast colour, with a 2px surface gap.
 * A divider marks where the forecast starts. Hover, focus or tap a bar to pin a panel listing the
 * items behind it (`detail`). Bars are buttons, so every period is reachable by keyboard.
 */

export function ForecastBars({
  bars,
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
  const active = bars.find((b) => b.key === (hover ?? pinned)) ?? null;
  const max = Math.max(0, ...bars.map((b) => b.total));
  const ticks = niceTicks(0, max > 0 ? max : 1, 4);
  const top = ticks[ticks.length - 1];
  const W = 720;
  const H = 220;
  const left = 52;
  const bottom = H - 24;
  const y = linearScale([0, top], [bottom, 12]);
  const slot = (W - left - 4) / Math.max(1, bars.length);
  const bw = Math.max(2, Math.min(28, slot - 3));
  const firstForecast = bars.findIndex((b) => b.phase !== "history");
  const labelEvery = Math.ceil(bars.length / 12);

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} aria-hidden="true">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={left} x2={W} y1={y(t)} y2={y(t)} className="stroke-chart-grid" />
              <text x={left - 6} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
                {format(t)}
              </text>
            </g>
          ))}
          {firstForecast > 0 ? (
            <g>
              <line x1={left + firstForecast * slot - 1} x2={left + firstForecast * slot - 1} y1={4} y2={bottom} className="stroke-border" strokeDasharray="3 3" />
              <text x={left + firstForecast * slot + 4} y={12} fontSize={11} className="fill-text-muted">
                Forecast
              </text>
            </g>
          ) : null}
          {bars.map((b, i) => {
            const x0 = left + i * slot + (slot - bw) / 2;
            const knownTop = y(b.known);
            const estTop = y(b.total);
            const on = active?.key === b.key;
            return (
              <g key={b.key} opacity={active && !on ? 0.55 : 1}>
                {b.known > 0 ? <rect x={x0} y={knownTop} width={bw} height={Math.max(0, bottom - knownTop)} rx={Math.min(3, bw / 3)} className="fill-chart-line" /> : null}
                {b.estimated > 0 ? (
                  <rect x={x0} y={estTop} width={bw} height={Math.max(0, knownTop - estTop - (b.known > 0 ? 2 : 0))} rx={Math.min(3, bw / 3)} className="fill-chart-forecast" />
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
          { label: "Estimated", className: "bg-chart-forecast" },
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
          {pinned === active.key ? <p className="mt-1 text-[11px] text-text-faint">Pinned. Select the bar again to release.</p> : null}
        </div>
      ) : (
        <p className="text-[11px] text-text-faint">Point at or select a bar to list its payments.</p>
      )}
      <SrTable caption={label} columns={["Period", "Received or declared", "Estimated", "Total"]} rows={bars.map((b) => [b.label, format(b.known), format(b.estimated), format(b.total)])} />
    </figure>
  );
}
