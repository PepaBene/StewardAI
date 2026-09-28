"use client";

import { ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useState, type ReactNode } from "react";

import { LineChart } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type FundValuation, type HoldingValuation, type ValuationRatio } from "@/lib/api/client";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { jobHref } from "@/lib/jobs/job-detail";
import {
  figureFlags,
  formatMultiple,
  formatPerShare,
  formatYield,
  periodLabel,
  ratioText,
  sourceLine,
} from "@/lib/portfolio/valuation";
import { cn } from "@/lib/utils";

const FLAG_CLASS: Record<string, string> = {
  annual: "border border-border text-text-muted",
  stale: "bg-warning-signal-subtle text-warning-signal",
  "entered manually": "border border-border text-text-muted",
  forecast: "border border-border text-text-muted",
};

function Flag({ children }: { children: string }) {
  return (
    <span className={cn("px-1.5 py-px font-mono text-[10px] tracking-wide uppercase", FLAG_CLASS[children] ?? FLAG_CLASS.annual)}>
      {children}
    </span>
  );
}

function Stat({ label, value, sub, title }: { label: string; value: string; sub?: ReactNode; title?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="text-[11px] text-text-muted">{label}</dt>
      <dd className="num font-heading text-[28px] leading-[1.1] font-semibold">{value}</dd>
      {sub ? <dd className="text-[11px] leading-snug text-text-faint">{sub}</dd> : null}
    </div>
  );
}

function reasonOf(ratio: ValuationRatio): string | undefined {
  return ratio.value === null || ratio.value === undefined ? (ratio.reason ?? undefined) : undefined;
}

function SourceLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  if (!href) return <>{children}</>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-ink hover:underline">
      {children}
      <ExternalLinkIcon aria-hidden="true" className="size-3" />
    </a>
  );
}

/** Fetches GET /holdings/{ticker}/valuation. */
export function useHoldingValuation(ticker: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/valuation", { params: { path: { ticker } } }),
    [ticker],
  );
  return useApiResource(fetcher);
}

/**
 * Holding detail: P/E, P/B and dividend yield from the latest stored close and the stored
 * fundamentals (SEC XBRL, ESEF, or figures entered manually in the holding profile), each
 * with its period, basis, source and staleness; the P/E over two years when the EPS history
 * allows. An ETF shows its provider's fund-level figures.
 */
export function ValuationPanel({ ticker }: { ticker: string }) {
  const { state } = useHoldingValuation(ticker);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Blueprint aria-labelledby="valuation-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Valuation"
        title={<span id="valuation-heading">{data?.kind === "fund" ? "Fund P/E, P/B and yield" : "P/E, P/B and dividend yield"}</span>}
        action={<RefreshControl ticker={ticker} />}
      />
      {data?.price ? (
        <p className="-mt-1 text-[11px] text-text-faint">
          Close {data.price.currency ?? ""} {data.price.close.toLocaleString("en-GB", { maximumFractionDigits: 2 })} on{" "}
          {formatDate(data.price.date)} ({data.price.source}){data.price.stale ? " · stale" : ""}
          {data.checked_at ? <> · fundamentals checked {formatDateTime(data.checked_at)}</> : null}
        </p>
      ) : null}
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading valuation…
        </p>
      ) : null}
      {state.kind === "error" && !data ? <p className="text-sm text-text-muted">Could not load the valuation: {state.title}</p> : null}
      {data ? data.kind === "fund" ? <FundBody data={data} /> : <StockBody data={data} /> : null}
    </Blueprint>
  );
}

/** Queues a forced refresh of this holding's fundamentals (POST /jobs/valuation-refresh). */
function RefreshControl({ ticker }: { ticker: string }) {
  const [pending, setPending] = useState(false);
  const [queued, setQueued] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run() {
    setPending(true);
    setError(null);
    try {
      const r = await getBrowserApiClient().POST("/jobs/valuation-refresh", { body: { ticker, force: true } });
      if (r.data) setQueued(r.data.id);
      else setError(`Not queued (HTTP ${r.response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="ghost" size="sm" onClick={() => void run()} disabled={pending}>
        {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
        Refresh
      </Button>
      {queued !== null ? (
        <Link href={jobHref(queued)} className="text-[11px] text-accent-ink hover:underline">
          Queued as job #{queued}
        </Link>
      ) : null}
      {error ? (
        <p role="alert" className="text-[11px] text-text">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function StockBody({ data }: { data: HoldingValuation }) {
  const eps = data.eps_ttm;
  const manual = data.fundamentals?.source === "manual";
  const flags = figureFlags(eps);
  const nothing = !data.fundamentals && data.pe.value === null && data.pb.value === null;
  const history = data.pe_history.map((p) => ({ date: p.date, value: p.pe }));
  const errors = Object.entries(data.errors ?? {});

  return (
    <div className={cn("grid grid-cols-1 gap-5", !manual && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]")}>
      <div className="flex min-w-0 flex-col gap-3">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Stat label="P/E (TTM)" value={ratioText(data.pe)} sub={reasonOf(data.pe)} />
          <Stat label="P/B" value={ratioText(data.pb)} sub={reasonOf(data.pb)} />
          <Stat label="Dividend yield" value={ratioText(data.dividend_yield, "yield")} sub={reasonOf(data.dividend_yield)} />
          <Stat
            label={`EPS ${data.fundamentals?.eps_kind === "basic" ? "basic" : "diluted"}`}
            value={eps ? formatPerShare(eps) : "—"}
            sub={periodLabel(eps) ?? undefined}
            title={eps?.method}
          />
        </dl>
        {nothing ? (
          <p className="text-sm text-text-muted">
            No fundamentals for this holding yet
            {data.pe.reason ? <>: {data.pe.reason}</> : null}.
          </p>
        ) : null}
        {data.fundamentals ? (
          <div className="space-y-1.5 border-t border-border pt-3 text-[12px] text-text-muted">
            <p className="flex flex-wrap items-center gap-1.5">
              <span>
                <SourceLink href={data.fundamentals.document_url}>{sourceLine(data)}</SourceLink>
              </span>
              {flags.map((f) => (
                <Flag key={f}>{f}</Flag>
              ))}
            </p>
            {eps && !manual ? (
              <p>
                EPS {eps.method}
                {data.shares_per_listing !== 1 ? <> · {data.shares_per_listing} shares per listed unit</> : null}
              </p>
            ) : null}
            {data.book_value_per_share ? (
              <p>
                Book value {formatPerShare(data.book_value_per_share)} per share
                {manual ? null : <> ({data.book_value_per_share.method})</>}
              </p>
            ) : null}
            {data.dividend_per_share ? (
              <p>
                Dividends {formatPerShare(data.dividend_per_share)} per share ({periodLabel(data.dividend_per_share)})
              </p>
            ) : null}
            {data.fundamentals.fetched_at ? <p>Read {formatDateTime(data.fundamentals.fetched_at)}</p> : null}
            {data.fundamentals.as_of ? <p>Entered for {formatDate(data.fundamentals.as_of)} in the holding profile</p> : null}
            {data.fundamentals.notes.map((n) => (
              <p key={n} className="text-text-faint">
                {manual ? <>Source: {n}</> : n}
              </p>
            ))}
          </div>
        ) : null}
        {data.alternatives.length > 0 ? (
          <div className="border-t border-border pt-3">
            <p className="text-[11px] tracking-wide text-text-muted uppercase">Other sources and bases (not used)</p>
            <ul className="mt-1 space-y-0.5 text-[12px]">
              {data.alternatives.map((a) => (
                <li key={`${a.source}-${a.basis}`} className="flex flex-wrap gap-x-2">
                  <span className="text-text-muted">
                    {a.source_label}
                    {a.basis ? ` · ${a.basis}` : ""}
                  </span>
                  <span className="num">
                    EPS {a.eps_ttm !== null && a.eps_ttm !== undefined ? `${a.currency} ${a.eps_ttm.toFixed(2)}` : "—"}
                    {a.period_end ? ` to ${formatDate(a.period_end)}` : ""} → P/E {formatMultiple(a.pe)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {data.cross_check ? (
          <p className="border-t border-border pt-3 text-[12px] text-text-muted">
            Cross-check, {data.cross_check.source_label}: P/E {formatMultiple(data.cross_check.pe)} · P/B {formatMultiple(data.cross_check.pb)}
            {data.cross_check.eps_ttm !== null && data.cross_check.eps_ttm !== undefined ? ` · EPS ${data.cross_check.eps_ttm.toFixed(2)}` : ""} · read{" "}
            {formatDateTime(data.cross_check.fetched_at)}
          </p>
        ) : null}
        {errors.length > 0 ? (
          <p className="text-[12px] text-warning-signal">
            Last refresh problems: {errors.map(([k, v]) => `${k.toUpperCase()}: ${v}`).join("; ")}
          </p>
        ) : null}
      </div>
      {manual ? null : (
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-[11px] tracking-wide text-text-muted uppercase">
          {history.length > 1 && historySpanDays(history) < 600 ? `P/E since ${formatDate(history[0].date)}` : "P/E over two years"}
        </p>
        {history.length >= 4 ? (
          <LineChart
            data={history}
            label="P/E"
            height={180}
            width={560}
            formatValue={(v) => formatMultiple(v)}
            ariaLabel={`P/E by week, from ${formatMultiple(history[0].value)} to ${formatMultiple(history[history.length - 1].value)}`}
          />
        ) : (
          <div className="flex min-h-[140px] items-center justify-center border border-dashed border-border px-4 text-center text-[12px] text-text-faint">
            Not enough EPS history to chart the P/E.
          </div>
        )}
        <p className="text-[11px] leading-snug text-text-faint">
          Each week&rsquo;s close over the EPS TTM in force then (a step on each report&rsquo;s filing date). {data.method}
        </p>
      </div>
      )}
    </div>
  );
}

function FundBody({ data }: { data: HoldingValuation }) {
  const fund: FundValuation | null | undefined = data.fund;
  if (!fund) {
    return <p className="text-sm text-text-muted">No fund figures: {data.pe.reason ?? "not fetched yet"}.</p>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        <Stat label="Fund P/E" value={formatMultiple(fund.pe)} />
        <Stat label="Fund P/B" value={formatMultiple(fund.pb)} />
        <Stat
          label={fund.dividend_yield_kind === "forecast" ? "Dividend yield (forecast)" : "Dividend yield"}
          value={formatYield(fund.dividend_yield)}
          sub={fund.dividend_yield === null || fund.dividend_yield === undefined ? `${fund.provider_label} publishes none` : undefined}
        />
        {fund.index_pe !== null && fund.index_pe !== undefined ? (
          <Stat label={`${fund.index_label ?? "Index"} P/E`} value={formatMultiple(fund.index_pe)} sub={`P/B ${formatMultiple(fund.index_pb)}`} />
        ) : null}
      </dl>
      <p className="flex flex-wrap items-center gap-1.5 border-t border-border pt-3 text-[12px] text-text-muted">
        <SourceLink href={fund.source_url.includes("graphql") ? null : fund.source_url}>
          {fund.provider_label}, as of {formatDate(fund.as_of)}
        </SourceLink>
        {fund.stale ? <Flag>stale</Flag> : null}
      </p>
      {fund.methodology ? (
        <details className="text-[12px] text-text-muted">
          <summary className="cursor-pointer">Provider&rsquo;s methodology</summary>
          <p className="mt-1 leading-snug">{fund.methodology}</p>
        </details>
      ) : null}
    </div>
  );
}

function historySpanDays(points: readonly { date: string }[]): number {
  if (points.length < 2) return 0;
  return (Date.parse(points[points.length - 1].date) - Date.parse(points[0].date)) / 86_400_000;
}
