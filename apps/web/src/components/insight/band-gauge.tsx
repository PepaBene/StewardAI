import { linearScale } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

/*
 * F21 BandGauge: a horizontal three-zone band (under / about right / over) with a marker and the
 * zone labels written under it. Replaces speedometers. Zones are tinted with the subtle state
 * roles; the marker carries the value and a label, so the zone colour is never the only cue.
 */

export type GaugeZone = { label: string; to: number; tone: "gain" | "neutral" | "loss" };

const ZONE_BG = { gain: "bg-gain-subtle", neutral: "bg-surface-muted", loss: "bg-loss-subtle" } as const;

export function BandGauge({
  value,
  min,
  max,
  zones,
  marker,
  markers = [],
  format,
  label,
  className,
}: {
  value: number | null;
  min: number;
  max: number;
  /** Zones in order; each ends at `to` (the last one at `max`). */
  zones: readonly GaugeZone[];
  /** Label printed above the marker ("Price 12% under fair value"). */
  marker: string;
  /** Extra reference ticks (bear / bull, peer median). */
  markers?: readonly { value: number; label: string }[];
  format: (v: number) => string;
  label: string;
  className?: string;
}) {
  const x = linearScale([min, max], [0, 100]);
  const clampPct = (v: number) => Math.max(0, Math.min(100, x(v)));
  return (
    <figure className={cn("m-0", className)} aria-label={label}>
      <div className="relative pt-6">
        {value !== null ? (
          <div className="absolute top-0 -translate-x-1/2 text-xs font-semibold whitespace-nowrap" style={{ left: `${clampPct(value)}%` }}>
            {marker}
          </div>
        ) : null}
        <div className="relative flex h-3 w-full gap-0.5">
          {zones.map((z, i) => {
            const w = clampPct(z.to) - clampPct(i === 0 ? min : zones[i - 1].to);
            return <div key={z.label} className={cn("h-full", ZONE_BG[z.tone])} style={{ width: `${w}%` }} />;
          })}
          {markers.map((m) => (
            <span
              key={m.label}
              title={`${m.label}: ${format(m.value)}`}
              className="absolute -top-1 h-5 w-px bg-text-faint"
              style={{ left: `${clampPct(m.value)}%` }}
            />
          ))}
          {value !== null ? (
            <span
              aria-hidden="true"
              className="absolute -top-1.5 size-6 -translate-x-1/2 rounded-full border-2 border-surface bg-text"
              style={{ left: `${clampPct(value)}%`, height: 24, width: 6, borderRadius: 3 }}
            />
          ) : null}
        </div>
        <div className="mt-1.5 flex text-[11px] text-text-muted">
          {(() => {
            return zones.map((z, i) => {
              const w = clampPct(z.to) - clampPct(i === 0 ? min : zones[i - 1].to);
              return (
                <span key={z.label} className="truncate text-center" style={{ width: `${w}%` }}>
                  {z.label}
                </span>
              );
            });
          })()}
        </div>
        {markers.length ? (
          <p className="mt-1 text-[11px] text-text-faint">
            {markers.map((m) => `${m.label} ${format(m.value)}`).join("; ")}
          </p>
        ) : null}
      </div>
      <figcaption className="sr-only">
        {label}: {value === null ? "no value" : `${marker} (${format(value)})`}; zones {zones.map((z) => z.label).join(", ")}
      </figcaption>
    </figure>
  );
}
