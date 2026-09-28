"use client";

import { InfoIcon } from "lucide-react";
import { useId } from "react";

import { StatTile } from "@/components/portfolio/stat-tile";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { formatSignedPct } from "@/lib/portfolio/money";
import type { RiskFigures } from "@/lib/portfolio/performance";
import { formatSharpe, riskFreeShort, sharpeExplanation, sharpeLabel } from "@/lib/portfolio/sharpe";

const fetchRisk = () => getBrowserApiClient().GET("/portfolio/risk", { params: { query: { days: 365 } } });

/**
 * The Overview risk tile: the portfolio's 1-year Sharpe ratio (GET /portfolio/risk: time-
 * weighted CZK returns over CZEONIA) with its window, observations and risk-free rate, and
 * the history-derived volatility, max drawdown and beta underneath.
 */
export function RiskStatTile({
  figures,
  historyLoading,
  spark,
  sparkNote,
}: {
  figures: RiskFigures | null;
  historyLoading: boolean;
  spark: readonly number[];
  sparkNote: string;
}) {
  const { state } = useApiResource(fetchRisk);
  const risk = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const tipId = useId();
  const p = risk?.portfolio ?? null;
  const rf = risk ? riskFreeShort(risk) : null;
  const benchmark = risk?.benchmark?.sharpe ?? null;

  const secondary = figures ? (
    <>
      Vol {figures.volatility != null ? formatPct(figures.volatility, 1) : "—"} · Max DD{" "}
      {figures.maxDrawdown != null ? formatPct(figures.maxDrawdown, 1) : "—"}
      {figures.beta != null ? <> · β {formatNumber(figures.beta, 2)}</> : null}
      {figures.return != null ? <> · return {formatSignedPct(figures.return)}</> : null}
    </>
  ) : historyLoading ? (
    "Loading history…"
  ) : (
    "Not enough history yet"
  );

  return (
    <StatTile
      label={
        <span className="inline-flex items-center gap-1">
          {risk ? sharpeLabel(risk) : "Sharpe · 1Y"}
          {risk ? (
            <>
              <button
                type="button"
                aria-label="How the Sharpe ratio is computed"
                aria-describedby={tipId}
                title={sharpeExplanation(risk)}
                className="inline-flex cursor-help text-text-faint outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <InfoIcon aria-hidden="true" className="size-3.5" />
              </button>
              <span id={tipId} className="sr-only">
                {sharpeExplanation(risk)}
              </span>
            </>
          ) : null}
        </span>
      }
      value={state.kind === "loading" ? "…" : formatSharpe(p?.sharpe)}
      sub={
        <>
          <span className="block">
            {p && p.sharpe !== null && p.window_start && p.window_end ? (
              <>
                {formatDate(p.window_start)} – {formatDate(p.window_end)} · n {p.observations}
                {rf ? <> · {rf}</> : null}
              </>
            ) : state.kind === "loading" ? (
              "Loading…"
            ) : state.kind === "error" && !risk ? (
              `Could not load: ${state.title}`
            ) : (
              (p?.reason ?? "No ratio yet")
            )}
          </span>
          <span className="block">
            {secondary}
            {benchmark !== null && risk?.benchmark_symbol ? <> · {risk.benchmark_symbol} {formatSharpe(benchmark)}</> : null}
          </span>
        </>
      }
      spark={spark}
      sparkNote={sparkNote}
    />
  );
}
