"use client";

import { DownloadIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { saveText, toCsv } from "@/lib/csv";
import { cn } from "@/lib/utils";

export type ResponsiveColumn<R> = {
  key: string;
  header: ReactNode;
  cell: (row: R) => ReactNode;
  /** Right-aligned, tabular figures. */
  numeric?: boolean;
  /** Plain value for CSV export; defaults to nothing (the column is left out). */
  csv?: (row: R) => string | number | null;
  className?: string;
};

/**
 * ResponsiveTable (spec B35): a table from md up, CompactRow cards below. Pages of `pageSize`
 * rows with "Show N more"; an optional CSV download of every row. Sticky sentence-case header.
 */
export function ResponsiveTable<R>({
  rows,
  columns,
  rowKey,
  compact,
  pageSize = 25,
  caption,
  csvName,
  empty,
  onRow,
  className,
}: {
  rows: readonly R[];
  columns: readonly ResponsiveColumn<R>[];
  rowKey: (row: R) => string;
  /** The mobile card for one row (usually a CompactRow). */
  compact: (row: R) => ReactNode;
  pageSize?: number;
  /** Accessible caption (visually hidden). */
  caption: string;
  /** File name for CSV download; omitted = no download. */
  csvName?: string;
  empty?: ReactNode;
  onRow?: (row: R) => void;
  className?: string;
}) {
  const [shown, setShown] = useState(pageSize);
  if (rows.length === 0) return <>{empty ?? null}</>;
  const visible = rows.slice(0, shown);
  const more = rows.length - visible.length;
  const csvCols = columns.filter((c) => c.csv);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {csvName && csvCols.length > 0 ? (
        <div className="flex justify-end">
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              saveText(
                csvName,
                toCsv(
                  csvCols.map((c) => (typeof c.header === "string" ? c.header : c.key)),
                  rows.map((r) => csvCols.map((c) => c.csv!(r))),
                ),
              )
            }
          >
            <DownloadIcon aria-hidden="true" />
            Download CSV
          </Button>
        </div>
      ) : null}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-14 z-10">
            <tr className="bg-surface-muted text-xs font-semibold text-text-muted">
              {columns.map((c) => (
                <th key={c.key} scope="col" className={cn("px-4 py-2.5 text-left font-semibold first:rounded-l-control last:rounded-r-control", c.numeric && "text-right", c.className)}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr
                key={rowKey(r)}
                onClick={onRow ? () => onRow(r) : undefined}
                className={cn("h-11 border-b border-border-soft last:border-0", onRow && "cursor-pointer hover:bg-surface-muted")}
              >
                {columns.map((c) => (
                  <td key={c.key} className={cn("px-4 py-2", c.numeric && "num text-right", c.className)}>
                    {c.cell(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="m-0 list-none divide-y divide-border-soft p-0 md:hidden">
        {visible.map((r) => (
          <li key={rowKey(r)}>{compact(r)}</li>
        ))}
      </ul>
      {more > 0 ? (
        <div>
          <Button variant="outline" size="sm" onClick={() => setShown((n) => n + pageSize)}>
            Show {Math.min(pageSize, more)} more
          </Button>
          <span className="ml-3 text-xs text-text-muted">
            {visible.length} of {rows.length}
          </span>
        </div>
      ) : null}
    </div>
  );
}
