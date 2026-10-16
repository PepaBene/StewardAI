import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * F28 GroupedTable: one table split into named groups, each opened by a header row with its count
 * and a one-line explanation ("Overvalued 4: price above the fair value on this basis"). Empty
 * groups still show their header with "None", so absence is visible.
 */

export type GroupColumn<T> = {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  align?: "left" | "right";
  /** Hide below the sm breakpoint (secondary columns). */
  wide?: boolean;
};

export type RowGroup<T> = { key: string; title: string; explanation: string; rows: readonly T[]; tone?: "gain" | "loss" | "warning" | "neutral" };

const TONE_BAR = { gain: "bg-gain", loss: "bg-loss", warning: "bg-warning-signal", neutral: "bg-text-faint" } as const;

export function GroupedTable<T>({
  groups,
  columns,
  rowKey,
  caption,
  onRowClick,
  className,
}: {
  groups: readonly RowGroup<T>[];
  columns: readonly GroupColumn<T>[];
  rowKey: (row: T) => string;
  caption: string;
  onRowClick?: (row: T) => void;
  className?: string;
}) {
  return (
    <div className={cn("overflow-x-auto border border-border", className)}>
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="bg-surface-muted text-xs text-text-muted">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn("px-3 py-1.5 font-medium", c.align === "right" ? "text-right" : "text-left", c.wide && "hidden sm:table-cell")}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g.key}>
            <tr className="border-t border-border">
              <th scope="rowgroup" colSpan={columns.length} className="px-3 pt-3 pb-1.5 text-left font-normal">
                <span className="flex items-baseline gap-2">
                  <span aria-hidden="true" className={cn("inline-block h-3 w-1 self-center", TONE_BAR[g.tone ?? "neutral"])} />
                  <span className="font-semibold">{g.title}</span>
                  <span className="num text-text-muted">{g.rows.length}</span>
                </span>
                <span className="mt-0.5 block text-xs text-text-muted">{g.explanation}</span>
              </th>
            </tr>
            {g.rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-3 pb-2 text-xs text-text-faint">
                  None
                </td>
              </tr>
            ) : (
              g.rows.map((r) => (
                <tr
                  key={rowKey(r)}
                  className={cn("border-t border-border-soft", onRowClick && "cursor-pointer hover:bg-surface-muted")}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                >
                  {columns.map((c, i) =>
                    i === 0 ? (
                      <th key={c.key} scope="row" className="px-3 py-1.5 text-left font-normal">
                        {c.cell(r)}
                      </th>
                    ) : (
                      <td key={c.key} className={cn("px-3 py-1.5", c.align === "right" && "num text-right", c.wide && "hidden sm:table-cell")}>
                        {c.cell(r)}
                      </td>
                    ),
                  )}
                </tr>
              ))
            )}
          </tbody>
        ))}
      </table>
    </div>
  );
}
