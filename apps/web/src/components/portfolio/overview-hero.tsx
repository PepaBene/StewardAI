"use client";

import { LoaderCircleIcon } from "lucide-react";

import { LineChart } from "@/components/charts";
import { ChartFrame } from "@/components/portfolio/chart-frame";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import type { PortfolioHistory } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { aheadShare } from "@/lib/charts/gap-runs";
import { formatDay } from "@/lib/charts/time";
import { HERO_RANGES, indexedRange, rangeStart, type HeroRangeId } from "@/lib/portfolio/performance";
import { indexName } from "@/lib/portfolio/ticker";

export type HistoryLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: PortfolioHistory };

/** Why a history window has no chart, in the user's words. */
export function historyGapMessage(gap: string | null): string {
  if (gap === "no portfolio snapshot yet") return "No portfolio snapshot yet. The first Trading212 sync creates one.";
  if (gap === "fewer than two snapshots in this window")
    return "Not enough sync history yet for a performance chart. Check back after another sync or two.";
  return gap ?? "No performance history yet.";
}

/**
 * The Overview hero: the flow-adjusted performance index against the benchmark for the chosen
 * range, in a framed card (one long history fetch, cut client-side). The headline figures live in
 * the answer strip above it.
 */
export function OverviewHero({
  history,
  range,
  onRange,
}: {
  history: HistoryLoad;
  range: HeroRangeId;
  onRange: (id: HeroRangeId) => void;
}) {
  const data = history.kind === "ok" ? history.data : null;
  const points = data?.points ?? [];
  const usable = !!data && !data.gap && points.length >= 2;
  const asOf = usable ? points[points.length - 1].date : null;
  const series = usable && asOf ? indexedRange(points, data.benchmark, rangeStart(range, asOf)) : null;
  const benchName = data?.benchmark_symbol ? indexName(data.benchmark_symbol) : "Benchmark";
  const fmt = (v: number) => formatNumber(v, 1);
  const last = series?.portfolio.at(-1)?.value ?? null;
  const benchLast = series?.benchmark ? [...series.benchmark].reverse().find((p) => p.value !== null)?.value ?? null : null;
  const benchByDate = new Map((series?.benchmark ?? []).map((b) => [b.date, b.value]));
  const share = series?.benchmark ? aheadShare(series.portfolio.map((p) => p.value), series.portfolio.map((p) => benchByDate.get(p.date) ?? null)) : null;
  const reading =
    share === null
      ? "Indexed to 100 at the start of the range; deposits and withdrawals are left out."
      : `Ahead of the benchmark on ${Math.round(share * 100)}% of days in this range. Indexed to 100; deposits and withdrawals are left out.`;

  let body;
  if (history.kind === "loading") {
    body = (
      <p className="flex min-h-40 items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading performance…
      </p>
    );
  } else if (history.kind === "error") {
    body = <p className="min-h-40 text-sm text-text-muted">Performance could not be loaded: {history.message}</p>;
  } else if (!usable) {
    body = <p className="min-h-40 text-sm text-text-muted">{historyGapMessage(data?.gap ?? null)}</p>;
  } else if (series && series.portfolio.length >= 2) {
    body = (
      <>
        <LineChart
          data={series.portfolio}
          benchmark={series.benchmark ?? undefined}
          label="Portfolio"
          benchmarkLabel={benchName}
          width={848}
          height={270}
          formatValue={fmt}
          gapShading
          ariaLabel={`Portfolio${series.benchmark ? ` and ${benchName}` : ""}, indexed to 100 on ${formatDay(series.portfolio[0].date)}. Latest ${last === null ? "n/a" : fmt(last)}${benchLast !== null ? ` against ${fmt(benchLast)}` : ""}. Deposits and withdrawals are excluded, so this is investment return, not raw balance.`}
        />
        {!series.benchmark ? (
          <p className="mt-2 text-xs text-text-muted">
            {data.benchmark_symbol
              ? `No ${indexName(data.benchmark_symbol)} close near the start of this range, so the benchmark is not drawn.`
              : "No benchmark is configured (Config: Jobs, Prices)."}
          </p>
        ) : null}
      </>
    );
  } else {
    body = <p className="min-h-40 text-sm text-text-muted">Not enough history in this range yet. Pick a longer one.</p>;
  }

  return (
    <ChartFrame
      title="Portfolio against the benchmark"
      reading={reading}
      controls={<RangeButtons options={HERO_RANGES} value={range} onChange={onRange} label="Chart range" />}
      dataset="Flow-adjusted performance index from daily position snapshots"
      asOf={asOf ? formatDay(asOf) : null}
      source="Trading212, stored daily closes"
      learnHref="/methods/time_weighted_return"
    >
      {body}
    </ChartFrame>
  );
}
