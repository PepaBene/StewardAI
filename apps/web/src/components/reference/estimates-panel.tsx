"use client";

import { useCallback, useMemo, useState } from "react";

import { BarChart, SrTable, VintageFan } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  consensusPeriods,
  dispersion,
  KIND_LABEL,
  latestVintage,
  metricLabel,
  parseForward,
  type ConsensusSeries,
  type OtherEstimate,
} from "@/lib/reference/forward";
import { parseSurprise } from "@/lib/reference/holding-charts";
import { cn } from "@/lib/utils";

import { ForwardEntry } from "./forward-entry";

const fmt = (v: number | null) => (v === null ? "Not published" : formatNumber(v, 2));

/**
 * GET /holdings/{ticker}/forward-data/consensus: what published consensus looked like at each
 * vintage, with guidance, your own scenarios and calendar estimates listed under their own
 * labels. A missing consensus stays missing; the app never estimates one.
 */
export function EstimatesPanel({ ticker }: { ticker: string }) {
  const [period, setPeriod] = useState<string | null>(null);
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/forward-data/consensus", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => parseForward(raw), [raw]);
  const periods = view ? consensusPeriods(view) : [];
  const shown = period && periods.includes(period) ? period : (periods[periods.length - 1] ?? null);
  const series = view ? view.consensus.filter((s) => s.fiscalPeriod === shown) : [];

  return (
    <Blueprint aria-labelledby="estimates-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Forward estimates · by vintage"
        title={<span id="estimates-heading">Estimates and guidance</span>}
        action={
          periods.length > 1 ? (
            <div role="group" aria-label="Fiscal period" className="inline-flex border border-border text-xs font-semibold">
              {periods.map((p, i) => (
                <button
                  key={p}
                  type="button"
                  aria-pressed={shown === p}
                  onClick={() => setPeriod(p)}
                  className={cn(
                    "num px-2.5 py-1.5",
                    i > 0 && "border-l border-border",
                    shown === p ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft",
                  )}
                >
                  {p}
                </button>
              ))}
            </div>
          ) : null
        }
      />
      {state.kind === "loading" && !view ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !view ? (
        <p className="text-sm text-text-muted">
          Estimates unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {view && view.consensus.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No published analyst consensus is stored for this holding. Free sources rarely publish one, so it stays empty
          rather than being estimated. Company guidance and your own scenarios appear below when recorded.
        </p>
      ) : null}

      {series.length > 0 ? (
        <ul className="grid grid-cols-1 gap-px border border-border bg-border md:grid-cols-2">
          {series.map((s) => (
            <ConsensusCard key={s.key} series={s} />
          ))}
        </ul>
      ) : null}

      {view && view.consensus.length > 0 ? <SurpriseCharts ticker={ticker} series={view.consensus} /> : null}

      {view && view.others.length > 0 ? <OtherEstimates rows={view.others} /> : null}

      <ForwardEntry ticker={ticker} onSaved={reload} />
    </Blueprint>
  );
}

const SURPRISE_METRICS = new Set(["revenue", "eps_diluted", "net_income", "operating_income", "capex"]);
const FISCAL_PERIOD = /^FY\d{4}(Q[1-4]|H[12]|M9)?$/;

/**
 * GET /holdings/{ticker}/actual-vs-consensus, one call per consensus metric and fiscal period:
 * the reported figure against the last consensus known before the report, as a percentage of
 * the absolute consensus. Actuals and consensus are never blended: a period with no consensus
 * known before its report is a gap with the reason, not a zero.
 */
function SurpriseCharts({ ticker, series }: { ticker: string; series: ConsensusSeries[] }) {
  const pairs = useMemo(() => {
    const seen = new Map<string, { metric: string; period: string }>();
    for (const s of series) {
      if (SURPRISE_METRICS.has(s.metric) && FISCAL_PERIOD.test(s.fiscalPeriod)) {
        seen.set(`${s.metric}|${s.fiscalPeriod}`, { metric: s.metric, period: s.fiscalPeriod });
      }
    }
    return [...seen.values()].sort((a, b) => a.period.localeCompare(b.period)).slice(-24);
  }, [series]);
  const pairKey = pairs.map((p) => `${p.metric}|${p.period}`).join(",");
  const fetcher = useCallback(async () => {
    const client = getBrowserApiClient();
    const rows = await Promise.all(
      pairs.map(async (p) => {
        try {
          const r = await client.GET("/holdings/{ticker}/actual-vs-consensus", {
            params: { path: { ticker }, query: { metric: p.metric, fiscal_period: p.period } },
          });
          return parseSurprise(p.metric, p.period, r.response.ok ? r.data : null);
        } catch {
          return parseSurprise(p.metric, p.period, null);
        }
      }),
    );
    return { data: rows, response: new Response(null, { status: 200 }) };
    // pairKey is the stable identity of `pairs`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, pairKey]);
  const { state } = useApiResource(fetcher);
  const rows = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const metrics = [...new Set((rows ?? []).map((r) => r.metric))];
  if (!rows || pairs.length === 0) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">Surprise against consensus</h3>
      <div className="grid gap-4 md:grid-cols-2">
        {metrics.map((metric) => {
          const mine = rows.filter((r) => r.metric === metric);
          const missing = mine.filter((r) => r.fraction === null);
          return (
            <div key={metric} className="space-y-1">
              <BarChart
                label={`${metricLabel(metric)} surprise by fiscal period, percent of consensus`}
                data={mine.map((r) => ({ label: r.period.replace("FY", ""), value: r.fraction === null ? null : r.fraction * 100 }))}
                formatValue={(v) => `${formatNumber(v, 1)}%`}
                height={140}
              />
              <p className="text-xs text-text-muted">
                {metricLabel(metric)}: reported minus consensus, as % of the absolute consensus.
                {missing.length > 0 ? ` No bar: ${missing.map((r) => `${r.period} (${r.reason ?? "not available"})`).join("; ")}.` : ""}
              </p>
              <SrTable
                caption={`${metricLabel(metric)} reported figure against consensus`}
                columns={["Period", "Reported", "Consensus", "Surprise"]}
                rows={mine.map((r) => [
                  r.period,
                  r.actual === null ? "Not published" : fmt(r.actual),
                  r.consensus === null ? "Not published" : fmt(r.consensus),
                  r.fraction === null ? (r.reason ?? "Not available") : `${formatNumber(r.fraction * 100, 1)}%`,
                ])}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ConsensusCard({ series }: { series: ConsensusSeries }) {
  const last = latestVintage(series);
  const spread = dispersion(last);
  const label = `${metricLabel(series.metric)} ${series.fiscalPeriod} consensus by vintage`;
  return (
    <li className="flex flex-col gap-1 bg-surface px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{metricLabel(series.metric)}</span>
        <span className="text-xs text-text-muted">
          {[series.provider, series.currency, series.basis].filter(Boolean).join(", ")}
        </span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="num text-lg leading-tight">{last ? fmt(last.value) : "No value"}</div>
          <div className="num text-xs text-text-muted">
            {last && last.low !== null && last.high !== null ? `Range ${fmt(last.low)} to ${fmt(last.high)}` : "Range not published"}
            {spread !== null ? ` (${formatPct(spread, 0)} wide)` : ""}
          </div>
          <div className="num text-xs text-text-muted">
            {last?.analystCount ? `${last.analystCount} analysts` : "Analyst count not published"}
            {last?.knownAt ? `, known ${formatDate(last.knownAt)}` : ""}
          </div>
        </div>
        <VintageFan
          className="w-44 shrink-0"
          label={label}
          formatValue={(v) => formatNumber(v, 2)}
          points={series.vintages.map((v) => ({ at: v.knownAt, value: v.value, low: v.low, high: v.high }))}
        />
      </div>
      <p className="num text-xs text-text-muted">
        {series.revision === null
          ? series.vintages.length < 2
            ? "One vintage so far, no revision yet"
            : "Revision not computable"
          : `${series.revision > 0 ? "Up" : series.revision < 0 ? "Down" : "Unchanged"}${
              series.revision === 0 ? "" : ` ${formatNumber(Math.abs(series.revision), 2)}`
            } since the previous vintage`}
        {series.vintages.length >= 2 ? ` (${series.vintages.length} vintages)` : ""}
      </p>
    </li>
  );
}

function OtherEstimates({ rows }: { rows: OtherEstimate[] }) {
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-semibold">Guidance and scenarios</h3>
      <div className="relative overflow-x-auto border border-border">
        <table className="w-full min-w-[40rem] text-sm">
          <caption className="sr-only">Company guidance, your scenarios and calendar estimates, kept apart from consensus</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-xs text-text-muted">
              <th scope="col" className="px-3 py-2 text-left font-medium">Kind</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Metric</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Range</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-border align-top last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-normal">
                  <span
                    className={cn(
                      "inline-block border px-1.5 py-0.5 text-xs font-medium",
                      r.kind === "owner_scenario" ? "border-dashed border-border text-text-muted" : "border-accent-300 bg-accent-soft text-text",
                    )}
                  >
                    {KIND_LABEL[r.kind]}
                  </span>
                </th>
                <td className="px-3 py-2">{metricLabel(r.metric)}</td>
                <td className="num px-3 py-2">{r.fiscalPeriod ?? "Not stated"}</td>
                <td className="num px-3 py-2 text-right">
                  {r.value === null ? "Not published" : `${r.currency ? `${r.currency} ` : ""}${formatNumber(r.value, 2)}`}
                </td>
                <td className="num px-3 py-2 text-right">
                  {r.low !== null && r.high !== null ? `${formatNumber(r.low, 2)} to ${formatNumber(r.high, 2)}` : "Not published"}
                </td>
                <td className="px-3 py-2 text-xs text-text-muted">
                  {r.provider ?? "Unknown"}
                  {r.knownAt ? `, ${formatDate(r.knownAt)}` : ""}
                  {r.sourceUrl ? (
                    <>
                      {" "}
                      <a href={r.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                        Source
                      </a>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
