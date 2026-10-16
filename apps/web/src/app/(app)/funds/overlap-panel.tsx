"use client";

import Link from "next/link";
import { useMemo } from "react";

import { Heatmap } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { overlapMatrix, parseOverlap, topPairs } from "@/lib/funds/funds";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const fetchOverlap = () => getBrowserApiClient().GET("/funds/overlap", { params: { query: {} } });

const UNKNOWN = <span className="text-text-muted">Unknown</span>;

/** Pairwise overlap (sum of the smaller fund weight over shared issuers) plus each fund's own coverage. */
export function OverlapPanel({ selectedIsin }: { selectedIsin: string | null }) {
  const { state } = useApiResource(fetchOverlap);
  const data = dataOf(state);
  const overlap = useMemo(() => (data ? parseOverlap(data) : null), [data]);
  const matrix = useMemo(() => (overlap ? overlapMatrix(overlap) : null), [overlap]);

  return (
    <Panel
      id="overlap"
      title="Fund overlap"
      description="For two funds, the sum over shared issuers of the smaller of the two weights, as a share of each fund's own weight. A blank cell is a pair with no reported figure, not 0%."
    >
      <ResourceNotice state={state} what="fund overlap" />
      {overlap && overlap.funds.length < 2 ? (
        <EmptyState title="Fewer than two funds with holdings">Overlap needs two or more funds whose holdings are collected.</EmptyState>
      ) : null}
      {overlap && matrix && overlap.funds.length >= 2 ? (
        <div className="space-y-4">
          <Heatmap
            rows={matrix.labels.map((l, i) => ({ label: `${i + 1} ${l}`, cells: matrix.cells[i] }))}
            columns={matrix.labels.map((_, i) => String(i + 1))}
            columnNames={matrix.labels}
            cap={0.6}
            scale={false}
            formatCell={(v) => String(Math.round(v * 100))}
            caption="Pairwise fund overlap, percent of each fund's weight"
          />
          <p className="text-xs text-text-muted">
            Cells are percent. Deeper blue is more overlap; the diagonal is empty. Largest pairs:{" "}
            {topPairs(overlap, 3)
              .map((p) => `${p.a} and ${p.b} ${p.overlap === null ? "unknown" : formatPct(p.overlap, 0)}`)
              .join("; ")}
            .
            {matrix.mixedDatePairs > 0
              ? ` ${matrix.mixedDatePairs} pair${matrix.mixedDatePairs === 1 ? " compares" : "s compare"} snapshots from different dates.`
              : ""}
          </p>
        </div>
      ) : null}
      {overlap && overlap.funds.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <caption className="sr-only">Per-fund coverage and concentration</caption>
            <thead>
              <tr className="border-b border-border text-left text-xs text-text-muted">
                <th scope="col" className="py-1.5 pr-3 font-medium">Fund</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Holdings as of</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Equity, of fund</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Unknown, of fund</th>
                <th scope="col" className="py-1.5 pr-3 text-right font-medium">Top 10, of equity</th>
                <th scope="col" className="py-1.5 text-right font-medium">Effective names</th>
              </tr>
            </thead>
            <tbody>
              {overlap.funds.map((f, i) => (
                <tr key={f.isin} className={cn("border-b border-border-soft", f.isin === selectedIsin && "bg-surface-muted")}>
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                    <Link href={`/funds?isin=${f.isin}#factsheet`} className="text-accent-ink hover:underline">
                      {i + 1} {f.label}
                    </Link>
                    <span className="num block text-xs text-text-muted">{f.isin}</span>
                  </th>
                  <td className="num py-1.5 pr-3 text-right">
                    {f.asOf ?? UNKNOWN}
                    {f.stale ? (
                      <span className="block text-xs text-text-muted">stale{f.lagDays !== null ? `, ${f.lagDays} d old` : ""}</span>
                    ) : null}
                  </td>
                  <td className="num py-1.5 pr-3 text-right">{f.equityWeight === null ? UNKNOWN : formatPct(f.equityWeight, 1)}</td>
                  <td className="num py-1.5 pr-3 text-right">{f.unknownWeight === null ? UNKNOWN : formatPct(f.unknownWeight, 1)}</td>
                  <td className="num py-1.5 pr-3 text-right">{f.top10Share === null ? UNKNOWN : formatPct(f.top10Share, 0)}</td>
                  <td className="num py-1.5 text-right">{f.effectiveN === null ? UNKNOWN : Math.round(f.effectiveN)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {overlap ? (
        <ul className="space-y-0.5 text-xs text-text-muted">
          {overlap.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
