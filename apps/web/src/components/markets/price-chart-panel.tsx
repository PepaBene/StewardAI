"use client";

import { useState } from "react";

import { CandleChart } from "@/components/charts";
import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame, Segmented } from "@/components/data-state";
import { candleBand, CANDLE_RIGHT, type OhlcBar } from "@/lib/charts/candles";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { describeAction, describeQuote, placeActions, quoteOf, rawSeries, seriesState, unexplainedJumps, chosenSeries } from "@/lib/markets/logic";
import { ACTION_GLYPHS, ACTION_LABELS, type CorporateAction, type PriceDiagnostic, type PriceHistory } from "@/lib/markets/types";
import { cn } from "@/lib/utils";

const WIDTH = 880;
const RANGES = [
  { id: "63", label: "3M" },
  { id: "126", label: "6M" },
  { id: "252", label: "1Y" },
] as const;

/**
 * Candles and volume for one holding with the corporate-action ledger as a lettered marker lane
 * on the candles' x geometry. The basis is stated outright: GET /holdings/{ticker}/prices serves
 * the provider-adjusted series only, so the raw option is disabled with the reason and the two
 * bases are never spliced into one line. Raw and adjusted window returns sit in the returns panel.
 */
export function PriceChartPanel({
  ticker,
  name,
  prices,
  diagnostics,
  actions,
  actionsReason,
}: {
  ticker: string;
  name: string | null;
  prices: PriceHistory;
  /** Null when the diagnostics could not be read. */
  diagnostics: readonly PriceDiagnostic[] | null;
  /** Null when the ledger could not be read (`actionsReason` says why). */
  actions: readonly CorporateAction[] | null;
  actionsReason?: string | null;
}) {
  const [range, setRange] = useState<(typeof RANGES)[number]["id"]>("126");
  const chosen = diagnostics ? chosenSeries(diagnostics) : null;
  const raw = diagnostics ? rawSeries(diagnostics, chosen) : null;
  const quote = quoteOf(chosen);
  const all: OhlcBar[] = prices.bars.map((b) => ({ date: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));
  const bars = all.slice(-Number(range));
  const placed = placeActions(bars, (actions ?? []).filter((a) => a.status !== "rejected"));
  const visible = placed.filter((p) => p.index !== null);
  const band = candleBand(bars.length, WIDTH);
  const jumps = unexplainedJumps(all.slice(-Number(range)), actions ?? []);
  const first = bars[0];
  const last = bars[bars.length - 1];
  const change = first?.close && last?.close && first.close > 0 ? last.close / first.close - 1 : null;
  const st = seriesState({ barCount: all.length, gap: prices.gap, plausibility: prices.plausibility ? { flagged: prices.plausibility.flagged, difference_pct: prices.plausibility.difference_pct, date: prices.plausibility.date } : null, chosen: diagnostics ? chosen : null });
  const hasSplit = (actions ?? []).some((a) => a.type === "split" && a.status === "confirmed");

  return (
    <ChartFrame
      id="price-chart"
      kicker={chosen ? `Series ${chosen.symbol}${chosen.mic ? ` · ${chosen.mic}` : ""}` : "Price series"}
      title={`${name ?? ticker} (${ticker})`}
      takeaway={
        last && last.close !== null && change !== null
          ? `Last close ${quote ? describeQuote(last.close, quote) : formatNumber(last.close, 2)} on ${formatDay(last.date)}, ${formatPct(change, 1, true)} over ${bars.length} sessions on the adjusted basis.`
          : "No priced bars are available."
      }
      units={`${quote ?? "quote unit not reported"} per share · adjusted basis (provider-adjusted closes as stored) · last ${bars.length} of ${all.length} stored sessions`}
      hasValue={all.length > 0}
      controls={
        <>
          <Segmented label="Window" value={range} onChange={setRange} options={RANGES} />
          <Segmented
            label="Price basis"
            value="adjusted"
            onChange={() => undefined}
            options={[
              { id: "raw", label: "Raw", disabled: true, hint: "Raw bars are stored but no endpoint serves them yet; the raw price return is shown in the returns panel." },
              { id: "adjusted", label: "Adjusted" },
            ]}
          />
        </>
      }
      provenance={{
        source: `${prices.source}${prices.symbol ? ` · ${prices.symbol}` : ""}`,
        asOf: prices.as_of,
        knownAt: prices.fetched_at,
        method: "Daily bars of the series every reader serves (bar type adjusted). Raw and adjusted are never spliced; a fallback source is named below when used.",
        state: st.state,
        reason: st.reason ?? prices.fallback_reason,
        coverage: chosen && chosen.expected_sessions > 0 ? { covered: chosen.expected_sessions - chosen.missing_days, total: chosen.expected_sessions, unit: "expected sessions" } : null,
      }}
      table={{
        caption: `${ticker} daily bars, adjusted basis, last ${bars.length} sessions`,
        columns: ["Date", "Open", "High", "Low", "Close", "Volume"],
        rows: [...bars].reverse().map((b) => [
          formatDay(b.date),
          b.open == null ? "not reported" : formatNumber(b.open, 2),
          b.high == null ? "not reported" : formatNumber(b.high, 2),
          b.low == null ? "not reported" : formatNumber(b.low, 2),
          b.close == null ? "not reported" : formatNumber(b.close, 2),
          b.volume == null ? "not reported" : formatNumber(b.volume, 0),
        ]),
      }}
    >
      <div className="flex min-w-0 flex-col gap-1">
        <CandleChart bars={bars} label={`${ticker} (adjusted)`} width={WIDTH} maWindow={20} formatPrice={(v) => formatNumber(v, 2)} />
        {actions === null ? (
          <p role="status" className="text-xs text-text-muted">
            Corporate-action lane unavailable: {actionsReason ?? "the ledger could not be read"}.
          </p>
        ) : visible.length > 0 ? (
          <svg
            viewBox={`0 0 ${WIDTH} 34`}
            className={SVG_BASE}
            role="img"
            aria-label={`Corporate actions in the window: ${visible.map((p) => `${ACTION_LABELS[p.action.type]} ${formatDay(p.action.exDate)}, ${p.action.status}`).join("; ")}`}
          >
            <line x1={0} x2={WIDTH - CANDLE_RIGHT} y1={4} y2={4} className="stroke-border" strokeWidth={1} />
            {visible.map((p) => {
              const cx = band.x(p.index!) + band.bandwidth / 2;
              const confirmed = p.action.status === "confirmed";
              return (
                <g key={p.action.id}>
                  <line x1={cx} x2={cx} y1={4} y2={11} className="stroke-text" strokeWidth={1} />
                  <rect x={cx - 8} y={11} width={16} height={16} className={cn("fill-surface-raised", confirmed ? "stroke-text" : "stroke-text-muted")} strokeWidth={1.25} strokeDasharray={confirmed ? undefined : "2 2"}>
                    <title>{`${ACTION_LABELS[p.action.type]} · ex ${formatDay(p.action.exDate)}${p.shifted ? " (non-session date: drawn at the next session)" : ""} · ${describeAction(p.action) ?? "amount not recorded"} · ${p.action.status} · ${p.action.source}`}</title>
                  </rect>
                  <text x={cx} y={23} textAnchor="middle" fontSize={11} className="fill-text font-mono font-semibold">
                    {ACTION_GLYPHS[p.action.type]}
                  </text>
                </g>
              );
            })}
            <text x={WIDTH} y={32} textAnchor="end" fontSize={10} className={AXIS_TEXT}>
              dashed = not confirmed
            </text>
          </svg>
        ) : (
          <p className="text-xs text-text-muted">No corporate action falls inside this window (solid marker = confirmed, dashed = suggested).</p>
        )}
      </div>

      {raw ? (
        <p className="text-xs text-text-muted">
          A raw-bar series is also stored for this listing ({raw.observed_sessions} sessions, last {raw.last_session ? formatDay(raw.last_session) : "n/a"}); it is not drawn because the price endpoint does not serve it.
        </p>
      ) : (
        <p className="text-xs text-text-muted">No raw-bar series is stored for this listing, so raw-basis figures and total return cannot be derived.</p>
      )}
      {hasSplit ? (
        <p role="note" className="border-l-[3px] border-l-info-signal bg-info-signal-subtle px-3 py-1.5 text-sm">
          A confirmed split is in the ledger. The drawn series is the adjusted basis, so the split does not appear as a price step; compare with the raw price return in the returns panel.
        </p>
      ) : null}
      {jumps.length > 0 ? (
        <p role="note" className="border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-1.5 text-sm">
          {jumps.length} large move{jumps.length === 1 ? "" : "s"} with no recorded corporate action ({jumps.map((j) => `${formatDay(j.date)} ${formatPct(j.change, 0, true)}`).join(", ")}). This may be a missing action or a bad bar; it is not treated as an event.
        </p>
      ) : null}
      {prices.plausibility ? (
        <p className={cn("text-xs", prices.plausibility.flagged ? "font-medium text-text" : "text-text-muted")}>
          Plausibility vs Trading212 on {formatDay(prices.plausibility.date)}: stored {prices.plausibility.source ?? "close"} {formatNumber(prices.plausibility.stooq_close, 2)} against {formatNumber(prices.plausibility.trading212_price, 2)} ({formatPct(prices.plausibility.difference_pct / 100, 2, true)}){prices.plausibility.flagged ? ", beyond the configured tolerance" : ""}.
        </p>
      ) : null}
    </ChartFrame>
  );
}
