"use client";

import { useCallback, useState, type FormEvent } from "react";

import { AlertMessage } from "@/components/alert-message";
import { useFundProfile } from "@/components/fund/fund-shell";
import { Absent, ResourceGate, SourceLink, Stat } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatFigure, toNumber } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { BENCHMARK_BASIS_OPTIONS, parseNavLines, pct, signedPct, type PerformanceView, type PeriodReturn } from "@/lib/fundresearch/fund-research";
import { useApiResource } from "@/lib/hooks/use-api-resource";

import { RecordFactsForm } from "./record-facts-form";

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
            <text x={pad.l - 6} y={y(t) + 3} textAnchor="end" className="fill-text-muted text-xs">{Math.round(t)}</text>
          </g>
        ))}
        {hasBench ? <path d={line((p) => toNumber(p.benchmark))} fill="none" className="stroke-chart-benchmark" strokeWidth="1.25" /> : null}
        {hasTotal ? <path d={line((p) => toNumber(p.fund_total))} fill="none" className="stroke-chart-line" strokeWidth="1.25" strokeDasharray="4 3" /> : null}
        <path d={line((p) => toNumber(p.fund))} fill="none" className="stroke-chart-line" strokeWidth="2.25" />
        <text x={pad.l} y={H - 6} className="fill-text-muted text-xs">{formatDate(pts[0].date)}</text>
        <text x={W - pad.r} y={H - 6} textAnchor="end" className="fill-text-muted text-xs">{formatDate(pts[pts.length - 1].date)}</text>
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
        {p.difference_annualised !== null && p.difference_annualised !== undefined ? <span className="block text-xs text-text-muted">{signedPct(p.difference_annualised)} a year</span> : null}
      </span>
    );
  }
  return p.compared_on === null && !p.note ? <span className="text-text-muted">—</span> : <Absent reason={p.note ? `n/m: ${p.note}` : "n/m: no same-basis return"} />;
}

function NavForm({ isin, currency: defaultCurrency, onSaved }: { isin: string; currency: string | null; onSaved: () => void }) {
  const [asOf, setAsOf] = useState("");
  const [nav, setNav] = useState("");
  const [pasted, setPasted] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency ?? "");
  const [sourceUrl, setSourceUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const rows: { as_of: string; nav: string }[] = [];
    if (asOf || nav.trim()) {
      const one = parseNavLines(`${asOf},${nav.trim()}`);
      if (one.bad.length > 0) return setError("Enter a date and a NAV above zero, for example 24.31.");
      rows.push(...one.rows);
    }
    const many = parseNavLines(pasted);
    if (many.bad.length > 0) return setError(`These lines are not in the form YYYY-MM-DD,value: ${many.bad.slice(0, 3).join("; ")}${many.bad.length > 3 ? "…" : ""}`);
    rows.push(...many.rows);
    if (rows.length === 0) return setError("Enter at least one date and NAV.");
    const code = currency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(code)) return setError("The NAV currency is a three-letter code such as EUR.");
    if (!/^https?:\/\//.test(sourceUrl.trim())) return setError("A source is required: paste the web address of the issuer page that publishes the NAV.");
    setError(null);
    setBusy(true);
    const { error: err } = await getBrowserApiClient().PUT("/funds/{isin}/nav", {
      params: { path: { isin } },
      body: rows.map((r) => ({ as_of: r.as_of, nav: r.nav, currency: code, source_url: sourceUrl.trim() })),
    });
    setBusy(false);
    if (err) return setError("The NAV could not be saved. Check the dates, values and address.");
    setSaved(`Saved ${rows.length} NAV ${rows.length === 1 ? "point" : "points"}.`);
    setAsOf("");
    setNav("");
    setPasted("");
    onSaved();
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-4">
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">NAV date</span>
        <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">NAV per share</span>
        <input value={nav} onChange={(e) => setNav(e.target.value)} inputMode="decimal" className="num w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-text-muted">NAV currency</span>
        <input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} className="w-full border border-border bg-surface px-2 py-1.5" />
      </label>
      <label className="text-sm sm:col-span-4">
        <span className="mb-1 block text-xs text-text-muted">Source (web address of the issuer page that publishes the NAV)</span>
        <input value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} className="w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs" />
      </label>
      <label className="text-sm sm:col-span-4">
        <span className="mb-1 block text-xs text-text-muted">Or paste several points, one per line as YYYY-MM-DD,value (they share the currency and source above)</span>
        <textarea value={pasted} onChange={(e) => setPasted(e.target.value)} rows={3} className="num w-full border border-border bg-surface px-2 py-1.5 font-mono text-xs" />
      </label>
      <div className="flex items-center gap-3 sm:col-span-4">
        <button type="submit" disabled={busy} className="border border-accent px-4 py-1.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-50">Save NAV</button>
        {error ? <p role="alert" className="text-sm text-loss">{error}</p> : null}
        {saved ? <p role="status" className="text-sm text-text-muted">{saved}</p> : null}
      </div>
    </form>
  );
}

function NavPanel({ isin, v, onSaved }: { isin: string; v: PerformanceView; onSaved: () => void }) {
  const nav = v.nav;
  return (
    <Blueprint aria-labelledby="fp-nav" className="space-y-4 px-[22px] py-5">
      <PanelHeading
        kicker={nav && nav.count > 0 ? `${nav.count} NAV ${nav.count === 1 ? "point" : "points"} you entered from the issuer` : "No NAV entered yet"}
        title={<span id="fp-nav">Premium or discount to NAV</span>}
      />
      <p className="max-w-[72ch] text-xs text-text-muted">
        Premium is the listing close divided by the NAV, minus 1; a positive figure means the listing traded above NAV. No data provider supplies NAV, so these points are only what you record from the issuer&apos;s own page.
      </p>
      {nav && nav.count > 0 ? (
        <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            <Stat label="Latest premium" note="listing close against NAV, same date">{signedPct(nav.latest_premium) ?? <Absent reason="missing: no entered date has a listing close" />}</Stat>
            <Stat label="Average gap" note="mean of absolute premiums">{pct(nav.mean_abs_premium) ?? <Absent reason="missing: no premium could be computed" />}</Stat>
            <Stat label="Largest gap" note="largest absolute premium">{pct(nav.max_abs_premium) ?? <Absent reason="missing: no premium could be computed" />}</Stat>
            <Stat
              label="NAV change"
              note={nav.nav_change_start && nav.nav_change_end ? `${formatDate(nav.nav_change_start)} to ${formatDate(nav.nav_change_end)}, excludes distributions` : "excludes distributions"}
            >
              {signedPct(nav.nav_change) ?? <Absent reason="missing: needs NAV on two different dates" />}
            </Stat>
          </dl>
          {nav.warnings.map((w) => (
            <AlertMessage key={w} title="Note">{w}</AlertMessage>
          ))}
          <div className="overflow-x-auto border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface-muted text-right">
                  <th scope="col" className="px-3 py-1.5 text-left font-medium">NAV date</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">NAV</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Listing close</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Premium</th>
                  <th scope="col" className="px-2 py-1.5 text-left font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {nav.points.map((p) => (
                  <tr key={p.as_of} className="border-t border-border-soft align-top">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{formatDate(p.as_of)}</th>
                    <td className="num px-2 py-1.5 text-right">{formatFigure(p.nav, "per_share")} {p.currency}</td>
                    <td className="num px-2 py-1.5 text-right">
                      {p.listing_close !== null && p.listing_close !== undefined ? (
                        <>
                          {formatFigure(p.listing_close, "per_share")}
                          {p.listing_date && p.listing_date !== p.as_of ? <span className="block text-xs text-text-muted">on {formatDate(p.listing_date)}</span> : null}
                        </>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </td>
                    <td className="num px-2 py-1.5 text-right">{signedPct(p.premium) ?? <Absent reason={`missing: ${p.reason ?? "no listing close on or near this date"}`} />}</td>
                    <td className="px-2 py-1.5 text-left"><SourceLink href={p.source_url}>issuer page</SourceLink></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <NavForm key={v.price_currency ?? "none"} isin={isin} currency={v.price_currency} onSaved={onSaved} />
      <p className="text-xs text-text-muted">{v.nav_note}</p>
    </Blueprint>
  );
}

function BenchmarkPanel({ isin, v, onSaved }: { isin: string; v: PerformanceView; onSaved: () => void }) {
  return (
    <Blueprint aria-labelledby="fp-bench" className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker="Used for the tracking difference" title={<span id="fp-bench">Record the fund&apos;s benchmark</span>} />
      <p className="max-w-[72ch] text-sm text-text-muted">
        {v.benchmark ? `${v.benchmark.label}: ${v.benchmark.note}` : "No benchmark is recorded for this fund. The benchmark can come from the facts you record here, with a source, or from the factsheet."}
      </p>
      <RecordFactsForm
        isin={isin}
        submitLabel="Record benchmark"
        onSaved={onSaved}
        fields={[
          { key: "benchmark_symbol", options: v.benchmark_options.map((o) => [o.symbol, `${o.label} (${o.symbol})`] as const) },
          { key: "benchmark_basis", options: BENCHMARK_BASIS_OPTIONS },
          { key: "benchmark_currency", hint: "Three letters, such as USD" },
        ]}
      />
      <p className="text-xs text-text-muted">The basis matters: a difference is shown only when the fund and the index are on the same basis and in the same currency.</p>
    </Blueprint>
  );
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
  const { state, reload } = useApiResource(fetcher);

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
                      <tr className="bg-surface-muted text-right">
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
                            {p.start ? <span className="block text-xs text-text-muted">from {formatDate(p.start)}</span> : null}
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
          <NavPanel isin={isin} v={v} onSaved={reload} />
          <BenchmarkPanel isin={isin} v={v} onSaved={reload} />
        </div>
      )}
    </ResourceGate>
  );
}
