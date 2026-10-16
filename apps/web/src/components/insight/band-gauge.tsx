import { LimitedState } from "@/components/insight/limited-state";
import { linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

/*
 * F21 BandGauge (visual spec B20): a 16 px band with zones (valuation: under / about right / over;
 * risk: a continuous gradient; budget: on track / tight / over), zone labels under it, and a marker
 * that always carries a label. Beyond the scale the marker clamps to the edge with an arrow. It is
 * never drawn without a marker value: the LimitedState line takes its place.
 */

export type GaugeZone = { label: string; to: number; tone: "gain" | "neutral" | "loss" };

/** Valuation scale of B20: price vs fair value from -60% to +60%, three zones split at ±20% (D.1). */
export const VALUATION_RANGE = { min: -0.6, max: 0.6 } as const;
export const VALUATION_ZONES: readonly GaugeZone[] = [
  { label: "20% undervalued", to: -0.2, tone: "gain" },
  { label: "About right", to: 0.2, tone: "neutral" },
  { label: "20% overvalued", to: 0.6, tone: "loss" },
];

const ZONE_BG ={ gain: "bg-gain-subtle", neutral: "bg-surface-muted", loss: "bg-loss-subtle" } as const;

export function BandGauge({
  value,
  min,
  max,
  zones,
  marker,
  markers = [],
  anchor,
  gradient = false,
  format,
  label,
  className,
}: {
  value: number | null;
  min: number;
  max: number;
  /** Zones in order; each ends at `to` (the last one at `max`). */
  zones: readonly GaugeZone[];
  /** Label printed in the pill above the marker ("Price USD 342 · 40.7% above fair value"). */
  marker: string;
  /** Extra reference ticks (My portfolio, S&P 500, bear / bull, peer median). */
  markers?: readonly { value: number; label: string }[];
  /** A labelled point under the bar, e.g. fair value at 0 on a price-vs-fair scale. */
  anchor?: { value: number; label: string };
  /** Continuous risk-low to risk-high gradient instead of tinted zones (zone labels sit under it). */
  gradient?: boolean;
  format: (v: number) => string;
  label: string;
  className?: string;
}) {
  if (value === null || !Number.isFinite(value)) {
    return (
      <LimitedState className={className}>
        {label}: there is no value to place on this scale yet.
      </LimitedState>
    );
  }
  const x = linearScale([min, max], [0, 100]);
  const clampPct = (v: number) => Math.max(0, Math.min(100, x(v)));
  const pct = clampPct(value);
  const beyond = value < min ? "left" : value > max ? "right" : null;
  const pillStyle = pct < 14 ? { left: 0 } : pct > 86 ? { right: 0 } : { left: `${pct}%`, transform: "translateX(-50%)" };
  const widths = zones.map((z, i) => clampPct(z.to) - clampPct(i === 0 ? min : zones[i - 1].to));

  return (
    <figure className={cn("m-0", className)} aria-label={label}>
      <div className="relative pt-9">
        <div className="absolute top-0 rounded-full border border-border bg-surface-raised px-2 py-0.5 text-xs font-semibold whitespace-nowrap" style={pillStyle}>
          {beyond === "left" ? "◀ " : ""}
          {marker}
          {beyond === "right" ? " ▶" : ""}
        </div>
        <div className="relative">
          <div className={cn("flex h-4 w-full gap-0.5 overflow-hidden rounded-sm", gradient && "gap-0 bg-linear-to-r from-risk-low via-risk-mid to-risk-high")}>
            {gradient
              ? null
              : zones.map((z, i) => <div key={z.label} className={cn("h-full", ZONE_BG[z.tone])} style={{ width: `${widths[i]}%` }} />)}
          </div>
          {markers.map((m) => (
            <span
              key={m.label}
              title={`${m.label}: ${format(m.value)}`}
              className="absolute -top-1 h-6 w-px bg-text-muted"
              style={{ left: `${clampPct(m.value)}%` }}
            />
          ))}
          <span aria-hidden="true" className="absolute -top-1.5 h-7 w-0.5 -translate-x-1/2 bg-text" style={{ left: `${pct}%` }} />
        </div>
        <div className="relative mt-1.5 flex text-xs text-text-muted">
          {zones.map((z, i) => (
            <span key={z.label} className="truncate text-center" style={{ width: `${widths[i]}%` }}>
              {z.label}
            </span>
          ))}
        </div>
        {anchor ? (
          <div className="relative mt-0.5 h-4 text-xs text-text-muted">
            <span className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${clampPct(anchor.value)}%` }}>
              ▲ {anchor.label}
            </span>
          </div>
        ) : null}
        {markers.length ? (
          <p className="mt-1 text-xs text-text-faint">{markers.map((m) => `${m.label} ${format(m.value)}`).join("; ")}</p>
        ) : null}
      </div>
      <figcaption className="sr-only">
        {label}: {marker} ({format(value)}); zones {zones.map((z) => z.label).join(", ")}
      </figcaption>
    </figure>
  );
}
