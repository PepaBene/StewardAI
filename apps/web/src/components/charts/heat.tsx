import { formatPct, formatSigned } from "@/lib/charts/format";
import { HEAT_LEVELS, heatLevel, type HeatLevel } from "@/lib/charts/heat";
import { isFiniteNumber } from "@/lib/charts/scale";
import type { MonthlyReturnRow } from "@/lib/charts/series";
import { MONTH_INITIAL, MONTH_SHORT } from "@/lib/charts/time";
import { cn } from "@/lib/utils";

import { ChartEmpty } from "./shared";

/** Background token per heat step (flat is the neutral midpoint, never a hue). */
export const HEAT_BG: Record<HeatLevel, string> = {
  "down-strong": "bg-heat-down-strong",
  down: "bg-heat-down",
  "down-soft": "bg-heat-down-soft",
  flat: "bg-diverging-neutral",
  "up-soft": "bg-heat-up-soft",
  up: "bg-heat-up",
  "up-strong": "bg-heat-up-strong",
};

/** Text on each heat step: reversed on the saturated ends, ink elsewhere. */
export const HEAT_TEXT: Record<HeatLevel, string> = {
  "down-strong": "text-accent-foreground dark:text-surface",
  down: "text-text",
  "down-soft": "text-text",
  flat: "text-text",
  "up-soft": "text-text",
  up: "text-text",
  "up-strong": "text-accent-foreground",
};

/** Discrete scale key: "−6% ▮▮▮▮▮▮▮ +6%". */
export function HeatScale({
  cap,
  format = (v) => formatPct(v, 0, true),
  className,
}: {
  /** The value at which the ends saturate. */
  cap: number;
  format?: (value: number) => string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-1.5 text-[11px] text-text-muted", className)} aria-hidden="true">
      <span className="num">{format(-cap)}</span>
      <span className="flex gap-px">
        {HEAT_LEVELS.map((l) => (
          <span key={l} className={cn("h-2 w-3", HEAT_BG[l])} />
        ))}
      </span>
      <span className="num">{format(cap)}</span>
    </div>
  );
}

export type HeatmapRow = {
  /** Row header, e.g. "2025". */
  label: string;
  /** One value per column; null is an empty (dashed) cell. */
  cells: readonly (number | null)[];
  /** Optional row total shown in the last column (e.g. the year's return). */
  total?: number | null;
};

export type HeatmapProps = {
  rows: readonly HeatmapRow[];
  /** Column headers (default: month initials J F M …). */
  columns?: readonly string[];
  /** Full column names for screen readers (default: month short names). */
  columnNames?: readonly string[];
  /** |value| at which colour saturates. Default 0.06 (±6% for monthly returns). */
  cap?: number;
  /** Header of the totals column. Omit totals by leaving `total` off every row. */
  totalLabel?: string;
  /** Visible cell text. Default: signed percent without the % sign ("+1.8"). */
  formatCell?: (value: number) => string;
  formatTotal?: (value: number) => string;
  /** Show the scale key above the grid. Default true. */
  scale?: boolean;
  /** Table caption (visually hidden), e.g. "Monthly returns, time-weighted". */
  caption?: string;
  className?: string;
};

/** Converts `monthlyReturns()` output into heatmap rows labelled by year. */
export function rowsFromMonthlyReturns(rows: readonly MonthlyReturnRow[]): HeatmapRow[] {
  return rows.map((r) => ({ label: String(r.year), cells: r.months, total: r.total }));
}

/**
 * Rows × columns grid on the diverging heat scale (loss-side reds, gain-side blues, neutral
 * midpoint). It is a real <table>, so every value is reachable by screen readers; empty cells
 * are dashed outlines, never a colour.
 */
export function Heatmap({
  rows,
  columns = MONTH_INITIAL,
  columnNames = MONTH_SHORT,
  cap = 0.06,
  totalLabel = "Year",
  formatCell = (v) => formatSigned(v * 100, 1),
  formatTotal = (v) => formatPct(v, 1, true),
  scale = true,
  caption = "Heatmap",
  className,
}: HeatmapProps) {
  const any = rows.some((r) => r.cells.some(isFiniteNumber));
  if (!any) return <ChartEmpty minHeight={120} className={className} />;
  const withTotals = rows.some((r) => r.total !== undefined);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {scale ? <HeatScale cap={cap} className="self-end" /> : null}
      <table className="w-full table-fixed border-separate border-spacing-0.5 text-xs">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <td className="w-12" />
            {columns.map((c, i) => (
              <th key={i} scope="col" className="pb-1 text-center text-[11px] font-normal text-text-muted">
                <span aria-hidden="true">{c}</span>
                <span className="sr-only">{columnNames[i] ?? c}</span>
              </th>
            ))}
            {withTotals ? (
              <th scope="col" className="w-16 pb-1 text-center text-[11px] font-normal text-text-muted">
                {totalLabel}
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label}>
              <th scope="row" className="pr-2 text-left font-semibold text-text">
                {r.label}
              </th>
              {columns.map((_, i) => {
                const v = r.cells[i] ?? null;
                const level = heatLevel(v, cap);
                return (
                  <td key={i} className="p-0">
                    {level && isFiniteNumber(v) ? (
                      <div
                        className={cn(
                          "num flex h-10 items-center justify-center",
                          HEAT_BG[level],
                          HEAT_TEXT[level],
                          level.endsWith("strong") && "font-semibold",
                        )}
                        title={`${r.label} ${columnNames[i] ?? columns[i]}: ${formatCell(v)}`}
                      >
                        {formatCell(v)}
                      </div>
                    ) : (
                      <div className="h-10 border border-dashed border-border">
                        <span className="sr-only">No data</span>
                      </div>
                    )}
                  </td>
                );
              })}
              {withTotals ? (
                <td className="num text-center text-[13px] font-semibold text-text">
                  {isFiniteNumber(r.total) ? formatTotal(r.total) : "—"}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
