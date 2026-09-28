"use client";

import { LoaderCircleIcon } from "lucide-react";

import { HBar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type PortfolioValuation } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { displayTicker } from "@/lib/portfolio/holdings-view";
import { formatMultiple, formatYield, relativeTo } from "@/lib/portfolio/valuation";

const fetchValuation = () => getBrowserApiClient().GET("/portfolio/valuation");

type Row = {
  key: "pe" | "pb" | "dividend_yield";
  label: string;
  portfolio: number | null;
  benchmark: number | null;
  coverage: number;
  format: (v: number | null) => string;
};

function Compare({ row, benchLabel }: { row: Row; benchLabel: string }) {
  const values = [row.portfolio, row.benchmark].filter((v): v is number => v !== null && Number.isFinite(v));
  const max = values.length ? Math.max(...values) * 1.25 : 1;
  const gap = relativeTo(row.portfolio, row.benchmark);
  return (
    <div className="grid grid-cols-[minmax(0,6.5rem)_1fr] items-center gap-x-3 gap-y-1">
      <div className="text-[12px] text-text-muted">{row.label}</div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="num font-heading text-[26px] leading-none font-semibold">{row.format(row.portfolio)}</span>
        <span className="text-right text-[11px] text-text-faint">
          {benchLabel} <span className="num text-text-muted">{row.format(row.benchmark)}</span>
          {gap !== null ? (
            <span className="num">
              {" "}
              ({gap > 0 ? "▲ +" : gap < 0 ? "▼ " : ""}
              {formatPct(gap, 0)})
            </span>
          ) : null}
        </span>
      </div>
      <div />
      <HBar
        value={row.portfolio}
        max={max}
        target={row.benchmark}
        size="sm"
        label={`${row.label}: portfolio against ${benchLabel}`}
        formatValue={(v) => row.format(v)}
      />
      <div />
      <div className="text-[11px] text-text-faint">covers {formatPct(row.coverage, 0)} of invested value</div>
    </div>
  );
}

/**
 * Overview: the portfolio's P/E, P/B and dividend yield against the benchmark's fund-level
 * figures (S&P 500 via CSPX by default), computed with the same capping and exclusion rules,
 * with the share of invested value each figure covers. Other configured fund and index
 * figures, the method and the per-holding inputs are below.
 */
export function ValuationBenchmarkPanel() {
  const { state } = useApiResource(fetchValuation);
  const data: PortfolioValuation | null = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Blueprint aria-labelledby="valuation-benchmark-heading" className="flex min-w-0 flex-col gap-3 px-6 py-5.5">
      <PanelHeading
        kicker="Valuation"
        title={<span id="valuation-benchmark-heading">Valuation vs benchmark</span>}
        action={
          data?.benchmark ? (
            <span className="text-[11px] text-text-faint">
              {data.benchmark.via}, {formatDate(data.benchmark.as_of)}
            </span>
          ) : null
        }
      />
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading valuation…
        </p>
      ) : null}
      {state.kind === "error" && !data ? <p className="text-sm text-text-muted">Could not load the valuation: {state.title}</p> : null}
      {data ? <Body data={data} /> : null}
    </Blueprint>
  );
}

function Body({ data }: { data: PortfolioValuation }) {
  const p = data.portfolio;
  const b = data.benchmark;
  const benchLabel = b?.label ?? "Benchmark";
  if (data.holdings.length === 0) {
    return <p className="text-sm text-text-muted">No positions in the latest snapshot.</p>;
  }
  const rows: Row[] = [
    { key: "pe", label: "P/E", portfolio: p.pe ?? null, benchmark: b?.pe ?? null, coverage: p.coverage.pe, format: (v) => formatMultiple(v) },
    { key: "pb", label: "P/B", portfolio: p.pb ?? null, benchmark: b?.pb ?? null, coverage: p.coverage.pb, format: (v) => formatMultiple(v, 2) },
    {
      key: "dividend_yield",
      label: "Dividend yield",
      portfolio: p.dividend_yield ?? null,
      benchmark: b?.dividend_yield ?? null,
      coverage: p.coverage.dividend_yield,
      format: (v) => formatYield(v),
    },
  ];
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="flex min-w-0 flex-col gap-4">
        {rows.map((r) => (
          <Compare key={r.key} row={r} benchLabel={benchLabel} />
        ))}
      </div>
      <div className="flex min-w-0 flex-col gap-3">
      {!b ? (
        <p className="text-[12px] text-text-muted">
          No benchmark figures stored: the benchmark is compared through its ETF&rsquo;s fund-level figures (Config → Jobs → valuation
          funds), fetched by the valuation refresh job.
        </p>
      ) : null}
      {data.comparisons.length > 0 ? (
        <table className="w-full text-[12px]">
          <caption className="sr-only">Fund and index valuation figures</caption>
          <thead>
            <tr className="text-left text-[10px] tracking-wide text-text-muted uppercase">
              <th className="py-1.5 font-medium">Compared with</th>
              <th className="py-1.5 text-right font-medium">P/E</th>
              <th className="py-1.5 text-right font-medium">P/B</th>
              <th className="hidden py-1.5 text-right font-medium sm:table-cell">As of</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-border-soft">
              <td className="py-1.5 font-medium">Portfolio</td>
              <td className="num py-1.5 text-right">{formatMultiple(p.pe)}</td>
              <td className="num py-1.5 text-right">{formatMultiple(p.pb, 2)}</td>
              <td className="hidden py-1.5 text-right text-text-faint sm:table-cell">{data.as_of ? formatDate(data.as_of) : "—"}</td>
            </tr>
            {data.comparisons.map((c) => (
              <tr key={`${c.fund_key}-${c.label}`} className="border-t border-border-soft">
                <td className="py-1.5">
                  {c.label}
                  <span className="block text-[11px] text-text-faint">
                    {c.via}
                    {c.stale ? " · stale" : ""}
                  </span>
                </td>
                <td className="num py-1.5 text-right">{formatMultiple(c.pe)}</td>
                <td className="num py-1.5 text-right">{formatMultiple(c.pb, 2)}</td>
                <td className="hidden py-1.5 text-right text-text-faint sm:table-cell">{formatDate(c.as_of)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      <details className="border-t border-border pt-2 text-[12px] text-text-muted xl:col-span-2">
        <summary className="cursor-pointer">Method and holdings</summary>
        <p className="mt-2 leading-snug">{data.method}</p>
        {b?.methodology ? <p className="mt-2 leading-snug text-text-faint">Benchmark: {b.methodology}</p> : null}
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[26rem] text-[12px]">
            <caption className="sr-only">Each holding&rsquo;s contribution</caption>
            <thead>
              <tr className="text-left text-[10px] tracking-wide uppercase">
                <th className="py-1 font-medium">Holding</th>
                <th className="py-1 text-right font-medium">Weight</th>
                <th className="py-1 text-right font-medium">P/E used</th>
                <th className="py-1 text-right font-medium">P/B</th>
                <th className="py-1 text-right font-medium">Yield</th>
                <th className="py-1 pl-3 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => (
                <tr key={h.ticker} className="border-t border-border-soft">
                  <td className="py-1 font-mono">{displayTicker(h.ticker)}</td>
                  <td className="num py-1 text-right">{formatPct(h.weight, 1)}</td>
                  <td className="num py-1 text-right" title={h.pe_note ?? undefined}>
                    {h.pe !== null && h.pe !== undefined ? formatMultiple(h.pe) : h.pe_note?.startsWith("n/m") ? "n/m" : "—"}
                    {h.pe_note?.startsWith("capped") ? "*" : ""}
                  </td>
                  <td className="num py-1 text-right">{formatMultiple(h.pb, 2)}</td>
                  <td className="num py-1 text-right">{formatYield(h.dividend_yield)}</td>
                  <td className="py-1 pl-3 text-text-faint">
                    {h.source_label ?? "—"}
                    {h.stale ? " · stale" : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      </div>
    </div>
  );
}
