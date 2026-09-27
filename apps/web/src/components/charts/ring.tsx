import type { ReactNode } from "react";

import { clamp01, gaugeArcs, ringDash } from "@/lib/charts/arc";
import { formatNumber } from "@/lib/charts/format";
import { isFiniteNumber } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { SVG_BASE, TONE_STROKE, type ChartTone } from "./shared";

export type RingProps = {
  /** Current value; null/non-finite renders an empty track with "—". */
  value: number | null;
  /** Full-circle value. Default 100. */
  max?: number;
  /** Centre text. Default: the rounded value. Pass "" for none. */
  label?: ReactNode;
  /** Arc colour. Default "accent". */
  tone?: ChartTone;
  /** Rendered diameter in px. Default 46. */
  size?: number;
  /** Ring thickness in viewBox units (box is 40). Default 4. */
  thickness?: number;
  /** Accessible name, e.g. "Thesis score". The value and max are appended. */
  ariaLabel?: string;
  formatValue?: (value: number) => string;
  className?: string;
};

/**
 * Single-value progress ring (thesis score 88, 31/31 checks, 92% coverage). Exposed as a
 * meter to assistive tech.
 */
export function Ring({
  value,
  max = 100,
  label,
  tone = "accent",
  size = 46,
  thickness = 4,
  ariaLabel = "Progress",
  formatValue = (v) => formatNumber(v, 0),
  className,
}: RingProps) {
  const has = isFiniteNumber(value) && max > 0;
  const fraction = has ? clamp01(value / max) : 0;
  const text = label ?? (has ? formatValue(value) : "—");
  const textLen = typeof text === "string" ? text.length : 2;
  const fontSize = Math.min(14, 28 / Math.max(2, textLen * 0.62));
  const r = 20 - thickness / 2 - 1;

  return (
    <svg
      viewBox="0 0 40 40"
      width={size}
      height={size}
      className={cn("block shrink-0", className)}
      role="meter"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={has ? value : undefined}
      aria-valuetext={has ? `${formatValue(value)} of ${formatValue(max)}` : "No data"}
    >
      <circle cx={20} cy={20} r={r} fill="none" className="stroke-border" strokeWidth={thickness} />
      {fraction > 0 ? (
        <circle
          cx={20}
          cy={20}
          r={r}
          fill="none"
          className={TONE_STROKE[tone]}
          strokeWidth={thickness}
          strokeDasharray={ringDash(fraction, r)}
          transform="rotate(-90 20 20)"
        />
      ) : null}
      {text !== "" ? (
        <text
          x={20}
          y={20 + fontSize * 0.36}
          textAnchor="middle"
          fontSize={fontSize}
          className="fill-text font-heading font-semibold num"
        >
          {text}
        </text>
      ) : null}
    </svg>
  );
}

/** Alias: the same component under the name used in the design notes. */
export const ProgressRing = Ring;

export type GaugeProps = {
  /** Current value; null renders the track only, with "—". */
  value: number | null;
  /** Value at the right end. The left end is 0. */
  max: number;
  /** Big centre text. Default: the formatted value. */
  label?: ReactNode;
  /** Small caption under the figure (e.g. "of 12 positions"). */
  sublabel?: string;
  /** Captions under the two ends (e.g. "One position" / "Evenly spread"). */
  minLabel?: string;
  maxLabel?: string;
  tone?: ChartTone;
  ariaLabel?: string;
  formatValue?: (value: number) => string;
  className?: string;
};

const GAUGE_FILL: Record<ChartTone, string> = {
  accent: "fill-accent",
  muted: "fill-chart-benchmark",
  gain: "fill-gain",
  loss: "fill-loss",
  warning: "fill-warning-signal",
};

/** Half-circle gauge for one value on a 0..max scale, with quarter ticks. */
export function Gauge({
  value,
  max,
  label,
  sublabel,
  minLabel,
  maxLabel,
  tone = "accent",
  ariaLabel = "Gauge",
  formatValue = (v) => formatNumber(v, 1),
  className,
}: GaugeProps) {
  const has = isFiniteNumber(value) && max > 0;
  const arcs = gaugeArcs(has ? value / max : 0, { cx: 120, cy: 120, r0: 78, r1: 104 });
  const text = label ?? (has ? formatValue(value) : "—");

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <svg
        viewBox="0 0 240 140"
        className={SVG_BASE}
        role="meter"
        aria-label={ariaLabel}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={has ? value : undefined}
        aria-valuetext={has ? `${formatValue(value)} of ${formatValue(max)}` : "No data"}
      >
        <path d={arcs.track} className="fill-surface-muted stroke-border" strokeWidth={1} />
        {arcs.value ? <path d={arcs.value} className={GAUGE_FILL[tone]} /> : null}
        {arcs.ticks.map((t, i) => (
          <line key={i} x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} className="stroke-text-faint" strokeWidth={1} />
        ))}
        <text
          x={120}
          y={sublabel ? 112 : 118}
          textAnchor="middle"
          fontSize={38}
          className="fill-text font-heading font-semibold"
        >
          {text}
        </text>
        {sublabel ? (
          <text x={120} y={132} textAnchor="middle" fontSize={11} className="fill-text-muted">
            {sublabel}
          </text>
        ) : null}
      </svg>
      {minLabel || maxLabel ? (
        <div className="flex justify-between text-[11px] text-text-muted">
          <span>{minLabel}</span>
          <span>{maxLabel}</span>
        </div>
      ) : null}
    </div>
  );
}
