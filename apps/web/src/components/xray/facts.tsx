import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Fact = { label: string; value: ReactNode; note?: ReactNode };

/** A row of labelled figures, each with the denominator or caveat that qualifies it. */
export function Facts({ items, className }: { items: readonly Fact[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-4", className)}>
      {items.map((f) => (
        <div key={f.label} className="min-w-0 border-l-2 border-border pl-3">
          <dt className="kicker">{f.label}</dt>
          <dd className="num mt-0.5 text-[22px] leading-tight font-semibold text-text">{f.value}</dd>
          {f.note ? <p className="mt-0.5 text-xs text-text-muted">{f.note}</p> : null}
        </div>
      ))}
    </dl>
  );
}

/** A plain table with real headers, wrapped so it scrolls inside its frame at phone width. */
export function DataTable({
  caption,
  columns,
  rows,
  minWidth = "40rem",
  className,
}: {
  caption: string;
  columns: readonly { label: string; align?: "left" | "right" }[];
  rows: readonly (readonly ReactNode[])[];
  minWidth?: string;
  className?: string;
}) {
  return (
    <div className={cn("relative overflow-x-auto border border-border-soft", className)}>
      <table className="w-full text-xs" style={{ minWidth }}>
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
            {columns.map((c, i) => (
              <th key={i} scope="col" className={cn("px-3 py-1.5 font-medium", c.align === "right" ? "text-right" : "text-left")}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b border-border-soft align-top last:border-0">
              {row.map((cell, c) =>
                c === 0 ? (
                  <th key={c} scope="row" className="px-3 py-1.5 text-left font-normal">
                    {cell}
                  </th>
                ) : (
                  <td key={c} className={cn("px-3 py-1.5", columns[c]?.align === "right" && "num text-right")}>
                    {cell}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
