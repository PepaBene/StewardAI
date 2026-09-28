import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * Pieces every chart in the kit shares: tone -> token class maps, the quiet empty state,
 * legends and the visually hidden data table. Class names are written out in full (never
 * built from fragments) so Tailwind can find them.
 */

/** Semantic tone of a single-series mark. gain/loss only when the colour means gain/loss. */
export type ChartTone = "accent" | "muted" | "gain" | "loss" | "warning";

export const TONE_STROKE: Record<ChartTone, string> = {
  accent: "stroke-chart-line",
  muted: "stroke-chart-benchmark",
  gain: "stroke-gain",
  loss: "stroke-loss",
  warning: "stroke-warning-signal",
};

export const TONE_FILL: Record<ChartTone, string> = {
  accent: "fill-chart-line",
  muted: "fill-chart-benchmark",
  gain: "fill-gain",
  loss: "fill-loss",
  warning: "fill-warning-signal",
};

export const TONE_BG: Record<ChartTone, string> = {
  accent: "bg-accent",
  muted: "bg-chart-benchmark",
  gain: "bg-gain",
  loss: "bg-loss",
  warning: "bg-warning-signal",
};

/** Categorical identity slots, fixed order (darkest blue first, slot 8 is the grey "Other"). */
export const CATEGORICAL_FILL = [
  "fill-categorical-1",
  "fill-categorical-2",
  "fill-categorical-3",
  "fill-categorical-4",
  "fill-categorical-5",
  "fill-categorical-6",
  "fill-categorical-7",
  "fill-categorical-8",
] as const;

export const CATEGORICAL_BG = [
  "bg-categorical-1",
  "bg-categorical-2",
  "bg-categorical-3",
  "bg-categorical-4",
  "bg-categorical-5",
  "bg-categorical-6",
  "bg-categorical-7",
  "bg-categorical-8",
] as const;

/** Classes for SVG axis/tick text: small mono, tabular, faint ink. */
export const AXIS_TEXT = "fill-text-faint font-mono num";

/** Responsive SVG: fills its container's width, keeps the viewBox aspect ratio. */
export const SVG_BASE = "block h-auto w-full overflow-visible";

/**
 * The quiet state a chart shows instead of inventing data: a dashed frame with one line of
 * faint text. `minHeight` keeps the layout from jumping when data arrives.
 */
export function ChartEmpty({
  label = "Not enough data to chart yet",
  minHeight = 120,
  className,
}: {
  label?: ReactNode;
  minHeight?: number;
  className?: string;
}) {
  return (
    <div
      data-slot="chart-empty"
      className={cn(
        "flex items-center justify-center border border-dashed border-border px-4 text-center text-xs text-text-faint",
        className,
      )}
      style={{ minHeight }}
    >
      {label}
    </div>
  );
}

export type LegendSwatch = "square" | "line" | "dashed" | "outline";

export type LegendEntry = {
  label: ReactNode;
  /** Shape of the key. */
  swatch?: LegendSwatch;
  /** Token class for the key: bg-* for square/line, border-* for dashed/outline. */
  className: string;
  /** Optional trailing figure (tabular). */
  value?: ReactNode;
};

/** One row of legend keys. Text stays in text tokens; only the key carries the colour. */
export function Legend({
  items,
  className,
  valueClassName = "text-text",
}: {
  items: readonly LegendEntry[];
  className?: string;
  /** Class for the trailing figures (e.g. text-accent-foreground on an accent panel). */
  valueClassName?: string;
}) {
  if (items.length === 0) return null;
  return (
    <ul className={cn("flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-muted", className)}>
      {items.map((item, i) => (
        <li key={i} className="inline-flex items-center gap-1.5">
          <LegendKey swatch={item.swatch ?? "square"} className={item.className} />
          <span>{item.label}</span>
          {item.value !== undefined ? <span className={cn("num font-semibold", valueClassName)}>{item.value}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function LegendKey({ swatch, className }: { swatch: LegendSwatch; className: string }) {
  const shape =
    swatch === "line"
      ? "h-0.5 w-4"
      : swatch === "dashed"
        ? "w-4 border-t-2 border-dashed"
        : swatch === "outline"
          ? "size-2.5 border"
          : "size-2.5";
  return <span aria-hidden="true" className={cn("inline-block shrink-0", shape, className)} />;
}

/** A visually hidden table carrying every value of a data-dense chart for screen readers. */
export function SrTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly string[];
  rows: readonly (readonly ReactNode[])[];
}) {
  // The wrapper, not the table, is visually hidden: a <table> ignores the 1px width of
  // `sr-only` and grows to its content, which pushed phone-width pages sideways.
  return (
    <div className="sr-only">
    <table>
      <caption>{caption}</caption>
      <thead>
        <tr>
          {columns.map((c, i) => (
            <th key={i} scope="col">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r}>
            {row.map((cell, c) =>
              c === 0 ? (
                <th key={c} scope="row">
                  {cell}
                </th>
              ) : (
                <td key={c}>{cell}</td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}
