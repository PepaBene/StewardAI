"use client";

import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { useFundProfile } from "@/components/fund/fund-shell";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { toNumber } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { pct, signedPct, type PerformanceView, type PeriodReturn } from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";

function Chart({ v }: { v: PerformanceView }) {
  const pts = v.series;
  if (pts.length < 2) return <p className="text-sm text-text-muted">Too few stored prices to draw a chart.</p>;
  const vals = pts.flatMap((p) => [toNumber(p.fund), toNumber(p.fund_total), toNumber(p.benchmark)].filter((n): n is number => n !== null));
  const lo = Math.min(...vals, 100);
  const hi = Math.max(...vals, 100);
  const W = 680;
  const H = 240;
  const pad = { l: 44, r: 16, t: 10, b: 22 };
  const x = (i: number) => pad.l + (i / (pts.length - 1)) * (W - pad.l - pad.r);
  const y = (n: number) => pad.t + (hi === lo ? 0.5 : 1 - (n - lo) / (hi - lo)) * (H - pad.t - pad.b);
  const line = (pick: (p: (typeof pts)[number]) => number | null) => {
    let d = "";
    let pen = false;
    pts.forEach((p, i) => {
      const n = pick(p);
      if (n === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(n).toFixed(1)} `;
      pen = true;
    });
    return d;
  };
  const hasTotal = pts.some((p) => p.fund_total !== null && p.fund_total !== undefined);
  const hasBench = pts.some((p) => p.benchmark !== null && p.benchmark !== undefined);
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full max-w-3xl" role="img" aria-label="Fund listing price and benchmark, indexed to 100 at the start of the window">
        {[lo, 100, hi].filter((t, i, a) => a.indexOf(t) === i).map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} className={t === 100 ? "stroke-border" : "stroke-border-soft"} strokeDasharray={t === 100 ? "" : "2 3"} />
            <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" className="fill-text-muted text-[10px]">{Math.round(t)}</text>
          </g>
        ))}
        {hasBench ? <path d={line((p) => toNumber(p.benchmark))} fill="none" className="stroke-chart-benchmark" strokeWidth="1.25" /> : null}
        {hasTotal ? <path d={line((p) => toNumber(p.fund_total))} fill="none" className="stroke-chart-line" strokeWidth="1.25" strokeDasharray="4 3" /> : null}
        <path d={line((p) => toNumber(p.fund))} fill="none" className="stroke-chart-line" strokeWidth="2.25" />
        <text x={pad.l} y={H - 6} className="fill-text-muted text-[10px]">{formatDate(pts[0].date)}</text>
        <text x={W - pad.r} y={H - 6} textAnchor="end" className="fill-text-muted text-[10px]">{formatDate(pts[pts.length - 1].date)}</text>
      </svg>
      <figcaption className="flex flex-wrap gap-x-5 text-xs text-text-muted">
        <span><span className="mr-1.5 inline-block h-0.5 w-5 bg-chart-line align-middle" />Fund listing price</span>
        {hasTotal ? <span><span className="mr-1.5 inline-block h-0 w-5 border-t border-dashed border-chart-line align-middle" />Fund with distributions reinvested</span> : null}
        {hasBench ? <span><span className="mr-1.5 inline-block h-0.5 w-5 bg-chart-benchmark align-middle" />{v.benchmark?.label}</span> : null}
      </figcaption>
    </figure>
  );
}

function Cell({ r }: { r: { value?: number | string | null; warnings?: string[] } | null | undefined }) {
  if (!r) return <span className="text-text-muted">—</span>;
  return <span className="num">{signedPct(r.value) ?? <Absent reason={r.warnings?.[0] ? `missing: ${r.warnings[0]}` : "missing: not computable"} />}</span>;
}

function DifferenceCell({ p }: { p: PeriodReturn }) {
  if (p.difference !== null && p.difference !== undefined) {
    return (
      <span className="num">
        {signedPct(p.difference)}
        {p.difference_annualised !== null && p.difference_annualised !== undefined ? <span className="block text-[11px] text-text-muted">{signedPct(p.difference_annualised)} a year</span> : null}
      </span>
    );
  }
  return p.compared_on === null && !p.note ? <span className="text-text-muted">—</span> : <Absent reason={p.note ? `n/m: ${p.note}` : "n/m: no same-basis return"} />;
}

export function PerformanceTab({ isin }: { isin: string }) {
  const profile = useFundProfile();
  const listings = profile.kind === "ok" ? profile.data.identity.listings.filter((l) => l.has_prices) : [];
  const [ticker, setTicker] = useState<string | null>(null);
  const [benchmark, setBenchmark] = useState<string | null>(null);

  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/{isin}/performance", { params: { path: { isin }, query: { ...(ticker ? { ticker } : {}), ...(benchmark ? { benchmark } : {}) } } }),
    [isin, ticker, benchmark],
  );
  const { state } = useApiResource(fetcher);

  return (
    <ResourceGate state={state} label="performance">
      {(v: PerformanceView) => (
        <div className="flex flex-col gap-5">
          <Blueprint className="px-[22px] py-4">
            <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
              {listings.length > 1 ? (
                <label className="text-sm">
                  <span className="mb-1 block text-xs text-text-muted">Exchange listing</span>
                  <select value={ticker ?? v.listing?.ticker ?? ""} onChange={(e) => setTicker(e.target.value)} className="border border-border bg-surface px-2 py-1.5">
                    {listings.map((l) => (
                      <option key={l.ticker} value={l.ticker}>{l.ticker} ({l.currency})</option>
                    ))}
                  </select>
                </label>
              ) : v.listing ? (
                <p className="text-sm"><span className="text-xs text-text-muted">Listing </span>{v.listing.ticker} ({v.price_currency ?? "currency unknown"})</p>
              ) : null}
              <label className="text-sm">
                <span className="mb-1 block text-xs text-text-muted">Benchmark</span>
                <select value={benchmark ?? ""} onChange={(e) => setBenchmark(e.target.value || null)} className="border border-border bg-surface px-2 py-1.5">
                  <option value="">No benchmark</option>
                  {v.benchmark_options.filter((o) => o.has_prices).map((o) => (
                    <option key={o.symbol} value={o.symbol}>{o.label} ({o.basis === "unknown" ? "basis unknown" : `${o.basis} return`})</option>
                  ))}
                </select>
              </label>
              {v.suggested_benchmark && benchmark !== v.suggested_benchmark ? (
                <button type="button" onClick={() => setBenchmark(v.suggested_benchmark ?? null)} className="pb-1.5 text-sm text-accent-ink hover:underline">
                  Use the factsheet&apos;s benchmark ({v.suggested_benchmark})
                </button>
              ) : null}
            </div>
          </Blueprint>

          <AlertMessage title="Listing price, not NAV">{v.nav_note}</AlertMessage>
          {v.warnings.map((w) => (
            <AlertMessage key={w} title="Note">{w}</AlertMessage>
          ))}

          {v.periods.length === 0 ? (
            <Blueprint className="px-[22px] py-5"><p className="text-sm text-text-muted">No return can be shown without stored prices for an exchange listing of this fund.</p></Blueprint>
          ) : (
            <>
              <Blueprint aria-labelledby="fp-chart" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker={v.alignment ? `${formatDate(v.alignment.start ?? "")} to ${formatDate(v.alignment.end ?? "")} · ${v.alignment.sessions} sessions` : ""} title={<span id="fp-chart">Growth of 100</span>} />
                <Chart v={v} />
                {v.alignment ? <p className="text-xs text-text-muted">{v.alignment.note}</p> : null}
              </Blueprint>
              <Blueprint aria-labelledby="fp-returns" className="space-y-3 px-[22px] py-5">
                <PanelHeading
                  kicker={v.benchmark ? `Against ${v.benchmark.label}: ${v.benchmark.basis === "unknown" ? "return basis not recorded" : `${v.benchmark.basis}-return index`}${v.benchmark.currency ? `, ${v.benchmark.currency}` : ", currency not recorded"}` : "Fund only"}
                  title={<span id="fp-returns">Returns and tracking difference</span>}
                />
                <div className="overflow-x-auto border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-surface-sunken text-right">
                        <th scope="col" className="px-3 py-1.5 text-left font-medium">Window</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">Listing price</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">With distributions</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">Benchmark</th>
                        <th scope="col" className="px-2 py-1.5 font-medium">Difference</th>
                      </tr>
                    </thead>
                    <tbody>
                      {v.periods.map((p) => (
                        <tr key={p.label} className="border-t border-border-soft align-top">
                          <th scope="row" className="px-3 py-1.5 text-left font-normal">
                            {p.label}
                            {p.start ? <span className="block text-[11px] text-text-muted">from {formatDate(p.start)}</span> : null}
                          </th>
                          <td className="px-2 py-1.5 text-right"><Cell r={p.fund_price} /></td>
                          <td className="px-2 py-1.5 text-right"><Cell r={p.fund_total} /></td>
                          <td className="px-2 py-1.5 text-right">{v.benchmark ? <Cell r={p.benchmark} /> : <span className="text-text-muted">—</span>}</td>
                          <td className="px-2 py-1.5 text-right">{v.benchmark ? <DifferenceCell p={p} /> : <span className="text-text-muted">—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="max-w-[72ch] text-xs text-text-muted">
                  A difference appears only when the fund and the index are on the same basis (listing price against a price index, or fund with distributions against a total-return index) and in the same currency. Otherwise both returns are shown and the difference is withheld with the reason.
                  {v.fund_policy ? ` This share class is ${v.fund_policy}.` : ""}
                </p>
              </Blueprint>
              <Blueprint aria-labelledby="fp-risk" className="space-y-3 px-[22px] py-5">
                <PanelHeading kicker="Daily listing-price returns over the aligned window" title={<span id="fp-risk">Volatility, drawdown and tracking error</span>} />
                <ul className="space-y-2 text-sm">
                  {v.risk.map((r) => (
                    <li key={r.id} className="flex flex-wrap items-baseline justify-between gap-x-6 border-b border-border-soft pb-1.5">
                      <span>{r.label}</span>
                      <span className="num">
                        Fund {pct(r.fund.value) ?? <Absent reason={r.fund.warnings?.[0] ? `missing: ${r.fund.warnings[0]}` : "missing: too few observations"} />}
                        {r.benchmark ? <> · Benchmark {pct(r.benchmark.value) ?? <Absent reason="missing: too few observations" />}</> : null}
                      </span>
                    </li>
                  ))}
                  {v.tracking_error ? (
                    <li className="flex flex-wrap items-baseline justify-between gap-x-6">
                      <span>Tracking error, annualised</span>
                      <span className="num">{pct(v.tracking_error.value) ?? <Absent reason={v.tracking_error.warnings?.[0] ? `n/m: ${v.tracking_error.warnings[0]}` : "n/m: needs matching bases"} />}</span>
                    </li>
                  ) : null}
                </ul>
              </Blueprint>
            </>
          )}
        </div>
      )}
    </ResourceGate>
  );
}
