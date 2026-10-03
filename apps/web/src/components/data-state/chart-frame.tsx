import type { ReactNode } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { displayDecision, placeholderState, type DataState } from "@/lib/data-state/states";
import type { DataProvenance } from "@/lib/data-state/provenance";
import { cn } from "@/lib/utils";

import { ProvenanceFooter } from "./provenance-footer";
import { StatePlaceholder } from "./state-placeholder";

export type TableEquivalent = {
  caption: string;
  columns: readonly string[];
  rows: readonly (readonly ReactNode[])[];
};

/** The visible table equivalent of a chart: exact values behind a disclosure, with real headers. */
export function TableView({ table, summary = "Table view: exact values" }: { table: TableEquivalent; summary?: string }) {
  return (
    <details className="group border border-border-soft text-sm">
      <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">
        {summary}
      </summary>
      <div className="overflow-x-auto border-t border-border-soft">
        <table className="w-full min-w-[28rem] text-xs">
          <caption className="sr-only">{table.caption}</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
              {table.columns.map((c, i) => (
                <th key={i} scope="col" className={cn("px-3 py-1.5 font-medium", i === 0 ? "text-left" : "text-right")}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, r) => (
              <tr key={r} className="border-b border-border-soft last:border-0">
                {row.map((cell, c) =>
                  c === 0 ? (
                    <th key={c} scope="row" className="px-3 py-1.5 text-left font-normal">
                      {cell}
                    </th>
                  ) : (
                    <td key={c} className="num px-3 py-1.5 text-right">
                      {cell}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

/**
 * The frame every chart of the free-data views sits in (Phase 7 contract, applied from the
 * start): a plain-language takeaway, units, the chart, a visible table equivalent with the
 * exact values, and the provenance footer. When the state allows no value, the placeholder
 * replaces the chart; with a last-good value the chart stays and the state is flagged.
 */
export function ChartFrame({
  id,
  kicker,
  title,
  takeaway,
  units,
  provenance,
  hasValue = true,
  controls,
  table,
  children,
  className,
}: {
  id?: string;
  kicker?: ReactNode;
  title: ReactNode;
  /** One sentence stating what the chart shows, as a fact — not an instruction. */
  takeaway?: ReactNode;
  /** Units and denominator, e.g. "% of portfolio value, 18 of 24 holdings". */
  units?: ReactNode;
  provenance: DataProvenance;
  hasValue?: boolean;
  controls?: ReactNode;
  table?: TableEquivalent;
  children: ReactNode;
  className?: string;
}) {
  const decision = displayDecision(provenance.state as DataState, hasValue);
  // With nothing drawn the footer and placeholder never say "Verified" (or another value state).
  const shown: DataProvenance = decision === "placeholder" ? { ...provenance, state: placeholderState(provenance.state as DataState) } : provenance;
  const headingId = id ? `${id}-heading` : undefined;
  return (
    <Blueprint
      as="section"
      aria-labelledby={headingId}
      {...(id ? { id } : {})}
      className={cn("flex scroll-mt-24 flex-col gap-3 px-5 py-5", className)}
    >
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <PanelHeading kicker={kicker} title={<span id={headingId}>{title}</span>} />
          {takeaway ? <p className="mt-1 max-w-prose text-sm text-text">{takeaway}</p> : null}
          {units ? <p className="num mt-0.5 text-xs text-text-muted">{units}</p> : null}
        </div>
        {controls ? <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2">{controls}</div> : null}
      </div>
      {decision === "placeholder" ? (
        <StatePlaceholder state={shown.state} reason={provenance.reason} />
      ) : (
        children
      )}
      {table && decision !== "placeholder" ? <TableView table={table} /> : null}
      <ProvenanceFooter provenance={shown} />
    </Blueprint>
  );
}
