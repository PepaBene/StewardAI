import { formatPct, formatSigned } from "@/lib/charts/format";
import { clamp01 } from "@/lib/charts/arc";
import { isFiniteNumber, niceStep } from "@/lib/charts/scale";
import { cn } from "@/lib/utils";

import { CATEGORICAL_BG, ChartEmpty, TONE_BG, type ChartTone } from "./shared";

export type HBarProps = {
  /** Current value; null draws the empty track. */
  value: number | null;
  /** Scale start. Default 0. */
  min?: number;
  /** Scale end. Default 1. */
  max?: number;
  /** Target marker (a short vertical tick in ink). */
  target?: number | null;
  /** Tolerance band [lo, hi], drawn as a pale region on the track. */
  band?: readonly [number, number] | null;
  /** Fill colour. Default "accent"; "loss" for a breached KPI. */
  tone?: ChartTone;
  /** Bar thickness. Default "md" (8px); "sm" is 4px. */
  size?: "sm" | "md";
  /** Accessible name, e.g. "NVDA weight". */
  label: string;
  formatValue?: (value: number) => string;
  className?: string;
};

/**
 * Horizontal meter: a filled bar on a track, with an optional target tick and tolerance band
 * (position weight vs target, KPI vs floor). Exposed as role="meter".
 */
export function HBar({
  value,
  min = 0,
  max = 1,
  target,
  band,
  tone = "accent",
  size = "md",
  label,
  formatValue = (v) => formatPct(v, 1),
  className,
}: HBarProps) {
  const span = max - min;
  const pos = (v: number) => (span > 0 ? clamp01((v - min) / span) * 100 : 0);
  const has = isFiniteNumber(value);
  const valueText =
    (has ? formatValue(value) : "No data") +
    (isFiniteNumber(target) ? `, target ${formatValue(target)}` : "") +
    (band ? `, band ${formatValue(band[0])} to ${formatValue(band[1])}` : "");

  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={has ? value : undefined}
      aria-valuetext={valueText}
      className={cn("relative w-full bg-surface-muted", size === "sm" ? "h-1" : "h-2", className)}
      title={`${label}: ${valueText}`}
    >
      {band ? (
        <div
          className="absolute inset-y-0 bg-accent-200"
          style={{ left: `${pos(band[0])}%`, width: `${Math.max(0, pos(band[1]) - pos(band[0]))}%` }}
        />
      ) : null}
      {has ? <div className={cn("absolute inset-y-0 left-0", TONE_BG[tone])} style={{ width: `${pos(value)}%` }} /> : null}
      {isFiniteNumber(target) ? (
        <div className="absolute -inset-y-1 w-0.5 -translate-x-1/2 bg-text" style={{ left: `${pos(target)}%` }} />
      ) : null}
    </div>
  );
}

/** Alias: the same component under the name used in the design notes. */
export const Meter = HBar;

export type DivergingBarRow = {
  /** Row label, e.g. a ticker (set in mono). */
  label: string;
  /** Signed deviation from target (e.g. drift in percentage points). Null -> "—". */
  value: number | null;
  /** Half-width of the tolerance band around 0, same units as value. */
  band?: number;
};

export type DivergingBarProps = {
  rows: readonly DivergingBarRow[];
  /** Axis half-range; values beyond it are clamped (their label still shows the true value). Default: a round number covering every value and band. */
  domain?: number;
  /**
   * "emphasis" (default, as in the design): out-of-band bars in the accent, in-band in a
   * lighter step, direction read from position. "direction": the diverging-under (cool) /
   * diverging-over (warm) pair by sign.
   */
  colorMode?: "emphasis" | "direction";
  /** Axis labels under the rows. Default true. */
  axis?: boolean;
  formatValue?: (value: number) => string;
  /** Accessible name for the list, e.g. "Drift against target bands". */
  label?: string;
  className?: string;
};

/**
 * Drift vs target: one row per holding, a bar from a centre zero line, the tolerance band
 * shaded with dashed edges. Out-of-band rows are bold with an "outside band" note for screen
 * readers, so the emphasis is never colour alone.
 */
export function DivergingBar({
  rows,
  domain,
  colorMode = "emphasis",
  axis = true,
  formatValue = (v) => `${formatSigned(v, 1)} pp`,
  label = "Deviation from target",
  className,
}: DivergingBarProps) {
  if (!rows.some((r) => isFiniteNumber(r.value))) return <ChartEmpty minHeight={120} className={className} />;

  const reach = Math.max(
    ...rows.map((r) => Math.max(isFiniteNumber(r.value) ? Math.abs(r.value) : 0, r.band ?? 0)),
    1e-9,
  );
  const half = domain ?? Math.ceil(reach / niceStep(reach, 2)) * niceStep(reach, 2);
  const pct = (v: number) => (Math.max(-half, Math.min(half, v)) / half) * 50;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <ul aria-label={label} className="m-0 flex list-none flex-col gap-1.5 p-0">
        {rows.map((r, i) => {
          const v = r.value;
          const has = isFiniteNumber(v);
          const out = has && r.band !== undefined && Math.abs(v) > r.band;
          const fill =
            colorMode === "direction"
              ? has && v < 0
                ? "bg-diverging-under"
                : "bg-diverging-over"
              : out || r.band === undefined
                ? "bg-accent"
                : "bg-accent-300";
          return (
            <li key={i} className="grid grid-cols-[56px_1fr_64px] items-center gap-2.5 text-xs">
              <span className="truncate font-mono text-[11px] text-text">{r.label}</span>
              <div className="relative h-3" aria-hidden="true">
                {r.band !== undefined ? (
                  <div
                    className="absolute inset-y-0 border-x border-dashed border-border bg-border-soft"
                    style={{ left: `${50 - pct(r.band)}%`, width: `${pct(r.band) * 2}%` }}
                  />
                ) : null}
                <div className="absolute -inset-y-0.5 left-1/2 w-px bg-border" />
                {has && v !== 0 ? (
                  <div
                    className={cn("absolute inset-y-0.5", fill)}
                    style={{ left: `${v >= 0 ? 50 : 50 + pct(v)}%`, width: `${Math.abs(pct(v))}%` }}
                  />
                ) : null}
              </div>
              <span className={cn("num text-right", out ? "font-bold text-accent-ink" : "text-text-muted")}>
                {has ? formatValue(v) : "—"}
                {out ? <span className="sr-only"> (outside band)</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
      {axis ? (
        <div className="grid grid-cols-[56px_1fr_64px] gap-2.5 text-[11px] text-text-faint" aria-hidden="true">
          <span />
          <div className="num flex justify-between">
            <span>{formatValue(-half)}</span>
            <span>0</span>
            <span>{formatValue(half)}</span>
          </div>
          <span />
        </div>
      ) : null}
    </div>
  );
}

export type StackedSegment = { label: string; value: number };

export type StackedBarProps = {
  /** Parts in display order; non-positive values are dropped, more than 8 fold into "Other". */
  segments: readonly StackedSegment[];
  /** Heading on the left above the bar (e.g. "Sector"). */
  title?: string;
  /** Muted note on the right above the bar (e.g. "Largest: Global equity"). */
  note?: string;
  /** Legend under the bar with each share. Default true. */
  legend?: boolean;
  formatShare?: (fraction: number) => string;
  className?: string;
};

/**
 * One 100% bar split into categorical segments (exposure by sector, region, currency), 2px
 * surface gap between segments, legend with shares below.
 */
export function StackedBar({
  segments,
  title,
  note,
  legend = true,
  formatShare = (f) => formatPct(f, 0),
  className,
}: StackedBarProps) {
  const clean = segments.filter((s) => Number.isFinite(s.value) && s.value > 0);
  const parts =
    clean.length <= 8
      ? clean
      : [
          ...clean.slice(0, 7),
          { label: `Other ${clean.length - 7}`, value: clean.slice(7).reduce((s, x) => s + x.value, 0) },
        ];
  const total = parts.reduce((s, p) => s + p.value, 0);
  const summary = parts.map((p) => `${p.label} ${formatShare(p.value / total)}`).join(", ");

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {title || note ? (
        <div className="flex justify-between gap-3 text-xs">
          {title ? <span className="font-semibold text-text">{title}</span> : <span />}
          {note ? <span className="text-text-muted">{note}</span> : null}
        </div>
      ) : null}
      {total > 0 ? (
        <>
          <div role="img" aria-label={title ? `${title}: ${summary}` : summary} className="flex h-5.5 gap-0.5">
            {parts.map((p, i) => (
              <div
                key={i}
                className={cn("min-w-px", CATEGORICAL_BG[i])}
                style={{ flexGrow: p.value, flexBasis: 0 }}
                title={`${p.label} · ${formatShare(p.value / total)}`}
              />
            ))}
          </div>
          {legend ? (
            <ul className="m-0 flex list-none flex-wrap gap-x-3.5 gap-y-1 p-0 text-[11px] text-text-muted" aria-hidden="true">
              {parts.map((p, i) => (
                <li key={i} className="inline-flex items-center gap-1.5">
                  <span className={cn("size-2", CATEGORICAL_BG[i])} />
                  <b className="font-semibold text-text">{p.label}</b>
                  <span className="num">{formatShare(p.value / total)}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <ChartEmpty minHeight={22} />
      )}
    </div>
  );
}
