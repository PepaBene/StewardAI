import { formatPct } from "@/lib/charts/format";
import { BAND_STATE_LABEL, bandState } from "@/lib/config/strategy-view";
import { cn } from "@/lib/utils";

/*
 * Target-and-band meter of the strategy page: a hairline track on a shared 0..max scale, the
 * band as a pale block, a line from 0 to the target ending in a square marker, the hard
 * limit as a dashed tick and the current weight as a thin ink tick. Values beyond the scale
 * are pinned to its end (the accessible label keeps the true numbers).
 */

const pos = (v: number, max: number) => `${Math.max(0, Math.min(1, max > 0 ? v / max : 0)) * 100}%`;

export function TargetMeter({
  label,
  target,
  band,
  hardLimit = null,
  current,
  max,
  className,
}: {
  /** Accessible name, e.g. the ticker. */
  label: string;
  target: number | null;
  /** Half-width of the band (± fraction). */
  band: number | null;
  hardLimit?: number | null;
  /** Current weight from the overview's drift; null when not held / unknown. */
  current: number | null;
  /** Right end of the scale (fraction). */
  max: number;
  className?: string;
}) {
  const half = band ?? 0;
  const state = bandState(current, target, band, hardLimit);
  const parts = [
    target !== null ? `target ${formatPct(target, 1)}` : "no target",
    target !== null && band !== null ? `band ${formatPct(Math.max(0, target - half), 1)} to ${formatPct(target + half, 1)}` : null,
    hardLimit !== null ? `hard limit ${formatPct(hardLimit, 1)}` : null,
    current !== null ? `now ${formatPct(current, 1)} (${BAND_STATE_LABEL[state]})` : "no current weight",
  ].filter(Boolean);
  const text = `${label}: ${parts.join(", ")}`;

  return (
    <div role="img" aria-label={text} title={text} className={cn("relative h-[18px] w-full", className)}>
      <div className="absolute inset-x-0 top-2 h-0.5 bg-border" />
      {target !== null && band !== null ? (
        <div
          className="absolute top-1 h-2.5 bg-accent-200"
          style={{ left: pos(target - half, max), width: `calc(${pos(target + half, max)} - ${pos(target - half, max)})` }}
        />
      ) : null}
      {target !== null ? <div className="absolute top-2 left-0 h-0.5 bg-accent" style={{ width: pos(target, max) }} /> : null}
      {hardLimit !== null ? (
        <div className="absolute -inset-y-0.5 -ml-px border-l border-dashed border-text-muted" style={{ left: pos(hardLimit, max) }} />
      ) : null}
      {target !== null ? (
        <div
          className="absolute top-px -ml-2 size-4 border-2 border-surface bg-accent outline outline-1 outline-accent"
          style={{ left: pos(target, max) }}
        />
      ) : null}
      {current !== null ? (
        <div className="absolute -inset-y-0.5 -ml-px w-0.5 bg-text opacity-60" style={{ left: pos(current, max) }} />
      ) : null}
    </div>
  );
}

/** The meters' legend (target square, band block, hard-limit and current-weight ticks). */
export function TargetMeterLegend({ className }: { className?: string }) {
  return (
    <ul aria-hidden="true" className={cn("flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted", className)}>
      <li className="inline-flex items-center gap-1.5">
        <span className="size-2.5 bg-accent" />
        Target
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span className="h-2 w-3.5 bg-accent-200" />
        Band
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span className="h-3 border-l border-dashed border-text-muted" />
        Hard limit
      </li>
      <li className="inline-flex items-center gap-1.5">
        <span className="h-3 w-0.5 bg-text opacity-60" />
        Current weight
      </li>
    </ul>
  );
}
