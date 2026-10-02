"use client";

import { useState } from "react";

import { Heatmap } from "@/components/charts";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber, formatSigned } from "@/lib/charts/format";
import { sharedObservationStats } from "@/lib/markets/logic";
import type { CorrelationReport } from "@/lib/markets/types";

/**
 * Clustered correlation of daily returns across holdings on the diverging heat scale: the
 * order puts similar listings together, a blank cell means too few shared sessions (never 0),
 * and the shared-observation counts are in the caption and the table view.
 */
export function CorrelationPanel({ report }: { report: CorrelationReport }) {
  const [windowId, setWindowId] = useState(report.windows[report.windows.length - 1].id);
  const w = report.windows.find((x) => x.id === windowId) ?? report.windows[0];
  const stats = sharedObservationStats(w.counts);
  const labelOf = (k: string) => report.labels[k] ?? k;
  const blanks = w.matrix.flatMap((row, i) => row.filter((v, j) => j > i && v === null)).length;
  const pairs = (w.order.length * (w.order.length - 1)) / 2;

  const pairList = w.order
    .flatMap((a, i) => w.order.slice(i + 1).map((b, k) => ({ a, b, r: w.matrix[i][i + 1 + k], n: w.counts[i][i + 1 + k] })))
    .filter((p) => p.r !== null)
    .sort((p, q) => q.r! - p.r!);
  const top = pairList[0];

  return (
    <ChartFrame
      id="correlation"
      kicker="Cross-holding correlation"
      title="How holdings move together"
      takeaway={
        top
          ? `${labelOf(top.a)} and ${labelOf(top.b)} are the most correlated pair over ${w.label} (${formatNumber(top.r!, 2)}, ${top.n} shared sessions). ${blanks} of ${pairs} pairs lack enough shared sessions and are left blank.`
          : "No pair has enough shared sessions in this window."
      }
      units={`Pearson correlation of daily simple returns, ${w.label}; minimum ${w.minShared} shared sessions per pair${stats ? `; shared sessions per pair: min ${stats.min}, median ${stats.median}` : ""}`}
      controls={<Segmented label="Window" options={report.windows.map((x) => ({ id: x.id, label: x.label }))} value={w.id} onChange={setWindowId} />}
      provenance={{
        source: report.source,
        asOf: report.asOf,
        method: "Average-linkage clustering on 1 − r orders rows and columns; the order can change with the window",
        state: blanks > 0 ? "partial" : "verified",
        reason: report.reason,
        coverage: { covered: pairs - blanks, total: pairs, unit: "pairs" },
      }}
      table={{
        caption: `Pairwise correlation and shared sessions, ${w.label}`,
        columns: ["Pair", "Correlation", "Shared sessions"],
        rows: w.order.flatMap((a, i) =>
          w.order.slice(i + 1).map((b, k) => [
            `${labelOf(a)} / ${labelOf(b)}`,
            w.matrix[i][i + 1 + k] === null ? "too few shared sessions" : formatSigned(w.matrix[i][i + 1 + k]!, 2),
            w.counts[i][i + 1 + k],
          ]),
        ),
      }}
    >
      <Heatmap
        rows={w.order.map((k, i) => ({ label: k, cells: w.matrix[i] }))}
        columns={w.order}
        columnNames={w.order.map(labelOf)}
        cap={1}
        formatCell={(v) => formatNumber(v, 2)}
        caption={`Correlation matrix, ${w.label}, clustered`}
      />
    </ChartFrame>
  );
}
