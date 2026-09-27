import type { ReactNode } from "react";

import { donutSlices } from "@/lib/charts/arc";
import { formatPct } from "@/lib/charts/format";
import { cn } from "@/lib/utils";

import { CATEGORICAL_BG, CATEGORICAL_FILL, ChartEmpty } from "./shared";

export type DonutSegment = { label: string; value: number };

export type DonutProps = {
  /** Part-to-whole segments in display order. More than 8 fold the tail into "Other". */
  segments: readonly DonutSegment[];
  /** Big figure in the hole (e.g. "12"). */
  centerValue?: ReactNode;
  /** Small caption under it (e.g. "positions"). */
  centerLabel?: string;
  /** Legend beside the ring with each share. Default true. */
  legend?: boolean;
  /** Set tickers in the legend in mono. Default false. */
  monoLabels?: boolean;
  /** Rendered diameter in px. Default 170. */
  size?: number;
  formatShare?: (fraction: number) => string;
  /** Name of the whole, for the accessible summary (e.g. "Allocation by holding"). */
  label?: string;
  className?: string;
};

const MAX_SLOTS = 8;
const OTHER_LABEL = "Other";

/** Folds segments past the 8 categorical slots into one "Other" in the grey 8th slot. */
function fold(segments: readonly DonutSegment[]): DonutSegment[] {
  const clean = segments.filter((s) => Number.isFinite(s.value) && s.value > 0);
  if (clean.length <= MAX_SLOTS) return clean;
  const head = clean.slice(0, MAX_SLOTS - 1);
  const tail = clean.slice(MAX_SLOTS - 1);
  return [...head, { label: `${OTHER_LABEL} ${tail.length}`, value: tail.reduce((s, x) => s + x.value, 0) }];
}

/**
 * Part-to-whole ring with a centre figure. Slices are the categorical blues in fixed order
 * with a 2px surface gap between them; the legend names every slice and its share. Use it
 * for at-a-glance composition (≤ 6–8 parts), not for comparing close values.
 */
export function Donut({
  segments,
  centerValue,
  centerLabel,
  legend = true,
  monoLabels = false,
  size = 170,
  formatShare = (f) => formatPct(f, 1),
  label = "Composition",
  className,
}: DonutProps) {
  const parts = fold(segments);
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (parts.length === 0 || total <= 0) return <ChartEmpty minHeight={size} className={className} />;

  const slices = donutSlices(
    parts.map((p) => p.value),
    { cx: 90, cy: 90, r0: 58, r1: 86, gapPx: 2 },
  );
  const summary = `${label}: ${parts.map((p) => `${p.label} ${formatShare(p.value / total)}`).join(", ")}`;

  return (
    <div className={cn("flex items-center gap-5", className)}>
      <svg
        viewBox="0 0 180 180"
        width={size}
        height={size}
        className="block shrink-0"
        role="img"
        aria-label={summary}
      >
        {slices.map((s) => (
          <path key={s.index} d={s.d} className={CATEGORICAL_FILL[s.index]}>
            <title>{`${parts[s.index].label} · ${formatShare(s.fraction)}`}</title>
          </path>
        ))}
        {centerValue !== undefined ? (
          <text
            x={90}
            y={centerLabel ? 96 : 101}
            textAnchor="middle"
            fontSize={30}
            className="fill-text font-heading font-semibold"
          >
            {centerValue}
          </text>
        ) : null}
        {centerLabel ? (
          <text x={90} y={114} textAnchor="middle" fontSize={11} className="fill-text-muted">
            {centerLabel}
          </text>
        ) : null}
      </svg>
      {legend ? (
        <ul className="flex min-w-0 flex-1 flex-col gap-1.5 text-xs" aria-hidden="true">
          {parts.map((p, i) => (
            <li key={i} className="grid grid-cols-[10px_1fr_auto] items-center gap-2">
              <span className={cn("size-2.5", CATEGORICAL_BG[i])} />
              <span className={cn("truncate text-text", monoLabels && "font-mono text-[11px]")}>{p.label}</span>
              <span className="num text-text-muted">{formatShare(p.value / total)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
