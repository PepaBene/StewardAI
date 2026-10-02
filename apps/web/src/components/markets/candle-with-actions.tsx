"use client";

import { useState } from "react";

import { CandleChart, LineChart } from "@/components/charts";
import { SVG_BASE } from "@/components/charts/shared";
import { ChartFrame, Segmented } from "@/components/data-state";
import { candleBand } from "@/lib/charts/candles";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { barsForBasis, describeQuote, placeActions, unexplainedJumps } from "@/lib/markets/logic";
import { ACTION_GLYPHS, ACTION_LABELS, type ListingSeries } from "@/lib/markets/types";
import { cn } from "@/lib/utils";

const WIDTH = 880;
const WINDOW = 180;

/**
 * Candles and volume for one listing with the corporate-action ledger drawn as a lettered
 * marker lane that shares the candles' x geometry. The basis control never substitutes one
 * adjustment for another: an unreconciled adjusted series shows its reason instead, and the
 * total-return index is a separate derived line, withheld when an input is missing.
 */
export function CandleWithActions({ listing }: { listing: ListingSeries }) {
  const [basis, setBasis] = useState<"raw" | "adjusted">("adjusted");
  const choice = barsForBasis(listing, basis);
  const bars = choice.bars.slice(-WINDOW);
  const placed = placeActions(bars, listing.actions);
  const band = candleBand(bars.length, WIDTH);
  const visible = placed.filter((p) => p.index !== null);
  const jumps = unexplainedJumps(choice.bars, listing.actions);
  const last = bars[bars.length - 1];
  const first = bars[0];
  const change = first && last && first.close > 0 ? last.close / first.close - 1 : null;
  const showRawNote = basis === "raw" && listing.actions.some((a) => a.type === "split");

  return (
    <ChartFrame
      id="price-chart"
      kicker={`${listing.venue} · ${listing.mic}`}
      title={`${listing.name} (${listing.ticker})`}
      takeaway={
        last && change !== null
          ? `Last close ${describeQuote(last.close, listing.quoteCurrency)} on ${formatDay(last.date)}, ${formatPct(change, 1, true)} over ${bars.length} sessions on the ${basis} basis.`
          : "No bars are available on this basis."
      }
      units={`${listing.quoteCurrency} per share, ${basis} basis, last ${bars.length} sessions${listing.ratioNote ? ` · ${listing.ratioNote}` : ""}`}
      hasValue={choice.available}
      controls={
        <Segmented
          label="Price basis"
          value={basis}
          onChange={setBasis}
          options={[
            { id: "raw", label: "Raw" },
            { id: "adjusted", label: "Adjusted", disabled: !barsForBasis(listing, "adjusted").available, hint: barsForBasis(listing, "adjusted").reason ?? undefined },
          ]}
        />
      }
      provenance={{
        source: listing.source,
        sourceUrl: listing.sourceUrl,
        asOf: last?.date ?? null,
        method: basis === "raw" ? "Raw closes as printed by the venue; a split appears as a step" : "Split-adjusted closes, reconciled to confirmed actions",
        state: choice.available ? listing.state : "unavailable",
        reason: choice.available ? listing.reason : choice.reason,
      }}
      table={{
        caption: `${listing.ticker} daily bars, ${basis} basis, last ${bars.length} sessions`,
        columns: ["Date", "Open", "High", "Low", "Close", "Volume"],
        rows: [...bars].reverse().map((b) => [
          formatDay(b.date),
          formatNumber(b.open, 2),
          formatNumber(b.high, 2),
          formatNumber(b.low, 2),
          formatNumber(b.close, 2),
          b.volume === null ? "—" : formatNumber(b.volume, 0),
        ]),
      }}
    >
      <div className="flex flex-col gap-1">
        <CandleChart bars={bars} label={`${listing.ticker} (${basis})`} width={WIDTH} maWindow={20} formatPrice={(v) => formatNumber(v, 2)} />
        {visible.length > 0 ? (
          <svg viewBox={`0 0 ${WIDTH} 30`} className={SVG_BASE} role="img" aria-label={`Corporate actions in the window: ${visible.map((p) => `${ACTION_LABELS[p.action.type]} ${formatDay(p.action.date)}${p.action.confirmed ? "" : " (unconfirmed)"}`).join("; ")}`}>
            <line x1={0} x2={WIDTH - 52} y1={4} y2={4} className="stroke-border" strokeWidth={1} />
            {visible.map((p) => {
              const cx = band.x(p.index!) + band.bandwidth / 2;
              return (
                <g key={p.action.id}>
                  <line x1={cx} x2={cx} y1={4} y2={11} className="stroke-text" strokeWidth={1} />
                  <rect
                    x={cx - 8}
                    y={11}
                    width={16}
                    height={16}
                    className={cn(p.action.confirmed ? "fill-surface-raised stroke-text" : "fill-surface-raised stroke-text-muted")}
                    strokeWidth={1.25}
                    strokeDasharray={p.action.confirmed ? undefined : "2 2"}
                  >
                    <title>{`${ACTION_LABELS[p.action.type]} · ${formatDay(p.action.date)}${p.shifted ? " (non-session date: shown at next session)" : ""} · ${p.action.detail ?? "amount not recorded"} · ${p.action.confirmed ? "confirmed" : "unconfirmed"} · ${p.action.source}`}</title>
                  </rect>
                  <text x={cx} y={23} textAnchor="middle" fontSize={11} className="fill-text font-mono font-semibold">
                    {ACTION_GLYPHS[p.action.type]}
                  </text>
                </g>
              );
            })}
          </svg>
        ) : (
          <p className="text-xs text-text-muted">No corporate action falls inside this window.</p>
        )}
      </div>

      {showRawNote ? (
        <p role="note" className="border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-1.5 text-sm">
          Raw basis: the split appears as a price step, not a loss. Switch to Adjusted to compare across it.
        </p>
      ) : null}
      {jumps.length > 0 ? (
        <p role="note" className="border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-1.5 text-sm">
          {jumps.length} large move{jumps.length === 1 ? "" : "s"} with no recorded corporate action ({jumps.map((j) => `${formatDay(j.date)} ${formatPct(j.change, 0, true)}`).join(", ")}).
          This may be a missing action or a bad bar; it is not treated as an event.
        </p>
      ) : null}

      <section aria-labelledby="actions-ledger-heading" className="flex flex-col gap-2">
        <h3 id="actions-ledger-heading" className="font-heading text-lg font-semibold">
          Corporate-action ledger
        </h3>
        {listing.actions.length === 0 ? (
          <p className="text-sm text-text-muted">No corporate action is recorded for this listing.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
            {listing.actions.map((a) => (
              <li key={a.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-border-soft pb-1.5">
                <span className="inline-flex size-5 items-center justify-center border border-text font-mono text-[11px] font-semibold" aria-hidden="true">
                  {ACTION_GLYPHS[a.type]}
                </span>
                <span className="font-medium">{ACTION_LABELS[a.type]}</span>
                <span className="num text-text-muted">{formatDay(a.date)}</span>
                <span className="num">{a.detail ?? "amount not recorded"}</span>
                <span className={cn("text-xs", a.confirmed ? "text-text-muted" : "font-medium text-text")}>
                  {a.confirmed ? "Confirmed" : "Unconfirmed"} · {a.source}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="total-return-heading" className="flex flex-col gap-2">
        <h3 id="total-return-heading" className="font-heading text-lg font-semibold">
          Total return (dividends reinvested)
        </h3>
        {listing.totalReturn ? (
          <LineChart
            data={listing.totalReturn.slice(-WINDOW).map((p) => ({ date: p.date, value: p.value }))}
            label="Total return index"
            height={180}
            formatValue={(v) => formatNumber(v, 1)}
            ariaLabel={`${listing.ticker} reinvested total-return index, start of window = 100`}
          />
        ) : (
          <p role="status" className="border border-dashed border-border px-3 py-3 text-sm text-text-muted">
            <strong className="font-medium text-text">Unavailable.</strong> {listing.totalReturnReason} Price return stays visible above.
          </p>
        )}
      </section>
    </ChartFrame>
  );
}
