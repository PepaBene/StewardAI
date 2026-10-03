import { Heatmap, HeatScale } from "@/components/charts";
import { ChartFrame } from "@/components/data-state";
import { formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { clusterOrder, type CorrelationGrid } from "@/lib/markets/logic";
import type { PortfolioRisk } from "@/lib/markets/types";

/**
 * Correlation heatmap of holdings on the diverging scale, clustered so similar holdings sit
 * together. The numbers are the backend's (the per-holding correlation vectors of GET
 * /holdings/{ticker}/prices, last 252 days); nothing is computed here. Neither that endpoint
 * nor GET /portfolio/risk returns a shared-observation count per pair, so the panel says so
 * rather than showing a sample size it does not have. A blank cell means the backend returned no
 * value for that pair, never 0.
 */
export function CorrelationPanel({
  grid,
  names,
  asOf,
  source,
  failed,
  risk,
}: {
  grid: CorrelationGrid;
  names: Readonly<Record<string, string>>;
  asOf: string | null;
  source: string | null;
  failed: readonly string[];
  risk: PortfolioRisk | null;
}) {
  const order = clusterOrder(grid.matrix);
  const tickers = order.map((i) => grid.tickers[i]);
  const matrix = order.map((i) => order.map((j) => grid.matrix[i][j]));
  const n = tickers.length;
  const pairs = (n * (n - 1)) / 2;
  let blanks = 0;
  const pairList: { a: string; b: string; r: number }[] = [];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const v = matrix[i][j];
      if (v === null) blanks += 1;
      else pairList.push({ a: tickers[i], b: tickers[j], r: v });
    }
  }
  pairList.sort((p, q) => q.r - p.r);
  const top = pairList[0];
  const label = (t: string) => names[t] ?? t;
  const state = n < 2 ? "not_covered" : failed.length > 0 || blanks > 0 || grid.withoutSeries.length > 0 ? "partial" : "verified";

  return (
    <ChartFrame
      id="correlation"
      kicker="Cross-holding correlation"
      title="How holdings move together"
      takeaway={
        n < 2
          ? "Fewer than two holdings have a stored price series, so no correlation exists to show."
          : top
            ? `${label(top.a)} and ${label(top.b)} are the most correlated pair (${formatNumber(top.r, 2)}). ${blanks} of ${pairs} pairs have no backend value and are left blank.`
            : "The backend returned no pairwise correlation for these holdings."
      }
      units="Correlation of daily returns, last 252 trading days, as returned by the backend; shared-observation counts per pair are not returned"
      hasValue={n >= 2 && pairList.length > 0}
      provenance={{
        source: `${source ?? "Stored price series"} via GET /holdings/{ticker}/prices (correlations)`,
        asOf,
        method: "One request per holding; each cell is the backend's value (row holding's own, else the mirrored one). Average-linkage clustering on 1 − r only orders rows and columns.",
        state,
        reason:
          [
            failed.length > 0 ? `Could not read ${failed.join(", ")}.` : null,
            grid.withoutSeries.length > 0 ? `No stored series for ${grid.withoutSeries.join(", ")}.` : null,
            grid.asymmetricPairs > 0 ? `${grid.asymmetricPairs} pair${grid.asymmetricPairs === 1 ? "" : "s"} where the two holdings' own values differ by more than 0.01.` : null,
            "Missing endpoint: a portfolio correlation matrix that also returns shared observations per pair.",
          ]
            .filter((x): x is string => x !== null)
            .join(" "),
        coverage: n >= 2 ? { covered: pairs - blanks, total: pairs, unit: "pairs" } : null,
      }}
      table={{
        caption: "Pairwise correlation as returned by the backend",
        columns: ["Pair", "Correlation"],
        rows: tickers.flatMap((a, i) => tickers.slice(i + 1).map((b, k) => [`${label(a)} / ${label(b)}`, matrix[i][i + 1 + k] === null ? "no backend value" : formatNumber(matrix[i][i + 1 + k]!, 2)])),
      }}
    >
      {/* The default scale key prints percent ("-100% to +100%"); a correlation runs -1 to +1. */}
      <HeatScale cap={1} format={(v) => formatSigned(v, 0)} className="self-end" />
      <Heatmap rows={tickers.map((t, i) => ({ label: t, cells: matrix[i] }))} columns={tickers} columnNames={tickers.map(label)} cap={1} formatCell={(v) => formatNumber(v, 2)} scale={false} caption="Correlation matrix, clustered" />
      {risk ? (
        <p className="m-0 text-xs text-text-muted">
          Portfolio context (GET /portfolio/risk, {risk.days} days
          {risk.portfolio.window_start && risk.portfolio.window_end ? `, ${formatDay(risk.portfolio.window_start)} to ${formatDay(risk.portfolio.window_end)}` : ""}): annualised volatility{" "}
          {risk.portfolio.annualised_volatility === null ? "unavailable" : formatPct(risk.portfolio.annualised_volatility, 1)}, beta {risk.beta === null || risk.beta === undefined ? "unavailable" : formatNumber(risk.beta, 2)}, {risk.portfolio.observations} observations
          {risk.portfolio.reason ? `; Sharpe unavailable: ${risk.portfolio.reason}` : ""}. This endpoint returns no correlation matrix.
        </p>
      ) : null}
    </ChartFrame>
  );
}
