"use client";

import { useCallback, useEffect, type ReactNode } from "react";

import { CandleChart } from "@/components/charts";
import type { HoldingIntraday } from "@/lib/api/client";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatNumber, formatSigned } from "@/lib/charts/format";
import { formatDate, formatPercent, formatPrice, gainLossTone } from "@/lib/format";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { displayTicker } from "@/lib/portfolio/holdings-view";
import { headerPrice, hourTicks, intradayOhlc, YAHOO_LABEL } from "@/lib/prices/intraday";
import { cn } from "@/lib/utils";

/*
 * Intraday prices on the holding page (Yahoo Finance, unofficial): the header's last price
 * with its exchange time and delay, and the price chart's 1D range. The backend fetches on
 * demand (with a short cache) when this page asks; when Yahoo fails or intraday prices are
 * off, the page keeps its daily closes and says why.
 */

/** Re-read while the page stays open (the backend refetches past its cache age). */
const REFRESH_MS = 5 * 60 * 1000;

export function useHoldingIntraday(ticker: string): ResourceState<HoldingIntraday> {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/intraday", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") reload();
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [reload]);
  return state;
}

export function intradayData(state: ResourceState<HoldingIntraday> | undefined): HoldingIntraday | null {
  if (!state) return null;
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

const TONE = { gain: "text-gain", loss: "text-loss", flat: "" };

/**
 * The header's price from intraday data, or null when there is none newer than the daily
 * close (the header then shows the close, as before).
 */
export function HeaderIntradayPrice({
  intraday,
  dailyAsOf,
  currency,
}: {
  intraday: HoldingIntraday | null;
  dailyAsOf: string | null;
  currency: string | null;
}): ReactNode {
  const view = headerPrice(intraday, dailyAsOf, (d) => formatDate(d));
  if (!view) return null;
  const ccy = currency ?? intraday?.currency ?? "";
  const tone = gainLossTone(view.change);
  return (
    <div className="text-right">
      <div className="num font-heading text-[44px] leading-none font-semibold">{formatPrice(view.price, ccy)}</div>
      <div className="text-sm">
        {view.change !== null && view.pct !== null ? (
          <span className={cn("num", TONE[tone])}>
            {tone === "gain" ? "▲ " : tone === "loss" ? "▼ " : ""}
            {formatSigned(view.change, 2)} ({formatPercent(view.pct, { decimals: 2, signed: true })})
          </span>
        ) : null}
        <span className="text-text-muted" title={intraday?.exchange_name ? `${intraday.exchange_name}, ${intraday.exchange_tz ?? ""}` : undefined}>
          {" "}
          · {view.label}
        </span>
      </div>
    </div>
  );
}

function IntradayReadout({ intraday }: { intraday: HoldingIntraday }) {
  const bars = intraday.bars;
  if (bars.length === 0) return null;
  const highs = bars.map((b) => b.high ?? b.close);
  const lows = bars.map((b) => b.low ?? b.close);
  const volume = bars.reduce((sum, b) => sum + (b.volume ?? 0), 0);
  const item = (k: string, v: number | null | undefined) => (
    <span>
      {k} <b className="font-medium text-text">{typeof v === "number" ? formatNumber(v, 2) : "—"}</b>
    </span>
  );
  return (
    <div className="num flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-text-muted" aria-label={`Session of ${intraday.session_date ?? ""}`}>
      {item("O", bars[0].open)}
      {item("H", Math.max(...highs))}
      {item("L", Math.min(...lows))}
      {item("Last", intraday.last_price)}
      {item("Prev", intraday.previous_close)}
      <span>
        Vol <b className="font-medium text-text">{volume > 0 ? formatCompact(volume) : "—"}</b>
      </span>
      {intraday.session_date ? <span className="text-text-faint">{formatDate(intraday.session_date)}</span> : null}
    </div>
  );
}

/** The 1D range: the session's 5-minute bars, or why there are none (daily closes remain). */
export function IntradayChartBody({
  ticker,
  state,
  switcher,
}: {
  ticker: string;
  state: ResourceState<HoldingIntraday> | undefined;
  switcher: ReactNode;
}) {
  const intraday = intradayData(state);
  const bars = intraday ? intradayOhlc(intraday.bars) : [];
  const loading = !state || state.kind === "loading";
  const failed = state?.kind === "error" && !intraday;
  const ticks = hourTicks(bars.map((b) => b.date));
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {intraday ? <IntradayReadout intraday={intraday} /> : <span />}
        {switcher}
      </div>
      {loading && bars.length === 0 ? (
        <p className="text-sm text-text-muted" role="status">
          Loading intraday bars…
        </p>
      ) : bars.length > 1 ? (
        <CandleChart
          bars={bars}
          label={`${displayTicker(ticker)}, ${intraday?.session_date ?? ""} (5-minute bars, exchange time)`}
          width={880}
          height={400}
          volumeHeight={64}
          xTicks={ticks}
        />
      ) : (
        <div className="border border-dashed border-border p-4 text-sm text-text-muted" role="status">
          {failed
            ? `Intraday prices could not be loaded (${state?.kind === "error" ? state.title : ""}); pick a longer range for daily closes.`
            : (intraday?.note ?? "No intraday bars are stored for this holding.")}{" "}
          {intraday?.daily ? (
            <>
              Latest daily close: <span className="num">{formatPrice(intraday.daily.close, intraday.currency ?? "")}</span> on{" "}
              {formatDate(intraday.daily.date)} ({intraday.daily.source}).
            </>
          ) : null}
        </div>
      )}
      {intraday?.available && bars.length > 1 && intraday.note ? (
        <p className="border-l-2 border-warning-signal pl-2 text-xs text-text-muted" role="status">
          {intraday.note}
        </p>
      ) : null}
      <p className="text-xs text-text-muted">
        5-minute bars from {YAHOO_LABEL}
        {intraday?.symbol ? (
          <>
            {" "}
            (<span className="font-mono">{intraday.symbol}</span>)
          </>
        ) : null}
        , times in exchange time{intraday?.exchange_tz ? ` (${intraday.exchange_tz.replace(/_/g, " ")})` : ""}
        {intraday?.delay_note ? `, ${intraday.delay_note}` : ""}. Unofficial: it can be late or stop without notice.
        {intraday?.fetched_at ? ` Fetched ${new Date(intraday.fetched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.` : ""}
      </p>
    </>
  );
}
