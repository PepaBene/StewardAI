"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PriceChart } from "@/components/reference/price-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBrowserApiClient, type PortfolioHistory } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  DEFAULT_RANGE,
  formatIndex,
  PRICE_RANGES,
  rangeDays,
  rebaseSeries,
  type PriceRangeId,
} from "@/lib/reference/prices";
import { cn } from "@/lib/utils";

/**
 * Portfolio value vs. benchmark, indexed to 100 (visualization A of the redesign,
 * extended from the per-holding chart to Overview). The index is flow-adjusted server
 * side (GET /portfolio/history): a deposit or withdrawal never reads as a fake gain or
 * loss, only investment return does. Reuses PriceChart and rebaseSeries unchanged.
 */
export function PerformanceCard() {
  const [range, setRange] = useState<PriceRangeId>(DEFAULT_RANGE);
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: rangeDays(range) } } }),
    [range],
  );
  const { state, reload } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
        <div>
          <CardTitle>Performance</CardTitle>
          <CardDescription>Portfolio value vs. benchmark, indexed to 100.</CardDescription>
        </div>
        <RangeSelector value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {state.kind === "loading" ? (
          <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading performance…
          </p>
        ) : null}
        {state.kind === "error" ? (
          <AlertMessage title={state.title}>
            <p>{state.detail}</p>
            <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
              <RefreshCwIcon aria-hidden="true" />
              Try again
            </Button>
          </AlertMessage>
        ) : null}
        {data ? <PerformanceContent data={data} /> : null}
      </CardContent>
    </Card>
  );
}

function RangeSelector({ value, onChange }: { value: PriceRangeId; onChange: (id: PriceRangeId) => void }) {
  return (
    <div role="group" aria-label="Chart range" className="inline-flex rounded-md border border-border bg-surface-raised p-0.5">
      {PRICE_RANGES.map((r) => (
        <button
          key={r.id}
          type="button"
          aria-pressed={value === r.id}
          onClick={() => onChange(r.id)}
          className={cn(
            "rounded-sm px-2.5 py-1 text-xs font-medium",
            value === r.id ? "bg-accent text-accent-foreground" : "text-text-muted hover:bg-surface-muted hover:text-text",
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function PerformanceContent({ data }: { data: PortfolioHistory }) {
  if (data.gap || data.points.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        {data.gap === "no portfolio snapshot yet"
          ? "No portfolio snapshot yet. The first Trading212 sync creates one."
          : data.gap === "fewer than two snapshots in this window"
            ? "Not enough sync history yet for a performance chart — check back after another sync or two."
            : (data.gap ?? "No performance history for this range.")}
      </p>
    );
  }
  const rebased = rebaseSeries(data.points, data.benchmark);
  const benchmarkLabel = rebased.withBenchmark && data.benchmark_symbol ? `Benchmark ${data.benchmark_symbol}` : null;
  const last = rebased.points.at(-1);
  const summary = `Portfolio and ${benchmarkLabel ?? "no benchmark"}, rebased to 100 on ${
    rebased.base ? formatDate(rebased.base) : "the first date"
  }. Latest: ${formatIndex(last?.holding)}${benchmarkLabel ? ` against ${formatIndex(last?.benchmark)}` : ""}. Deposits and withdrawals are excluded, so this is investment return, not raw balance.`;

  return (
    <div className="space-y-2">
      <PriceChart points={rebased.points} holdingLabel="Portfolio" benchmarkLabel={benchmarkLabel} label={summary} />
      <p className="num text-xs text-text-muted">{summary}</p>
      {!benchmarkLabel ? (
        <p className="text-xs text-text-muted">
          {data.benchmark_symbol
            ? `No ${data.benchmark_symbol} closes on the same days in this range, so the benchmark is not drawn.`
            : "No benchmark is configured (Config: Jobs, Prices)."}
        </p>
      ) : null}
    </div>
  );
}
