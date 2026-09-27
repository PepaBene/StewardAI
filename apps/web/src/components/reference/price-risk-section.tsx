"use client";

import {
  CircleCheckIcon,
  DownloadIcon,
  ChartLineIcon,
  LoaderCircleIcon,
  PencilIcon,
  RefreshCwIcon,
  TriangleAlertIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type PriceHistory } from "@/lib/api/client";
import { formatDate, formatDateTime, formatPrice, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  DEFAULT_RANGE,
  formatCoefficient,
  formatDifferencePct,
  formatIndex,
  formatReturn,
  formatRiskPercent,
  parsePlausibility,
  PRICE_RANGES,
  rangeDays,
  rebaseSeries,
  RETURN_PERIODS,
  sortCorrelations,
  type PriceRangeId,
} from "@/lib/reference/prices";
import { cn } from "@/lib/utils";

import { PriceChart } from "./price-chart";

/**
 * Holding page, price and risk (Phase 2b): Stooq closes against the configured benchmark,
 * the risk card exactly as the backend stored it, correlations with the other holdings and
 * the plausibility check against Trading212. A holding without history shows why.
 */
export function PriceRiskSection({ ticker, currency }: { ticker: string; currency?: string | null }) {
  const [range, setRange] = useState<PriceRangeId>(DEFAULT_RANGE);
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/prices", {
        params: { path: { ticker }, query: { days: rangeDays(range) } },
      }),
    [ticker, range],
  );
  const { state, reload } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <section aria-labelledby="prices-heading" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="prices-heading" className="text-[22px] leading-tight font-semibold">
          Price and risk
        </h2>
        <RangeSelector value={range} onChange={setRange} />
      </div>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading price history…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.status === 404 ? "No price history for this holding" : state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}
      {data ? <PriceContent data={data} ticker={ticker} currency={currency ?? null} onQueued={reload} /> : null}
    </section>
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

function PriceContent({
  data,
  ticker,
  currency,
  onQueued,
}: {
  data: PriceHistory;
  ticker: string;
  currency: string | null;
  onQueued: () => void;
}) {
  const plausibility = parsePlausibility(data.plausibility);
  if (data.gap || data.bars.length === 0) {
    return <PriceGap data={data} ticker={ticker} onQueued={onQueued} />;
  }
  const rebased = rebaseSeries(data.bars, data.benchmark);
  const holdingLabel = `${ticker} (${data.symbol ?? "no symbol"})`;
  const benchmarkLabel = rebased.withBenchmark && data.benchmark_symbol ? `Benchmark ${data.benchmark_symbol}` : null;
  const last = rebased.points.at(-1);
  const summary = `${holdingLabel} and ${benchmarkLabel ?? "no benchmark"}, rebased to 100 on ${
    rebased.base ? formatDate(rebased.base) : "the first date"
  }. Latest: ${formatIndex(last?.holding)}${benchmarkLabel ? ` against ${formatIndex(last?.benchmark)}` : ""}.`;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
      <figure className="space-y-2 rounded-lg border border-border bg-surface-raised p-4">
        <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-sm">
          <span className="font-medium">
            Rebased to 100 on <span className="num">{rebased.base ? formatDate(rebased.base) : "—"}</span>
            {benchmarkLabel ? " (first date both have a close)" : null}
          </span>
          <span className="num text-xs text-text-muted">
            Source: {data.source} · as of {data.as_of ? formatDate(data.as_of) : "—"}
            {data.fetched_at ? ` · fetched ${formatDateTime(data.fetched_at)}` : null}
          </span>
        </figcaption>
        <PriceChart points={rebased.points} holdingLabel={holdingLabel} benchmarkLabel={benchmarkLabel} label={summary} />
        <p className="num text-xs text-text-muted">{summary}</p>
        {!benchmarkLabel ? (
          <p className="text-xs text-text-muted">
            {data.benchmark_symbol
              ? `No ${data.benchmark_symbol} closes on the same days in this range, so the benchmark is not drawn.`
              : "No benchmark is configured (Config: Jobs, Prices)."}
          </p>
        ) : null}
      </figure>
      <div className="space-y-4">
        <RiskCard data={data} />
        {plausibility ? <PlausibilityCheck check={plausibility} currency={currency} /> : null}
      </div>
      <Correlations data={data} />
    </div>
  );
}

function PriceGap({ data, ticker, onQueued }: { data: PriceHistory; ticker: string; onQueued: () => void }) {
  return (
    <div className="space-y-3 rounded-lg border border-dashed border-border bg-surface-muted p-4 text-sm">
      <p className="flex items-center gap-2 font-medium">
        <ChartLineIcon aria-hidden="true" className="size-4 text-text-muted" />
        No price history to chart
      </p>
      <p className="text-text-muted">
        {data.gap ?? "Stooq returned no closes for this range."}
        {data.symbol ? (
          <>
            {" "}
            Stooq symbol: <span className="font-mono">{data.symbol}</span>.
          </>
        ) : null}
      </p>
      <p className="text-text-muted">
        If the symbol is wrong or missing, set the Stooq symbol in the holding&rsquo;s profile, then collect prices.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline" size="sm">
          <Link href={`/config/holding-profiles#${encodeURIComponent(ticker)}`}>
            <PencilIcon aria-hidden="true" />
            Set the Stooq symbol
          </Link>
        </Button>
        <CollectPricesButton onQueued={onQueued} />
      </div>
    </div>
  );
}

function CollectPricesButton({ onQueued }: { onQueued: () => void }) {
  const [status, setStatus] = useState<{ busy: boolean; text: string } | null>(null);
  async function run() {
    setStatus({ busy: true, text: "Queueing…" });
    try {
      const r = await getBrowserApiClient().POST("/jobs/price-history");
      setStatus({
        busy: false,
        text: r.data
          ? `Queued as job #${r.data.id}. Prices for every holding are collected; reload in a minute.`
          : `Not queued (HTTP ${r.response.status}).`,
      });
      if (r.data) setTimeout(onQueued, 60_000);
    } catch (err) {
      setStatus({ busy: false, text: err instanceof Error ? err.message : String(err) });
    }
  }
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => void run()} disabled={status?.busy}>
        <DownloadIcon aria-hidden="true" />
        Collect prices now
      </Button>
      <p role="status" className="basis-full text-xs text-text-muted">
        {status?.text}
      </p>
    </>
  );
}

/** Figures exactly as stored by the backend; the full value is in each figure's title. */
function Figure({ value, text, tone }: { value: number | null | undefined; text: string; tone?: boolean }) {
  const t = tone ? gainLossTone(value) : "flat";
  return (
    <dd
      className={cn("num text-right font-medium", t === "gain" && "text-gain", t === "loss" && "text-loss")}
      title={value === null || value === undefined ? "No figure" : `Stored value: ${value}`}
    >
      {text}
    </dd>
  );
}

function RiskCard({ data }: { data: PriceHistory }) {
  const risk = data.risk;
  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface-raised p-4">
      <div>
        <h3 className="font-medium">Risk</h3>
        <p className="text-xs text-text-muted">
          Computed by the backend from the stored daily closes, as of{" "}
          <span className="num">{data.as_of ? formatDate(data.as_of) : "—"}</span>; the range above does not change them.
        </p>
      </div>
      {risk ? (
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
          {RETURN_PERIODS.map((p) => (
            <div key={p.key} className="contents">
              <dt className="text-text-muted">Return, {p.label}</dt>
              <Figure value={risk.returns[p.key]} text={formatReturn(risk.returns[p.key])} tone />
            </div>
          ))}
          <dt className="mt-2 text-text-muted">Volatility (annualised, 252 days)</dt>
          <Figure value={risk.volatility} text={formatRiskPercent(risk.volatility)} />
          <dt className="text-text-muted">Maximum drawdown</dt>
          <Figure value={risk.max_drawdown} text={formatRiskPercent(risk.max_drawdown)} />
          <dt className="text-text-muted">
            Beta vs <span className="font-mono">{data.benchmark_symbol ?? "no benchmark"}</span> (252 days)
          </dt>
          <Figure value={risk.beta} text={formatCoefficient(risk.beta)} />
          <dt className="text-text-muted">Observations (daily closes)</dt>
          <dd className="num text-right font-medium">{risk.observations}</dd>
        </dl>
      ) : (
        <p className="text-sm text-text-muted">No risk figures yet: too few stored closes.</p>
      )}
      <p className="text-xs text-text-muted">A dash means too little history for that figure.</p>
    </div>
  );
}

function PlausibilityCheck({
  check,
  currency,
}: {
  check: NonNullable<ReturnType<typeof parsePlausibility>>;
  currency: string | null;
}) {
  const Icon = check.flagged ? TriangleAlertIcon : CircleCheckIcon;
  const money = (v: number) => (currency ? formatPrice(v, currency) : String(v));
  return (
    <div
      className={cn(
        "space-y-1 rounded-lg border p-4 text-sm",
        check.flagged ? "border-warning-signal/40 bg-warning-signal-subtle" : "border-border bg-surface-raised",
      )}
    >
      <h3 className="flex items-center gap-1.5 font-medium">
        <Icon aria-hidden="true" className={cn("size-4", check.flagged ? "text-warning-signal" : "text-neutral-signal")} />
        Plausibility vs Trading212: {check.flagged ? "flagged" : "within tolerance"}
      </h3>
      <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-0.5">
        <dt className="text-text-muted">Sync day</dt>
        <dd className="num text-right">{formatDate(check.date)}</dd>
        <dt className="text-text-muted">Stooq close</dt>
        <dd className="num text-right">{money(check.stooqClose)}</dd>
        <dt className="text-text-muted">Trading212 price</dt>
        <dd className="num text-right">{money(check.trading212Price)}</dd>
        <dt className="text-text-muted">Difference</dt>
        <dd className="num text-right font-medium">{formatDifferencePct(check.differencePct)}</dd>
      </dl>
      {check.flagged ? (
        <p className="text-xs text-text-muted">
          More than the tolerance set in Config: Jobs (Prices). Check the Stooq symbol, or whether Trading212&rsquo;s
          price was taken before the close.
        </p>
      ) : null}
    </div>
  );
}

function Correlations({ data }: { data: PriceHistory }) {
  const rows = sortCorrelations(data.correlations);
  return (
    <div className="space-y-2 rounded-lg border border-border bg-surface-raised p-4 lg:col-span-2">
      <div>
        <h3 className="font-medium">Correlation with the other holdings</h3>
        <p className="text-xs text-text-muted">
          Pearson correlation of daily returns on shared trading days, last 252 days, computed by the backend.
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">No other holding has price history yet.</p>
      ) : (
        <table className="w-full max-w-md text-sm">
          <thead>
            <tr className="border-b border-border text-text-muted">
              <th scope="col" className="py-1 text-left font-medium">Holding</th>
              <th scope="col" className="py-1 text-right font-medium">Correlation</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ticker} className="border-b border-border last:border-0">
                <th scope="row" className="py-1 text-left font-normal">
                  <Link href={`/holdings/${encodeURIComponent(r.ticker)}`} className="font-mono hover:text-accent hover:underline">
                    {r.ticker}
                  </Link>
                </th>
                <td
                  className="num py-1 text-right"
                  title={r.correlation === null ? "Too few shared days" : `Stored value: ${r.correlation}`}
                >
                  {formatCoefficient(r.correlation)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
