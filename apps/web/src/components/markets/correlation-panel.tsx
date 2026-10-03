import type { ReactNode } from "react";

import { Heatmap, HeatScale } from "@/components/charts";
import { ChartFrame } from "@/components/data-state";
import { formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { clusterOrder, type CorrelationGrid } from "@/lib/markets/logic";
import type { PortfolioCorrelation, PortfolioRisk } from "@/lib/markets/types";

/** Hover / table text of one pair: the shared-return count and the backend's note. */
function cellText(count: number, note: string | null): string {
  return `${count} shared return${count === 1 ? "" : "s"}${note ? ` · ${note}` : ""}`;
}

/**
 * Correlation heatmap of holdings on the diverging scale, clustered so similar holdings sit
 * together. One request (GET /portfolio/correlation) returns the matrix, the shared-return count
 * and a note per pair; nothing is computed here and the cluster order only rearranges rows and
 * columns. A blank cell means the backend returned null (too few shared returns, or a flat
 * series), never 0; its reason, the count and any different-source caution are in the cell
 * tooltip and in the table view.
 */
export function CorrelationPanel({
  data,
  grid,
  names,
  risk,
  windowControl,
}: {
  data: Pick<PortfolioCorrelation, "days" | "min_shared" | "max_returns" | "start" | "end" | "method">;
  grid: CorrelationGrid;
  names: Readonly<Record<string, string>>;
  risk: PortfolioRisk | null;
  windowControl?: ReactNode;
}) {
  const order = clusterOrder(grid.matrix);
  const tickers = order.map((i) => grid.tickers[i]);
  const matrix = order.map((i) => order.map((j) => grid.matrix[i][j]));
  const cellNotes = order.map((i) => order.map((j) => cellText(grid.counts[i][j], grid.notes[i][j])));
  const n = tickers.length;
  const pairs = (n * (n - 1)) / 2;
  let blanks = 0;
  const pairList: { a: string; b: string; r: number }[] = [];
  const tableRows: (string | number)[][] = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const v = matrix[i][j];
      if (v === null) blanks += 1;
      else pairList.push({ a: tickers[i], b: tickers[j], r: v });
      tableRows.push([`${names[tickers[i]] ?? tickers[i]} / ${names[tickers[j]] ?? tickers[j]}`, v === null ? "no value" : formatNumber(v, 2), grid.counts[order[i]][order[j]], grid.notes[order[i]][order[j]] ?? ""]);
    }
  }
  pairList.sort((p, q) => q.r - p.r);
  const top = pairList[0];
  const label = (t: string) => names[t] ?? t;
  const state = n < 2 ? "not_covered" : blanks > 0 || grid.withoutSeries.length > 0 ? "partial" : "verified";
  const span = data.start && data.end ? `${formatDay(data.start)} to ${formatDay(data.end)}` : "window not reported";

  return (
    <ChartFrame
      id="correlation"
      kicker="Cross-holding correlation"
      title="How holdings move together"
      takeaway={
        n < 2
          ? "Fewer than two holdings have a stored price series, so no correlation exists to show."
          : top
            ? `${label(top.a)} and ${label(top.b)} are the most correlated pair (${formatNumber(top.r, 2)}). ${blanks} of ${pairs} pairs have no value (fewer than ${data.min_shared} shared returns, or a flat series) and are left blank.`
            : "The backend returned no pairwise correlation for these holdings."
      }
      units={`Correlation of daily returns (unitless, -1 to +1) over the last ${data.days} days; a pair needs at least ${data.min_shared} shared returns`}
      hasValue={n >= 2 && pairList.length > 0}
      controls={windowControl}
      provenance={{
        source: `${grid.sources.length > 0 ? grid.sources.join(", ") : "Stored price series"} via GET /portfolio/correlation`,
        asOf: data.end,
        method: `${data.method}. Window ${span}; at least ${data.min_shared} shared returns per pair, at most the latest ${data.max_returns}. Average-linkage clustering on 1 - r only orders rows and columns.`,
        state,
        reason:
          [
            grid.withoutSeries.length > 0 ? `No stored series in the window for ${grid.withoutSeries.join(", ")}.` : null,
            blanks > 0 ? `${blanks} pair${blanks === 1 ? "" : "s"} without a value; the reason is in each cell's tooltip and in the table view.` : null,
            grid.sources.length > 1 ? "Series come from more than one source; pairs across sources carry the backend's caution in their note (adjustments can differ)." : null,
          ]
            .filter((x): x is string => x !== null)
            .join(" "),
        coverage: n >= 2 ? { covered: pairs - blanks, total: pairs, unit: "pairs" } : null,
      }}
      table={{
        caption: "Pairwise correlation, shared returns and notes as returned by the backend",
        columns: ["Pair", "Correlation", "Shared returns", "Note"],
        rows: tableRows,
      }}
    >
      {/* The default scale key prints percent ("-100% to +100%"); a correlation runs -1 to +1. */}
      <HeatScale cap={1} format={(v) => formatSigned(v, 0)} className="self-end" />
      <Heatmap rows={tickers.map((t, i) => ({ label: t, cells: matrix[i], notes: cellNotes[i] }))} columns={tickers} columnNames={tickers.map(label)} cap={1} formatCell={(v) => formatNumber(v, 2)} scale={false} caption="Correlation matrix, clustered" rowHeaderClassName="w-28" />
      {risk ? (
        <p className="m-0 text-xs text-text-muted">
          Portfolio context (GET /portfolio/risk, {risk.days} days
          {risk.portfolio.window_start && risk.portfolio.window_end ? `, ${formatDay(risk.portfolio.window_start)} to ${formatDay(risk.portfolio.window_end)}` : ""}): annualised volatility{" "}
          {risk.portfolio.annualised_volatility === null ? "unavailable" : formatPct(risk.portfolio.annualised_volatility, 1)}, beta {risk.beta === null || risk.beta === undefined ? "unavailable" : formatNumber(risk.beta, 2)}, {risk.portfolio.observations} observations
          {risk.portfolio.reason ? `; Sharpe unavailable: ${risk.portfolio.reason}` : ""}.
        </p>
      ) : null}
    </ChartFrame>
  );
}
