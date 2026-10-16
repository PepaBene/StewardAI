import { cn } from "@/lib/utils";

import { ChartEmpty, Legend, SrTable } from "./shared";

export type DumbbellRow = {
  label: string;
  before: number;
  after: number;
  /** Optional target band (same unit), shaded behind the row. */
  target?: { low: number; high: number } | null;
};

export type DumbbellProps = {
  rows: readonly DumbbellRow[];
  formatValue?: (v: number) => string;
  formatDelta?: (d: number) => string;
  beforeLabel?: string;
  afterLabel?: string;
  caption?: string;
  /** Show at most this many rows (largest absolute change first). Default 12. */
  max?: number;
  className?: string;
};

/**
 * Dumbbell (spec B28): before → after per row, as two dots joined by a line. Before in the
 * benchmark tone, after in accent, the signed change at the after dot. Rows sort by the size of
 * the change; a target band is shaded where one exists.
 */
export function Dumbbell({
  rows,
  formatValue = (v) => v.toFixed(1),
  formatDelta = (d) => `${d > 0 ? "+" : d < 0 ? "−" : "±"}${Math.abs(d).toFixed(1)}`,
  beforeLabel = "Before",
  afterLabel = "After",
  caption = "Before and after",
  max = 12,
  className,
}: DumbbellProps) {
  const finite = rows.filter((r) => Number.isFinite(r.before) && Number.isFinite(r.after));
  if (finite.length === 0) return <ChartEmpty minHeight={80} className={className} />;
  const sorted = [...finite].sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before)).slice(0, max);
  const all = sorted.flatMap((r) => [r.before, r.after, ...(r.target ? [r.target.low, r.target.high] : [])]);
  const lo = Math.min(0, ...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const pos = (v: number) => `${((v - lo) / span) * 100}%`;

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <ul aria-hidden="true" className="m-0 flex list-none flex-col gap-1.5 p-0">
        {sorted.map((r) => {
          const d = r.after - r.before;
          const left = Math.min(r.before, r.after);
          const right = Math.max(r.before, r.after);
          return (
            <li key={r.label} className="grid grid-cols-[minmax(5rem,9rem)_minmax(0,1fr)_4.5rem] items-center gap-3 text-xs">
              <span className="truncate text-text" title={r.label}>
                {r.label}
              </span>
              <span className="relative h-5">
                {r.target ? (
                  <span
                    className="absolute inset-y-0.5 rounded-sm bg-surface-muted"
                    style={{ left: pos(r.target.low), width: `calc(${pos(r.target.high)} - ${pos(r.target.low)})` }}
                  />
                ) : null}
                <span
                  className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-text-faint"
                  style={{ left: pos(left), width: `calc(${pos(right)} - ${pos(left)})` }}
                />
                <span
                  className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-chart-benchmark ring-2 ring-surface"
                  style={{ left: pos(r.before) }}
                  title={`${beforeLabel}: ${formatValue(r.before)}`}
                />
                <span
                  className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-surface"
                  style={{ left: pos(r.after) }}
                  title={`${afterLabel}: ${formatValue(r.after)}`}
                />
              </span>
              <span className={cn("num text-right", d > 0 ? "text-text" : d < 0 ? "text-text" : "text-text-faint")}>{formatDelta(d)}</span>
            </li>
          );
        })}
      </ul>
      <Legend
        items={[
          { label: beforeLabel, className: "bg-chart-benchmark" },
          { label: afterLabel, className: "bg-accent" },
        ]}
      />
      <SrTable
        caption={caption}
        columns={["", beforeLabel, afterLabel, "Change"]}
        rows={sorted.map((r) => [r.label, formatValue(r.before), formatValue(r.after), formatDelta(r.after - r.before)])}
      />
    </figure>
  );
}
