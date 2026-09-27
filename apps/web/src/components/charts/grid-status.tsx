import { cn } from "@/lib/utils";

import { ChartEmpty, Legend, SrTable, type LegendEntry } from "./shared";

export type GridCellStatus = "ok" | "failed" | "retried" | "none";

export type GridStatusRow = {
  /** Row label, e.g. the job type. */
  label: string;
  /** One status per column. Missing trailing cells count as "none". */
  cells: readonly GridCellStatus[];
  /** Muted text at the end of the row, e.g. "18:02 · 41s". */
  note?: string;
};

export type GridStatusProps = {
  rows: readonly GridStatusRow[];
  /** Column names (e.g. dates), used in tooltips and the accessible table. */
  columns: readonly string[];
  /** Legend above the grid. Default true. */
  legend?: boolean;
  /** Table caption, e.g. "Job runs, last 21 days". */
  caption?: string;
  className?: string;
};

const CELL: Record<GridCellStatus, string> = {
  ok: "bg-accent",
  retried: "bg-accent-300",
  failed: "bg-loss",
  none: "border border-border",
};

const WORD: Record<GridCellStatus, string> = {
  ok: "OK",
  retried: "Retried",
  failed: "Failed",
  none: "Not scheduled",
};

export const GRID_STATUS_LEGEND: LegendEntry[] = [
  { label: WORD.ok, className: CELL.ok },
  { label: WORD.retried, className: CELL.retried },
  { label: WORD.failed, className: CELL.failed },
  { label: WORD.none, swatch: "outline", className: "border-border" },
];

/**
 * Status matrix (job types × days): solid accent = ran first time, pale = retried, loss =
 * failed, outlined = not scheduled. Each cell has a tooltip; a visually hidden table lists
 * every status in words.
 */
export function GridStatus({ rows, columns, legend = true, caption = "Status by day", className }: GridStatusProps) {
  if (rows.length === 0 || columns.length === 0) return <ChartEmpty minHeight={120} className={className} />;
  const hasNotes = rows.some((r) => r.note);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {legend ? <Legend items={GRID_STATUS_LEGEND} className="self-end" /> : null}
      <div
        aria-hidden="true"
        className="grid items-center gap-x-3 gap-y-0.5 text-xs"
        style={{ gridTemplateColumns: `minmax(6rem, max-content) 1fr${hasNotes ? " max-content" : ""}` }}
      >
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <span className="truncate text-text">{r.label}</span>
            <div className="grid gap-0.5" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}>
              {columns.map((c, i) => {
                const s = r.cells[i] ?? "none";
                return <span key={i} className={cn("h-5", CELL[s])} title={`${r.label} · ${c} · ${WORD[s]}`} />;
              })}
            </div>
            {hasNotes ? <span className="num text-right text-[11px] text-text-muted">{r.note}</span> : null}
          </div>
        ))}
      </div>
      <SrTable
        caption={caption}
        columns={["", ...columns]}
        rows={rows.map((r) => [r.label, ...columns.map((_, i) => WORD[r.cells[i] ?? "none"])])}
      />
    </div>
  );
}
