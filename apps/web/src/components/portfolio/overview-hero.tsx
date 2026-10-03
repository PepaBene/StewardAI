"use client";

import { LoaderCircleIcon, TrendingDownIcon, TrendingUpIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Legend, LineChart, type LegendEntry } from "@/components/charts";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { BlueprintCorners } from "@/components/ui/blueprint";
import type { PortfolioHistory } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { formatMoney } from "@/lib/format";
import { formatSignedPct } from "@/lib/portfolio/money";
import {
  HERO_RANGES,
  indexedRange,
  lastChange,
  periodReturn,
  rangeStart,
  type HeroRangeId,
} from "@/lib/portfolio/performance";

export type HistoryLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: PortfolioHistory };

/** Why a history window has no chart, in the user's words. */
export function historyGapMessage(gap: string | null): string {
  if (gap === "no portfolio snapshot yet") return "No portfolio snapshot yet. The first Trading212 sync creates one.";
  if (gap === "fewer than two snapshots in this window")
    return "Not enough sync history yet for a performance chart — check back after another sync or two.";
  return gap ?? "No performance history yet.";
}

/**
 * The Overview hero on the solid accent: total value, the latest day's flow-adjusted change,
 * time-weighted return YTD against the benchmark, and the indexed performance line for the
 * chosen range (one long history fetch, cut client-side).
 */
export function OverviewHero({
  total,
  currency,
  subline,
  history,
  range,
  onRange,
}: {
  total: number;
  currency: string;
  /** Muted line under the chips (e.g. what Trading212 itself reports). */
  subline?: ReactNode;
  history: HistoryLoad;
  range: HeroRangeId;
  onRange: (id: HeroRangeId) => void;
}) {
  const data = history.kind === "ok" ? history.data : null;
  const points = data?.points ?? [];
  const usable = !!data && !data.gap && points.length >= 2;
  const asOf = usable ? points[points.length - 1].date : null;
  const change = usable ? lastChange(points, data.values) : null;
  const ytdStart = asOf ? rangeStart("ytd", asOf) : null;
  const ytd = usable && asOf ? periodReturn(points, ytdStart) : null;
  const ytdBench = usable && asOf && data.benchmark.length ? indexedRange(points, data.benchmark, ytdStart).benchmarkReturn : null;
  const series = usable && asOf ? indexedRange(points, data.benchmark, rangeStart(range, asOf)) : null;
  const benchName = data?.benchmark_symbol ? `${data.benchmark_symbol} benchmark` : "Benchmark";
  const fmt = (v: number) => formatNumber(v, 1);
  const last = series?.portfolio.at(-1)?.value ?? null;
  const benchLast = series?.benchmark ? [...series.benchmark].reverse().find((p) => p.value !== null)?.value ?? null : null;
  const legend: LegendEntry[] = series
    ? [
        { label: "Portfolio", swatch: "line", className: "bg-accent-foreground", value: last === null ? undefined : fmt(last) },
        ...(series.benchmark
          ? [{ label: benchName, swatch: "dashed" as const, className: "border-accent-foreground/70", value: benchLast === null ? undefined : fmt(benchLast) }]
          : []),
      ]
    : [];
  const up = change ? change.pct >= 0 : true;
  const TrendIcon = up ? TrendingUpIcon : TrendingDownIcon;

  return (
    <section
      aria-labelledby="hero-total"
      className="blueprint flex min-w-0 flex-col gap-3.5 border-accent bg-accent px-5 pt-6 pb-5 text-accent-foreground sm:px-7"
    >
      <BlueprintCorners />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 id="hero-total" className="text-[11px] font-semibold tracking-[0.14em] uppercase">
            Total value · {currency}
          </h2>
          <p className="mt-1.5 font-heading text-[44px] leading-none font-semibold tracking-[-0.02em] sm:text-[68px]">
            {formatMoney(total, currency, { decimals: 0 })}
          </p>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4.5 gap-y-2 text-sm">
            {change ? (
              <span
                className="num inline-flex items-center gap-1.5 bg-accent-foreground/15 px-2.5 py-0.5"
                title={`Flow-adjusted change from the ${formatDay(change.since)} snapshot to ${formatDay(change.asOf)}: deposits and withdrawals excluded.`}
              >
                <TrendIcon aria-hidden="true" className="size-3.5" />
                {change.amount !== null ? `${formatMoney(change.amount, currency, { decimals: 0, signed: true })} ` : null}(
                {formatSignedPct(change.pct, 2)}) since {formatDay(change.since).replace(/ \d{4}$/, "")}
              </span>
            ) : null}
            {ytd !== null ? (
              <span className="num">
                TWR YTD <b>{formatSignedPct(ytd)}</b>
                {ytdBench !== null ? <> vs benchmark {formatSignedPct(ytdBench)}</> : null}
              </span>
            ) : null}
          </div>
          {subline ? <p className="num mt-1.5 text-xs">{subline}</p> : null}
        </div>
        <RangeButtons options={HERO_RANGES} value={range} onChange={onRange} label="Chart range" inverse />
      </div>

      {history.kind === "loading" ? (
        <p className="flex min-h-40 items-center gap-2 text-sm" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading performance…
        </p>
      ) : history.kind === "error" ? (
        <p className="min-h-40 text-sm">Performance could not be loaded: {history.message}</p>
      ) : !usable ? (
        <p className="min-h-40 text-sm">{historyGapMessage(data?.gap ?? null)}</p>
      ) : series && series.portfolio.length >= 2 ? (
        <>
          <LineChart
            data={series.portfolio}
            benchmark={series.benchmark ?? undefined}
            label="Portfolio"
            benchmarkLabel={benchName}
            variant="inverse"
            legend={false}
            width={848}
            height={270}
            formatValue={fmt}
            ariaLabel={`Portfolio${series.benchmark ? ` and ${benchName}` : ""}, indexed to 100 on ${formatDay(series.portfolio[0].date)}. Latest ${last === null ? "—" : fmt(last)}${benchLast !== null ? ` against ${fmt(benchLast)}` : ""}. Deposits and withdrawals are excluded, so this is investment return, not raw balance.`}
          />
          <div className="flex flex-wrap items-center gap-x-5.5 gap-y-1 text-xs">
            <Legend items={legend} className="text-xs text-accent-foreground/90" valueClassName="text-accent-foreground" />
            <span className="opacity-85 sm:ml-auto">Indexed to 100 · flow-adjusted, deposits excluded</span>
          </div>
          {!series.benchmark ? (
            <p className="text-xs">
              {data.benchmark_symbol
                ? `No ${data.benchmark_symbol} close near the start of this range, so the benchmark is not drawn.`
                : "No benchmark is configured (Config: Jobs, Prices)."}
            </p>
          ) : null}
        </>
      ) : (
        <p className="min-h-40 text-sm">Not enough history in this range yet — pick a longer one.</p>
      )}
    </section>
  );
}
