"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { AXIS_TEXT, ChartEmpty, Legend, SVG_BASE } from "@/components/charts/shared";
import { ResourceGate } from "@/components/company/primitives";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { linePath } from "@/lib/charts/path";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * Valuation pictures (insight I7): the key multiple beside peers, inside the sector of the covered
 * universe, against the company's own history and against a peer regression. Each picture shows
 * its stated reason instead of a chart when the data is too thin, and carries its footer.
 */

type Pictures = components["schemas"]["ValuationPictures"];
type Peers = Pictures["peers"];
type Hist = Pictures["histogram"];
type History = Pictures["history"];
type Fair = Pictures["fair_multiple"];

const x1 = (v: unknown) => {
  const n = num(v as number | string | null);
  return n === null ? "—" : `${n.toFixed(1)}×`;
};
const pct = (v: unknown) => {
  const n = num(v as number | string | null);
  return n === null ? "—" : `${(n * 100).toFixed(1)}%`;
};

function Absent({ reason }: { reason: string | null | undefined }) {
  return <p className="border border-dashed border-border px-3 py-4 text-sm text-text-muted">{reason ?? "Not enough data to draw this picture."}</p>;
}

/* ------------------------------------------------------------------ peer bars (I7.1) */

function PeerBarsChart({ bars, median, label }: { bars: NonNullable<Peers["bars"]>; median: number | null; label: string }) {
  const vals = bars.map((b) => num(b.value)).filter((v): v is number => v !== null && v > 0);
  if (!vals.length) return <ChartEmpty label="No peer has this multiple" minHeight={120} />;
  const max = Math.max(...vals, median ?? 0) * 1.1;
  const row = 22;
  const W = 640;
  const labelW = 150;
  const H = bars.length * row + 18;
  const x = linearScale([0, max], [labelW, W - 56]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={label}>
      {bars.map((b, i) => {
        const v = num(b.value);
        const y = i * row + 4;
        return (
          <g key={b.ticker}>
            <text x={labelW - 8} y={y + 12} textAnchor="end" fontSize={11} className={b.is_subject ? "fill-text font-semibold" : "fill-text-muted"}>
              {(b.name ?? b.ticker).slice(0, 22)}
            </text>
            {v !== null && v > 0 ? (
              <>
                <rect x={labelW} y={y + 2} width={Math.max(1, x(v) - labelW)} height={row - 8} rx={2} className={b.is_subject ? "fill-accent" : "fill-accent-200"} />
                <text x={x(v) + 4} y={y + 12} fontSize={11} className="num fill-text">
                  {v.toFixed(1)}×
                </text>
              </>
            ) : (
              <text x={labelW + 4} y={y + 12} fontSize={11} className="fill-text-faint">
                {b.reason ?? "n/m"}
              </text>
            )}
          </g>
        );
      })}
      {median !== null ? (
        <g>
          <line x1={x(median)} x2={x(median)} y1={0} y2={H - 14} className="stroke-text" strokeDasharray="3 3" />
          <text x={x(median)} y={H - 2} textAnchor="middle" fontSize={11} className="fill-text">
            Peer median {median.toFixed(1)}×
          </text>
        </g>
      ) : null}
    </svg>
  );
}

function PeerPanel({ p, ticker }: { p: Peers; ticker: string }) {
  const [forward, setForward] = useState(false);
  const useFwd = forward && p.forward_available;
  const bars = (useFwd ? p.forward_bars : p.bars) ?? [];
  const median = num(useFwd ? p.forward_median : p.median);
  const title = useFwd ? "Forward P/E" : (p.label ?? "Key multiple");
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading
        kicker={p.scope_label}
        title={`${title} against peers`}
        action={
          p.forward_available ? (
            <div role="group" aria-label="Multiple basis" className="flex border border-border text-xs">
              {[false, true].map((f) => (
                <button key={String(f)} type="button" aria-pressed={forward === f} onClick={() => setForward(f)} className={cn("px-2.5 py-1", forward === f ? "bg-accent-soft text-accent-ink" : "text-text-muted")}>
                  {f ? "Forward" : "Trailing"}
                </button>
              ))}
            </div>
          ) : null
        }
      />
      <p className="text-xs text-text-muted">
        {p.rule}
        {!p.forward_available && p.forward_reason ? ` Forward view not shown: ${p.forward_reason}.` : ""}
      </p>
      {!p.available ? (
        <Absent reason={p.reason} />
      ) : (
        <ChartWithFooter
          chart={<PeerBarsChart bars={bars} median={median} label={`${title} of the company and its peers`} />}
          table={
            <table className="w-full text-sm">
              <caption className="sr-only">{title} and filed revenue growth per company</caption>
              <thead>
                <tr className="bg-surface-muted text-xs text-text-muted">
                  <th scope="col" className="px-2 py-1.5 text-left font-medium">Company</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">{title}</th>
                  <th scope="col" className="px-2 py-1.5 text-right font-medium">Revenue growth (3y)</th>
                </tr>
              </thead>
              <tbody>
                {bars.map((b) => (
                  <tr key={b.ticker} className={cn("border-t border-border-soft", b.is_subject && "font-semibold")}>
                    <th scope="row" className="px-2 py-1 text-left font-normal">{b.name ?? b.ticker}</th>
                    <td className="num px-2 py-1 text-right">{num(b.value) !== null ? x1(b.value) : (b.reason ?? "—")}</td>
                    <td className="num px-2 py-1 text-right">{pct(b.growth)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
          dataset={`${title}; ${p.peer_count} peers with a value`}
          asOf={p.footer.as_of}
          source={`${p.footer.source}. ${p.footer.basis}`}
          learnHref={`/companies/${encodeURIComponent(ticker)}/peers`}
        />
      )}
      {p.withheld_reason ? <p className="text-xs text-text-muted">No median: {p.withheld_reason}.</p> : null}
      <Link href={`/companies/${encodeURIComponent(ticker)}/peers`} className="inline-block text-sm text-accent-ink hover:underline">
        Edit peers
      </Link>
    </Blueprint>
  );
}

/* ------------------------------------------------------------------ histogram (I7.2) */

function HistogramChart({ h }: { h: Hist }) {
  const bins = h.bins ?? [];
  const W = 640;
  const H = 180;
  const maxCount = Math.max(1, ...bins.map((b) => b.count));
  const lo = num(bins[0]?.lo) ?? 0;
  const hi = num(bins[bins.length - 1]?.hi) ?? 1;
  const x = linearScale([lo, hi], [8, W - 8]);
  const y = linearScale([0, maxCount], [H - 26, 10]);
  const mean = num(h.mean);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`${h.label} distribution across ${h.sector} in the covered universe`}>
      {bins.map((b, i) => {
        const l = num(b.lo) ?? 0;
        const r = num(b.hi) ?? 0;
        return (
          <g key={i}>
            <rect x={x(l) + 1} y={y(b.count)} width={Math.max(1, x(r) - x(l) - 2)} height={H - 26 - y(b.count)} rx={2} className={b.has_company ? "fill-accent" : "fill-accent-200"} />
            {b.count ? (
              <text x={(x(l) + x(r)) / 2} y={y(b.count) - 3} textAnchor="middle" fontSize={10} className={AXIS_TEXT}>
                {b.count}
              </text>
            ) : null}
            <text x={x(l)} y={H - 10} fontSize={10} textAnchor="middle" className={AXIS_TEXT}>
              {i === 0 && b.open_low ? "≤" : ""}
              {l.toFixed(0)}
            </text>
          </g>
        );
      })}
      <text x={x(hi)} y={H - 10} fontSize={10} textAnchor="middle" className={AXIS_TEXT}>
        {bins[bins.length - 1]?.open_high ? "≥" : ""}
        {hi.toFixed(0)}
      </text>
      {mean !== null && mean >= lo && mean <= hi ? (
        <g>
          <line x1={x(mean)} x2={x(mean)} y1={6} y2={H - 26} className="stroke-text" strokeDasharray="3 3" />
          <text x={x(mean) + 4} y={16} fontSize={11} className="fill-text">
            Average {mean.toFixed(1)}×
          </text>
        </g>
      ) : null}
    </svg>
  );
}

function HistogramPanel({ h }: { h: Hist }) {
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker={`${h.sector ?? "No sector"} · ${h.value_count} of ${h.universe_count} members with a value`} title={`${h.label ?? "Multiple"} across the sector`} />
      {!h.available ? (
        <Absent reason={h.reason} />
      ) : (
        <>
          <ChartWithFooter
            chart={<HistogramChart h={h} />}
            table={
              <table className="w-full text-sm">
                <caption className="sr-only">Members per {h.label} range</caption>
                <thead>
                  <tr className="bg-surface-muted text-xs text-text-muted">
                    <th scope="col" className="px-2 py-1.5 text-left font-medium">Range</th>
                    <th scope="col" className="px-2 py-1.5 text-right font-medium">Members</th>
                  </tr>
                </thead>
                <tbody>
                  {(h.bins ?? []).map((b, i) => (
                    <tr key={i} className={cn("border-t border-border-soft", b.has_company && "font-semibold")}>
                      <th scope="row" className="num px-2 py-1 text-left font-normal">
                        {b.open_low ? "up to " : ""}
                        {x1(b.lo)} – {x1(b.hi)}
                        {b.open_high ? " and above" : ""}
                        {b.has_company ? " (this company)" : ""}
                      </th>
                      <td className="num px-2 py-1 text-right">{b.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
            dataset={`${h.label} of ${h.value_count} ${h.sector} members`}
            asOf={h.footer.as_of}
            source={`${h.footer.source}. ${h.footer.basis}`}
          />
          <p className="text-sm">
            This company: <span className="num font-semibold">{x1(h.company_value)}</span>
            {num(h.company_percentile) !== null ? `, higher than ${Math.round(num(h.company_percentile)! * 100)}% of the sector members` : ""}. Median {x1(h.median)}.
          </p>
        </>
      )}
      <p className="text-xs text-text-muted">{h.note}</p>
    </Blueprint>
  );
}

/* ------------------------------------------------------------------ history (I7.3) */

function HistoryChart({ points, w }: { points: NonNullable<History["points"]>; w: NonNullable<History["windows"]>[number] }) {
  const pts = points.map((p) => ({ date: p.date, pe: num(p.pe) }));
  const valid = pts.filter((p): p is { date: string; pe: number } => p.pe !== null);
  if (valid.length < 2) return <ChartEmpty label="No meaningful P/E in this window" minHeight={160} />;
  const W = 640;
  const H = 200;
  const t = (d: string) => new Date(d).getTime();
  const lo = Math.min(...valid.map((p) => p.pe), num(w.low) ?? Infinity);
  const hi = Math.max(...valid.map((p) => p.pe), num(w.high) ?? -Infinity);
  const ticks = niceTicks(Math.max(0, lo * 0.9), hi * 1.05, 4);
  const x = linearScale([t(pts[0].date), t(pts[pts.length - 1].date)], [8, W - 56]);
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 22, 8]);
  const segments: [number, number][][] = [];
  let cur: [number, number][] = [];
  for (const p of pts) {
    if (p.pe === null) {
      if (cur.length) segments.push(cur);
      cur = [];
    } else cur.push([x(t(p.date)), y(p.pe)]);
  }
  if (cur.length) segments.push(cur);
  const med = num(w.median);
  const bl = num(w.low);
  const bh = num(w.high);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`P/E over ${w.years} years with the median and one standard deviation band`}>
      {bl !== null && bh !== null ? <rect x={8} width={W - 64} y={y(bh)} height={Math.max(0, y(bl) - y(bh))} className="fill-surface-muted" /> : null}
      {ticks.map((v) => (
        <g key={v}>
          <line x1={8} x2={W - 56} y1={y(v)} y2={y(v)} className="stroke-chart-grid" />
          <text x={W - 50} y={y(v) + 4} fontSize={11} className={AXIS_TEXT}>
            {v}×
          </text>
        </g>
      ))}
      {med !== null ? <line x1={8} x2={W - 56} y1={y(med)} y2={y(med)} className="stroke-text" strokeDasharray="4 3" /> : null}
      {segments.map((s, i) => (
        <path key={i} d={linePath(s)} fill="none" strokeWidth={2} className="stroke-chart-line" />
      ))}
      <text x={8} y={H - 4} fontSize={11} className={AXIS_TEXT}>
        {formatDate(pts[0].date)}
      </text>
      <text x={W - 56} y={H - 4} fontSize={11} textAnchor="end" className={AXIS_TEXT}>
        {formatDate(pts[pts.length - 1].date)}
      </text>
    </svg>
  );
}

function HistoryPanel({ h }: { h: History }) {
  const firstOk = (h.windows ?? []).find((w) => w.available)?.years ?? 5;
  const [years, setYears] = useState(firstOk);
  const w = (h.windows ?? []).find((x) => x.years === years) ?? (h.windows ?? [])[0];
  const start = new Date();
  start.setFullYear(start.getFullYear() - years);
  const inWindow = (h.points ?? []).filter((p) => new Date(p.date) >= start);
  const cur = num(h.current);
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading
        kicker={h.currency ? `Price in ${h.currency} over trailing diluted EPS` : "Price over trailing EPS"}
        title="P/E against its own history"
        action={
          <div role="group" aria-label="Window" className="flex border border-border text-xs">
            {(h.windows ?? []).map((x) => (
              <button key={x.years} type="button" aria-pressed={years === x.years} onClick={() => setYears(x.years)} className={cn("px-2.5 py-1", years === x.years ? "bg-accent-soft text-accent-ink" : "text-text-muted")}>
                {x.years}y
              </button>
            ))}
          </div>
        }
      />
      {!h.available ? (
        <Absent reason={h.reason} />
      ) : !w || !w.available ? (
        <Absent reason={w?.reason} />
      ) : (
        <>
          <ChartWithFooter
            chart={
              <>
                <HistoryChart points={inWindow} w={w} />
                <Legend
                  items={[
                    { label: "P/E", swatch: "line", className: "bg-chart-line" },
                    { label: "Median", swatch: "dashed", className: "border-text" },
                    { label: "±1 standard deviation", swatch: "square", className: "bg-surface-muted" },
                  ]}
                />
              </>
            }
            table={
              <table className="w-full text-sm">
                <caption className="sr-only">Weekly P/E, last {years} years</caption>
                <thead>
                  <tr className="bg-surface-muted text-xs text-text-muted">
                    {["Week", "Price", "EPS", "Fiscal year", "P/E"].map((c) => (
                      <th key={c} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...inWindow].reverse().map((p) => (
                    <tr key={p.date} className="border-t border-border-soft">
                      <th scope="row" className="px-2 py-1 text-left font-normal">{formatDate(p.date)}</th>
                      <td className="num px-2 py-1 text-right">{num(p.price)?.toFixed(2)}</td>
                      <td className="num px-2 py-1 text-right">{num(p.eps)?.toFixed(2) ?? "—"}</td>
                      <td className="num px-2 py-1 text-right">{p.eps_period_end ? formatDate(p.eps_period_end) : "—"}</td>
                      <td className="num px-2 py-1 text-right">{num(p.pe) !== null ? x1(p.pe) : "n/m"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            }
            dataset={`Weekly P/E over ${years} years (${w.points} weeks)`}
            asOf={h.footer.as_of}
            source={`${h.footer.source}. ${h.footer.basis}`}
          />
          <VerdictLine verdict={cur === null ? "unknown" : cur > (num(w.high) ?? Infinity) ? "caution" : cur < (num(w.low) ?? -Infinity) ? "good" : "neutral"}>
            Now <span className="num font-semibold">{x1(cur)}</span> against a {years}-year median of {x1(w.median)} (band {x1(w.low)} to {x1(w.high)}).
          </VerdictLine>
        </>
      )}
      <p className="text-xs text-text-muted">{h.basis}</p>
    </Blueprint>
  );
}

/* ------------------------------------------------------------------ fair multiple (I7.4) */

function FairPanel({ f }: { f: Fair }) {
  return (
    <Blueprint className="space-y-3 px-[22px] py-5">
      <PanelHeading kicker={`${f.label} · method ${f.method_version}`} title="Fair multiple from peers" />
      <p className="text-xs text-text-muted">{f.formula}.</p>
      {!f.available ? (
        <Absent reason={f.reason} />
      ) : (
        <ChartWithFooter
          chart={
            <div className="space-y-2 text-sm">
              <VerdictLine verdict="neutral">
                Peers with this company&apos;s growth ({pct(f.company_growth)}) and margin ({pct(f.company_margin)}) trade at about{" "}
                <span className="num font-semibold">{x1(f.fair_pe)}</span>; the company is at {x1(f.company_pe)}.
                {num(f.implied_price) !== null ? (
                  <>
                    {" "}At the fitted multiple the price would be <span className="num font-semibold">{num(f.implied_price)!.toFixed(2)} {f.currency ?? ""}</span>.
                  </>
                ) : null}
              </VerdictLine>
              <p className="text-xs text-text-muted">
                Fit over {f.n} peers, R² {num(f.r_squared)?.toFixed(2)} (shown from {num(f.min_r_squared)?.toFixed(2)}). P/E = {num(f.intercept)?.toFixed(1)} + {num(f.growth_coef)?.toFixed(1)} × growth + {num(f.margin_coef)?.toFixed(1)} × margin.
                An indication of how the market prices these features, not a value.
              </p>
            </div>
          }
          table={
            <table className="w-full text-sm">
              <caption className="sr-only">Peers in the regression</caption>
              <thead>
                <tr className="bg-surface-muted text-xs text-text-muted">
                  {["Peer", "Growth", "Margin", "P/E", "Fitted"].map((c) => (
                    <th key={c} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(f.points ?? []).map((p) => (
                  <tr key={p.ticker} className="border-t border-border-soft">
                    <th scope="row" className="px-2 py-1 text-left font-normal">{p.ticker}</th>
                    <td className="num px-2 py-1 text-right">{pct(p.growth)}</td>
                    <td className="num px-2 py-1 text-right">{pct(p.margin)}</td>
                    <td className="num px-2 py-1 text-right">{x1(p.pe)}</td>
                    <td className="num px-2 py-1 text-right">{x1(p.fitted)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
          dataset={`Regression over ${f.n} peers`}
          asOf={f.footer.as_of}
          source={`${f.footer.source}. ${f.footer.basis}`}
        />
      )}
    </Blueprint>
  );
}

export function ValuationPictures({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/valuation/pictures", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <section aria-label="Valuation pictures" className="flex flex-col gap-5">
      <ResourceGate state={state} label="the valuation pictures">
        {(p) => (
          <>
            <div className="grid gap-5 xl:grid-cols-2">
              <PeerPanel p={p.peers} ticker={ticker} />
              <HistogramPanel h={p.histogram} />
            </div>
            <div className="grid gap-5 xl:grid-cols-2">
              <HistoryPanel h={p.history} />
              <FairPanel f={p.fair_multiple} />
            </div>
          </>
        )}
      </ResourceGate>
    </section>
  );
}
