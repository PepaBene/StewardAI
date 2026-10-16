import { cn } from "@/lib/utils";

import { ChartEmpty, Legend, SrTable, type LegendEntry } from "./shared";

export type GridCellStatus = "ok" | "failed" | "retried" | "none" | "unknown";

export type GridStatusRow = {
  /** Row label, e.g. the job type. */
  label: string;
  /** One status per column. Missing trailing cells count as "none". */
  cells: readonly GridCellStatus[];
  /** Muted text at the end of the row, e.g. "18:02, 41 s". */
  note?: string;
  /** Rows sharing a group render under one collapsible heading (StatusMatrix, spec B23). */
  group?: string;
};

export type GridStatusProps = {
  rows: readonly GridStatusRow[];
  /** Column names (e.g. dates), used in tooltips and the accessible table. */
  columns: readonly string[];
  /** Legend above the grid. Default true. */
  legend?: boolean;
  /** Table caption, e.g. "Job runs, last 21 days". */
  caption?: string;
  /** Override the state words (e.g. "Covered" / "Partly" / "Missing" for coverage). */
  words?: Partial<Record<GridCellStatus, string>>;
  className?: string;
};

const CELL: Record<GridCellStatus, string> = {
  ok: "bg-gain",
  retried: "bg-warning-signal",
  failed: "bg-loss",
  none: "bg-chart-track",
  unknown: "hatch-forecast bg-surface-muted",
};

const GLYPH: Record<GridCellStatus, string> = { ok: "✓", retried: "↻", failed: "✕", none: "–", unknown: "?" };

const WORD: Record<GridCellStatus, string> = {
  ok: "OK",
  retried: "Retried",
  failed: "Failed",
  none: "Not scheduled",
  unknown: "Unknown",
};

const ORDER: readonly GridCellStatus[] = ["ok", "retried", "failed", "none", "unknown"];

export const GRID_STATUS_LEGEND: LegendEntry[] = ORDER.slice(0, 4).map((s) => ({ label: `${GLYPH[s]} ${WORD[s]}`, className: CELL[s] }));

/**
 * StatusMatrix (spec B23): rows of items × columns of days or periods. 14 px cells, 2 px gaps:
 * gain = OK, warning = retried or partial, loss = failed, track = not scheduled, hatch = unknown.
 * Every state has a glyph in the tooltip and legend, so colour is never the only signal; a
 * visually hidden table lists each state in words. Rows with a `group` collapse under it.
 */
export function GridStatus({ rows, columns, legend = true, caption = "Status by day", words, className }: GridStatusProps) {
  if (rows.length === 0 || columns.length === 0) return <ChartEmpty minHeight={120} className={className} />;
  const word = (s: GridCellStatus) => words?.[s] ?? WORD[s];
  const used = ORDER.filter((s) => rows.some((r) => columns.some((_, i) => (r.cells[i] ?? "none") === s)));
  const legendItems: LegendEntry[] = used.map((s) => ({ label: `${GLYPH[s]} ${word(s)}`, className: CELL[s] }));
  const hasNotes = rows.some((r) => r.note);
  const groups: { name: string | null; rows: GridStatusRow[] }[] = [];
  for (const r of rows) {
    const name = r.group ?? null;
    const last = groups[groups.length - 1];
    if (last && last.name === name) last.rows.push(r);
    else groups.push({ name, rows: [r] });
  }
  // Only the most recent 21 columns are visible without scrolling (spec B23).
  const scroll = columns.length > 21;

  const grid = (list: readonly GridStatusRow[]) => (
    <div
      aria-hidden="true"
      className="grid items-center gap-x-3 gap-y-0.5 text-xs"
      style={{ gridTemplateColumns: `minmax(6rem, max-content) minmax(0, 1fr)${hasNotes ? " max-content" : ""}` }}
    >
      {list.map((r) => (
        <div key={r.label} className="contents">
          <span className="truncate text-text">{r.label}</span>
          <div className={cn(scroll && "overflow-x-auto [scrollbar-width:thin]")} dir={scroll ? "rtl" : undefined}>
            <div className="flex gap-0.5" dir="ltr">
              {columns.map((c, i) => {
                const s = r.cells[i] ?? "none";
                return <span key={i} className={cn("size-3.5 shrink-0 rounded-[3px]", CELL[s])} title={`${r.label}, ${c}: ${GLYPH[s]} ${word(s)}`} />;
              })}
            </div>
          </div>
          {hasNotes ? <span className="num text-right text-xs text-text-muted">{r.note}</span> : null}
        </div>
      ))}
    </div>
  );

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {legend ? <Legend items={legendItems} className="self-end" /> : null}
      {groups.map((g, gi) =>
        g.name === null ? (
          <div key={`g${gi}`}>{grid(g.rows)}</div>
        ) : (
          <details key={g.name} open className="group">
            <summary className="mb-1.5 flex cursor-pointer list-none items-baseline gap-2 text-[13px] font-semibold">
              {g.name}
              <span className="num text-xs font-normal text-text-faint">{g.rows.length}</span>
            </summary>
            {grid(g.rows)}
          </details>
        ),
      )}
      <SrTable
        caption={caption}
        columns={["", ...columns]}
        rows={rows.map((r) => [r.label, ...columns.map((_, i) => word(r.cells[i] ?? "none"))])}
      />
    </div>
  );
}

/** The spec name for the same component. */
export { GridStatus as StatusMatrix };
