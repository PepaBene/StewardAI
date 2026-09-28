"use client";

import { ArrowRightIcon, ExternalLinkIcon, FlaskConicalIcon, MessageSquareIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { BarChart, CandleChart, DrawdownChart, HBar, Radar, Ring } from "@/components/charts";
import { openChatPanel } from "@/components/chat/chat-panel";
import { BandBadge } from "@/components/portfolio/band-badge";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import type {
  BoardRow,
  Holding,
  HoldingDetail,
  HoldingIdentity,
  HoldingOwnership,
  KpiStatus,
  PositionDrift,
  PriceHistory,
} from "@/lib/api/client";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { formatObserved, formatTarget, humanizeMetric } from "@/lib/evidence/confidence";
import { formatDate, formatMoney, formatPercent, formatPrice, formatQuantity, gainLossTone } from "@/lib/format";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { kpiAnchorId } from "@/lib/portfolio/kpi-anchor";
import { displayTicker } from "@/lib/portfolio/holdings-view";
import {
  annualisedVolatility,
  barsInRange,
  buildTimeline,
  CANDLE_RANGES,
  candleRangeDays,
  DEFAULT_CANDLE_RANGE,
  exchangeName,
  fromYearHigh,
  historicalVar,
  holderBars,
  kpiBar,
  lastCloseChange,
  RADAR_NOTE,
  reportCountdown,
  scoreChange,
  shortPeriod,
  THESIS_STATE_LABEL,
  thesisRadar,
  yearMaxDrawdown,
  type CandleRangeId,
  type TimelineEntry,
} from "@/lib/reference/holding-detail";
import { isExternalUrl } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

const TONE_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" };

/** The data a resource currently has: the latest result, or the previous one next to an error. */
export function current<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

function Panel({
  kicker,
  title,
  action,
  children,
  className,
  id,
}: {
  kicker: ReactNode;
  title: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  id: string;
}) {
  return (
    <Blueprint aria-labelledby={id} className={cn("flex min-w-0 flex-col gap-3 px-[22px] py-5", className)}>
      <PanelHeading kicker={kicker} title={<span id={id}>{title}</span>} action={action} />
      {children}
    </Blueprint>
  );
}

function Signed({ value, children }: { value: number | null | undefined; children: ReactNode }) {
  const tone = gainLossTone(value);
  return (
    <span className={cn("num", TONE_CLASS[tone])}>
      {tone === "gain" ? "▲ " : tone === "loss" ? "▼ " : ""}
      {children}
    </span>
  );
}

// ----------------------------------------------------------------------------- header

const STATE_TAG = {
  intact: "bg-verified-subtle text-verified",
  watch: "bg-warning-signal-subtle text-warning-signal",
  at_risk: "bg-loss-subtle text-loss",
  unjudged: "border border-border text-text-muted",
} as const;

export function HoldingHeader({
  detail,
  identity,
  classes,
  row,
  bars,
}: {
  detail: HoldingDetail;
  identity: HoldingIdentity | null;
  classes: Record<string, string> | null;
  row: BoardRow | null;
  bars: PriceHistory["bars"];
}) {
  const name = detail.instrument?.name ?? detail.profile?.company_name ?? detail.ticker;
  const short = displayTicker(detail.ticker);
  const currency = detail.position?.instrument_currency ?? detail.instrument?.currency ?? null;
  const exchange = exchangeName(identity?.home_mic);
  const move = lastCloseChange(bars);
  const sector = classes?.sector;
  const region = classes?.geography ?? classes?.region;

  function startResearch() {
    const target = document.getElementById("research");
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
    target?.querySelector<HTMLElement>("select, input, button")?.focus({ preventScroll: true });
  }

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="Breadcrumb" className="text-[13px] text-text-muted">
          <ol className="flex items-center gap-1.5">
            <li>
              <Link href="/holdings" className="text-accent-ink hover:underline">
                Holdings
              </Link>
            </li>
            <li aria-hidden="true">›</li>
            <li aria-current="page" className="font-mono">
              {short}
            </li>
          </ol>
        </nav>
        {detail.freshness ? (
          <FreshnessLabel syncedAt={detail.freshness.synced_at} snapshotDate={detail.freshness.snapshot_date} />
        ) : null}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div className="flex min-w-0 items-center gap-4">
          <div
            aria-hidden="true"
            className="grid size-16 shrink-0 place-items-center bg-accent font-mono text-[15px] font-medium text-accent-foreground"
          >
            {short.slice(0, 5)}
          </div>
          <div className="min-w-0">
            <h1 className="page-title mb-2">{name}</h1>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="mr-1.5 font-mono text-xs text-text-muted" title={detail.ticker}>
                {[short, exchange, currency].filter(Boolean).join(" · ")}
              </span>
              {sector ? <span className="bg-accent-soft px-2 py-[3px] text-accent-ink">{sector}</span> : null}
              {region ? <span className="bg-border-soft px-2 py-[3px]">{region}</span> : null}
              {row ? (
                <Link href="/theses" className={cn("px-2 py-[3px] font-semibold hover:underline", STATE_TAG[row.state])}>
                  Thesis · {THESIS_STATE_LABEL[row.state]}
                </Link>
              ) : null}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-5">
          {move && currency ? (
            <div className="text-right">
              <div className="num font-heading text-[44px] leading-none font-semibold">{formatPrice(move.close, currency)}</div>
              <div className="text-sm">
                <Signed value={move.change}>
                  {formatSigned(move.change, 2)} ({formatPercent(move.pct, { decimals: 2, signed: true })})
                </Signed>
                <span className="text-text-muted"> · close {formatDate(move.date)}</span>
              </div>
            </div>
          ) : detail.position ? (
            <div className="text-right">
              <div className="num font-heading text-[44px] leading-none font-semibold">
                {formatPrice(detail.position.current_price, detail.position.instrument_currency)}
              </div>
              <div className="text-sm text-text-muted">Trading212 price at the last sync</div>
            </div>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => openChatPanel(`About ${name} (${detail.ticker}): `)}>
              <MessageSquareIcon aria-hidden="true" />
              Ask about {short}
            </Button>
            <Button onClick={startResearch}>
              <FlaskConicalIcon aria-hidden="true" />
              Start research
            </Button>
          </div>
        </div>
      </div>
    </>
  );
}

// ----------------------------------------------------------------------------- candles

function CandleRangeSwitch({ value, onChange }: { value: CandleRangeId; onChange: (id: CandleRangeId) => void }) {
  return (
    <div role="group" aria-label="Chart range" className="inline-flex border border-border text-xs font-semibold">
      {CANDLE_RANGES.map((r, i) => (
        <button
          key={r.id}
          type="button"
          aria-pressed={value === r.id}
          onClick={() => onChange(r.id)}
          className={cn(
            "px-2.5 py-1.5",
            i > 0 && "border-l border-border",
            value === r.id ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft",
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function Readout({ bar }: { bar: PriceHistory["bars"][number] | undefined }) {
  if (!bar) return null;
  const item = (k: string, v: number | null | undefined, digits = 2) => (
    <span>
      {k} <b className="font-medium text-text">{typeof v === "number" ? formatNumber(v, digits) : "—"}</b>
    </span>
  );
  return (
    <div className="num flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-text-muted" aria-label={`Last session, ${formatDate(bar.date)}`}>
      {item("O", bar.open)}
      {item("H", bar.high)}
      {item("L", bar.low)}
      {item("C", bar.close)}
      <span>
        Vol <b className="font-medium text-text">{typeof bar.volume === "number" ? formatCompact(bar.volume) : "—"}</b>
      </span>
      <span className="text-text-faint">{formatDate(bar.date)}</span>
    </div>
  );
}

function FiveYearBars({ ticker, children }: { ticker: string; children: (bars: PriceHistory["bars"] | null, loading: boolean) => ReactNode }) {
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/prices", {
        params: { path: { ticker }, query: { days: candleRangeDays("5y") } },
      }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  return <>{children(current(state)?.bars ?? null, state.kind === "loading")}</>;
}

export function CandlePanel({
  ticker,
  history,
  loading,
}: {
  ticker: string;
  history: PriceHistory | null;
  loading: boolean;
}) {
  const [range, setRange] = useState<CandleRangeId>(DEFAULT_CANDLE_RANGE);
  const render = (all: PriceHistory["bars"] | null, busy: boolean) => {
    const bars = all ? barsInRange(all, candleRangeDays(range)) : [];
    return (
      <>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Readout bar={bars[bars.length - 1]} />
          <CandleRangeSwitch value={range} onChange={setRange} />
        </div>
        {busy && bars.length === 0 ? (
          <p className="text-sm text-text-muted" role="status">
            Loading daily bars…
          </p>
        ) : bars.length > 1 ? (
          <CandleChart
            bars={bars}
            label={displayTicker(ticker)}
            maWindow={20}
            width={880}
            height={400}
            volumeHeight={64}
          />
        ) : (
          <div className="border border-dashed border-border p-4 text-sm text-text-muted">
            {history?.gap ?? "No daily bars are stored for this range."}{" "}
            <a href="#prices-heading" className="text-accent-ink hover:underline">
              Price &amp; risk details
            </a>{" "}
            has the symbol and collect-prices actions.
          </div>
        )}
        <p className="text-xs text-text-muted">
          Daily bars from {history?.source ?? "Stooq"}
          {history?.symbol ? (
            <>
              {" "}
              (<span className="font-mono">{history.symbol}</span>)
            </>
          ) : null}
          ; no intraday data. MA 20 is the 20-session average close, drawn once 20 sessions are in view.
        </p>
      </>
    );
  };
  return (
    <Blueprint aria-label={`${displayTicker(ticker)} price chart`} className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      {range === "5y" ? <FiveYearBars ticker={ticker}>{render}</FiveYearBars> : render(history?.bars ?? null, loading)}
    </Blueprint>
  );
}

// ----------------------------------------------------------------------------- position

export function PositionPanel({
  position,
  base,
  drift,
}: {
  position: Holding | null;
  base: string;
  drift: PositionDrift | null;
}) {
  if (!position) {
    return (
      <Panel id="position-heading" kicker="Your position" title="Not held">
        <p className="text-sm text-text-muted">The latest Trading212 sync has no open position in this instrument.</p>
      </Panel>
    );
  }
  const band: [number, number] | null =
    drift && typeof drift.lower === "number" && typeof drift.upper === "number" ? [drift.lower, drift.upper] : null;
  const top = Math.max(position.weight ?? 0, position.target_weight ?? 0, band?.[1] ?? 0, drift?.hard_limit ?? 0);
  const scale = Math.max(0.05, Math.ceil((top * 1.25 * 100) / 5 - 1e-9) * 0.05);
  return (
    <Blueprint aria-labelledby="position-heading" className="flex flex-col gap-3 px-[22px] py-5">
      <div id="position-heading" className="kicker">
        Your position
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="num font-heading text-[34px] leading-none font-semibold">
            {formatMoney(position.value_base, base, { decimals: 0 })}
          </div>
          <div className="text-[13px]">
            <Signed value={position.ppl}>
              {formatMoney(position.ppl, position.account_currency, { decimals: 0, signed: true })} (
              {formatPercent(position.ppl_pct, { signed: true })})
            </Signed>{" "}
            <span className="text-text-muted">on cost</span>
          </div>
          {position.account_currency !== base || position.instrument_currency !== base ? (
            <div className="num text-xs text-text-muted" title="Trading212's own conversion">
              T212 {formatMoney(position.value_account, position.account_currency)}
            </div>
          ) : null}
        </div>
        <div className="num text-right text-xs text-text-muted">
          {formatQuantity(position.quantity)} shares
          <br />
          avg {formatPrice(position.avg_price, position.instrument_currency)}
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="num flex flex-wrap items-center justify-between gap-2 text-xs">
          <span className="inline-flex items-center gap-2">
            Weight <b>{formatPercent(position.weight)}</b>
            <BandBadge status={position.drift_status} />
          </span>
          <span className="text-text-muted">
            {position.target_weight !== null ? `target ${formatPercent(position.target_weight)}` : "no target"}
            {band ? ` · band ${formatPercent(band[0])}–${formatPercent(band[1])}` : ""}
          </span>
        </div>
        <HBar
          value={position.weight}
          max={scale}
          target={position.target_weight}
          band={band}
          label="Weight against target"
          className="h-2.5"
        />
        <div className="num flex justify-between text-[10px] text-text-faint">
          <span>0%</span>
          <span>{formatPercent(scale, { decimals: 0 })}</span>
        </div>
      </div>
    </Blueprint>
  );
}

// ----------------------------------------------------------------------------- score

export function ScorePanel({
  row,
  kpis,
  method,
  loaded,
}: {
  row: BoardRow | null;
  kpis: KpiStatus[];
  method: string | null;
  loaded: boolean;
}) {
  // Spokes are named by the KPI id ("china-share"), usually shorter than the metric name.
  const radar = useMemo(() => thesisRadar(kpis, (_metric, id) => humanizeMetric(id.replace(/-/g, "_"))), [kpis]);
  const change = row ? scoreChange(row.score, row.score_history) : null;
  const title =
    row && typeof row.score === "number" ? (
      <>
        <span className="num">{row.score} / 100</span>
        {change ? (
          <span className="text-text-muted">
            {" "}
            · {change.direction === "same" ? "unchanged from" : `${change.direction} from`}{" "}
            <span className="num">{change.prior}</span>
          </span>
        ) : null}
      </>
    ) : loaded ? (
      "Not scored yet"
    ) : (
      "…"
    );
  return (
    <Panel id="score-heading" kicker="Thesis score" title={title} className="flex-1">
      {change ? (
        <p className="-mt-2 text-xs text-text-muted">
          Previous score on {formatDate(change.date)}; the history keeps the end of each day a verdict was recorded.
        </p>
      ) : null}
      {radar ? (
        <Radar
          axes={radar.axes}
          series={[
            { label: "Now", values: radar.now },
            ...(radar.previous ? [{ label: radar.previousLabel ?? "Previous", values: radar.previous, dashed: true }] : []),
          ]}
          formatValue={(v) => `${formatNumber(v * 2, 2)}× threshold`}
          label="KPI headroom against thresholds"
        />
      ) : (
        <p className="text-sm text-text-muted">
          {kpis.length < 3
            ? "The headroom radar needs at least three thesis KPIs."
            : "The headroom radar needs three KPIs with a measured latest verdict."}
        </p>
      )}
      <details className="text-xs text-text-muted">
        <summary className="cursor-pointer">How the score and radar work</summary>
        {method ? <p className="mt-1">{method}</p> : null}
        <p className="mt-1">{RADAR_NOTE}</p>
      </details>
    </Panel>
  );
}

// ----------------------------------------------------------------------------- KPI bars

const STATUS_TAG = {
  pass: "bg-accent-soft text-accent-ink",
  breach: "bg-loss-subtle text-loss",
  unknown: "border border-border text-text-muted",
} as const;

export function KpiBarsPanel({ ticker, kpis, hasThesis }: { ticker: string; kpis: KpiStatus[]; hasThesis: boolean }) {
  const latest = kpis
    .map((k) => k.current)
    .filter((v): v is NonNullable<typeof v> => Boolean(v))
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
  return (
    <Panel
      id="kpis-heading"
      kicker="Thesis KPIs"
      title={latest?.period ? `Checked against ${latest.period} results` : hasThesis ? "Not judged yet" : "No thesis yet"}
    >
      {!hasThesis ? (
        <p className="text-sm text-text-muted">
          Write why you own it and the KPIs that must hold in the{" "}
          <Link className="text-accent-ink hover:underline" href={`/config/theses#${encodeURIComponent(ticker)}`}>
            theses editor
          </Link>
          .
        </p>
      ) : kpis.length === 0 ? (
        <p className="text-sm text-text-muted">This thesis has no KPIs.</p>
      ) : (
        <ul className="flex flex-col gap-3.5">
          {kpis.map(({ kpi, current: v }) => {
            const bar = kpiBar(kpi.operator, kpi.threshold, v?.observed_value);
            const status = v?.status ?? "unknown";
            const label = humanizeMetric(kpi.metric);
            return (
              <li key={kpi.id} className="flex flex-col gap-1.5">
                <div className="flex items-start justify-between gap-2 text-[13px]">
                  <a href={`#${kpiAnchorId(kpi.id)}`} className="font-medium hover:text-accent-ink hover:underline">
                    {label} <span className="num font-normal text-text-muted">{formatTarget(kpi.operator, kpi.threshold, kpi.unit)}</span>
                  </a>
                  <span className={cn("shrink-0 px-1.5 py-0.5 text-[10px] font-semibold tracking-[0.05em] uppercase", STATUS_TAG[status])}>
                    {status}
                    {v?.stale ? " · old config" : ""}
                  </span>
                </div>
                <HBar
                  value={bar.value}
                  min={bar.min}
                  max={bar.max}
                  target={bar.target}
                  tone={status === "breach" ? "loss" : "accent"}
                  label={`${label} against its threshold`}
                  formatValue={(x) => formatObserved(x, kpi.unit)}
                />
                <div className="num flex justify-between gap-2 text-[11px] text-text-muted">
                  <span>
                    {v ? `Latest ${formatObserved(v.observed_value, kpi.unit)}` : "Never judged"}
                    {v?.period ? ` · ${v.period}` : ""}
                  </span>
                  <span>{bar.side ? bar.side.charAt(0).toUpperCase() + bar.side.slice(1) : ""}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// ----------------------------------------------------------------------------- drawdown & risk

function Stat({ label, value, sub }: { label: string; value: string; sub: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className="num font-heading text-xl font-semibold">{value}</div>
      <div className="text-[10px] text-text-faint">{sub}</div>
    </div>
  );
}

export function DrawdownPanel({
  history,
  portfolioIndex,
}: {
  history: PriceHistory | null;
  portfolioIndex: { date: string; close: number }[] | null;
}) {
  const bars = history?.bars ?? [];
  const year = barsInRange(bars, 365);
  const high = fromYearHigh(bars);
  const worst = yearMaxDrawdown(bars);
  const closes = year.map((b) => b.close);
  const varDay = historicalVar(closes);
  const pVol = portfolioIndex ? annualisedVolatility(barsInRange(portfolioIndex, 365).map((p) => p.close)) : null;
  const risk = history?.risk ?? null;
  const title = !high
    ? "Not enough history"
    : high.drawdown < 0
      ? `${formatPct(high.drawdown, 1)} from 52-week high`
      : "At its 52-week high";
  return (
    <Panel id="drawdown-heading" kicker="Drawdown" title={title}>
      {year.length > 1 ? (
        <DrawdownChart data={year.map((b) => ({ date: b.date, value: b.close }))} label="Close" height={150} />
      ) : (
        <p className="text-sm text-text-muted">No stored closes in the last year.</p>
      )}
      <div className="grid grid-cols-2 gap-3 border-t border-border pt-3 sm:grid-cols-4">
        <Stat
          label="Volatility"
          value={typeof risk?.volatility === "number" ? formatPct(risk.volatility, 1) : "—"}
          sub={pVol !== null ? `Portfolio ${formatPct(pVol, 1)}` : "annualised"}
        />
        <Stat
          label="Beta"
          value={typeof risk?.beta === "number" ? formatNumber(risk.beta, 2) : "—"}
          sub={history?.benchmark_symbol ? `vs ${history.benchmark_symbol}` : "no benchmark"}
        />
        <Stat
          label="Max DD 1Y"
          value={worst ? formatPct(worst.value, 1) : "—"}
          sub={worst ? formatDate(worst.date) : "—"}
        />
        <Stat label="VaR 95%" value={varDay !== null ? formatPct(varDay, 1) : "—"} sub="1-day, historical" />
      </div>
      <p className="text-[11px] text-text-muted">
        Drawdown and max DD from the last year of daily closes. Volatility and beta are the backend&rsquo;s (252 days,
        log returns annualised); the portfolio figure uses the same method on the flow-adjusted portfolio index. VaR is
        the 5th-percentile daily return of the last year.
      </p>
    </Panel>
  );
}

// ----------------------------------------------------------------------------- next report

export function NextReportPanel({ detail, today }: { detail: HoldingDetail; today: string }) {
  const instrument = detail.instrument;
  const countdown = reportCountdown(instrument?.next_report_date, instrument?.last_report_date, today);
  const charted = detail.kpis.filter((k) => k.history.some((v) => typeof v.observed_value === "number"));
  const [kpiId, setKpiId] = useState<string | null>(null);
  const chosen = charted.find((k) => k.kpi.id === kpiId) ?? charted[0];
  const source = instrument?.report_date_source;
  return (
    <Panel
      id="report-heading"
      kicker="Next report"
      title={instrument?.next_report_date ? formatDate(instrument.next_report_date) : "Date unknown"}
      action={
        countdown ? (
          <Ring
            value={countdown.elapsed !== null ? countdown.elapsed * 100 : null}
            label={String(Math.max(0, countdown.days))}
            size={58}
            thickness={5}
            ariaLabel="Share of the reporting interval elapsed"
          />
        ) : null
      }
    >
      <p className="text-[13px] text-text-muted">
        {countdown
          ? countdown.days > 0
            ? `${countdown.days} day${countdown.days === 1 ? "" : "s"} to go.`
            : countdown.days === 0
              ? "Reporting today."
              : `Due ${-countdown.days} day${countdown.days === -1 ? "" : "s"} ago; waiting for the results.`
          : "Set by the weekly calendar job."}{" "}
        {instrument?.last_report_date ? `Last read ${formatDate(instrument.last_report_date)}. ` : null}
        {source ? (
          isExternalUrl(source) ? (
            <a href={source} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-ink hover:underline">
              source <ExternalLinkIcon aria-hidden="true" className="size-3" />
            </a>
          ) : (
            <span>Source: {source}</span>
          )
        ) : null}
      </p>
      {chosen ? (
        <>
          <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] tracking-[0.1em] text-text-muted uppercase">
              {humanizeMetric(chosen.kpi.metric)}, {chosen.kpi.unit}
            </span>
            {charted.length > 1 ? (
              <div role="group" aria-label="KPI history to chart" className="flex flex-wrap gap-1">
                {charted.map((k) => (
                  <button
                    key={k.kpi.id}
                    type="button"
                    aria-pressed={k === chosen}
                    onClick={() => setKpiId(k.kpi.id)}
                    className={cn(
                      "border px-1.5 py-0.5 text-[10px]",
                      k === chosen ? "border-accent bg-accent text-accent-foreground" : "border-border text-text-muted hover:text-text",
                    )}
                  >
                    {humanizeMetric(k.kpi.metric)}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          <BarChart
            data={[...chosen.history]
              .reverse()
              .slice(-8)
              .map((v) => ({ label: shortPeriod(v.period), value: v.observed_value }))}
            highlightLast
            threshold={{ value: chosen.kpi.threshold, label: `Threshold ${formatTarget(chosen.kpi.operator, chosen.kpi.threshold, chosen.kpi.unit)}` }}
            height={130}
            formatValue={(v) => formatNumber(v, Math.abs(v) >= 100 ? 0 : 1)}
            label={`${humanizeMetric(chosen.kpi.metric)} by period, ${chosen.kpi.unit}`}
          />
        </>
      ) : (
        <p className="text-sm text-text-muted">No KPI history yet: it fills in as verdicts are recorded.</p>
      )}
    </Panel>
  );
}

// ----------------------------------------------------------------------------- timeline

const MARK = {
  pass: "bg-gain",
  breach: "bg-warning-signal",
  unknown: "bg-text-faint",
} as const;

function markClass(e: TimelineEntry) {
  if (e.kind === "verdict") return MARK[e.tone];
  if (e.severity === null) return "bg-text-faint";
  return e.severity >= 2 ? "bg-accent" : "bg-accent-300";
}

export function TimelinePanel({ ticker, entries, loaded }: { ticker: string; entries: TimelineEntry[]; loaded: boolean }) {
  const thisYear = new Date().getFullYear();
  return (
    <Panel
      id="timeline-heading"
      kicker="Verdicts & news"
      title="What changed"
      action={
        <Link href={`/news?ticker=${encodeURIComponent(ticker)}`} className="inline-flex items-center gap-1 text-[13px] text-accent-ink hover:underline">
          All {displayTicker(ticker)} news <ArrowRightIcon aria-hidden="true" className="size-3.5" />
        </Link>
      }
    >
      {entries.length === 0 ? (
        <p className="text-sm text-text-muted">{loaded ? "No verdicts or linked news yet." : "Loading…"}</p>
      ) : (
        <ol className="flex flex-col">
          {entries.map((e) => (
            <li
              key={e.key}
              className="grid grid-cols-[72px_10px_1fr] items-start gap-x-2.5 border-t border-border-soft py-2.5 sm:grid-cols-[80px_10px_1fr_auto]"
            >
              <time dateTime={e.date} className="num font-mono text-xs text-text-muted">
                {new Date(e.date).toLocaleDateString(undefined, {
                  day: "2-digit",
                  month: "short",
                  ...(new Date(e.date).getFullYear() !== thisYear ? { year: "2-digit" } : {}),
                })}
              </time>
              <span aria-hidden="true" className={cn("mt-[5px] size-[9px]", markClass(e))} />
              <span className="text-sm">
                <span className="sr-only">{e.kind === "verdict" ? "Verdict run: " : "News: "}</span>
                {e.kind === "news" && e.href ? (
                  <a href={e.href} target="_blank" rel="noreferrer" className="hover:text-accent-ink hover:underline">
                    {e.text}
                  </a>
                ) : e.kind === "verdict" ? (
                  <a href="#kpi-heading" className="hover:text-accent-ink hover:underline">
                    {e.text}
                  </a>
                ) : (
                  e.text
                )}
              </span>
              <span className="col-start-3 text-[11px] text-text-muted sm:col-start-auto sm:text-right">{e.source}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

// ----------------------------------------------------------------------------- 13F holders

export function HoldersPanel({ data, loaded }: { data: HoldingOwnership | null; loaded: boolean }) {
  const rows = data ? holderBars(data.top_holders, data.top_holders_previous_period) : [];
  const period = data?.periods[0]?.period ?? null;
  return (
    <Panel id="holders-heading" kicker="Ownership · Form 13F" title="Largest reported holders">
      {rows.length === 0 ? (
        <p className="text-sm text-text-muted">{loaded ? "No 13F holders reported for this holding." : "Loading…"}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((r) => (
            <li key={r.key} className="grid grid-cols-[minmax(0,9.5rem)_1fr_4rem_3.75rem] items-center gap-2.5 text-[13px]">
              <span className="truncate" title={r.name}>
                {r.name}
              </span>
              <span aria-hidden="true" className="h-2.5 bg-border-soft">
                <span className="block h-full bg-accent" style={{ width: `${r.share * 100}%` }} />
              </span>
              <span className="num text-right" title={`${formatNumber(r.shares)} shares`}>
                {formatCompact(r.shares)}
              </span>
              <span
                className={cn(
                  "num text-right text-xs",
                  typeof r.change === "number" ? TONE_CLASS[gainLossTone(r.change)] : "text-text-muted",
                )}
              >
                {r.change === "new" ? "new" : typeof r.change === "number" ? formatPct(r.change, 1, true) : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {data && rows.length > 0 ? (
        <p className="text-[11px] text-text-muted">
          {data.source}
          {period ? `, quarter ended ${formatDate(period)}` : ""}. Shares held; bars relative to the largest holder.
          {data.top_holders_previous_period
            ? ` Change is shares against the quarter ended ${formatDate(data.top_holders_previous_period)}.`
            : ""}
        </p>
      ) : null}
    </Panel>
  );
}
