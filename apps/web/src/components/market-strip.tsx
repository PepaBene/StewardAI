"use client";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { stripTooltip } from "@/lib/prices/intraday";
import { cn } from "@/lib/utils";

const fetchStrip = () => getBrowserApiClient().GET("/market/strip");

const rate = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const level = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** "+0.42%" / "−0.18%" from a fraction, with a true minus sign. */
function signedPct(fraction: number): string {
  const v = fraction * 100;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${pct.format(Math.abs(v))}%`;
}

/**
 * The header's market strip: the configured indices' latest move (Config → Jobs → Prices →
 * Market strip) and the reference FX rates today's valuation uses. Each index value comes
 * through a source chain — Stooq's close, else Yahoo's (intraday while its market is open,
 * unofficial), and for PX the Prague Stock Exchange's page (stored one close a day from the
 * first read, no backfill, so its history is short) — and its tooltip says which, and
 * whether it is an intraday value (with its time) or a close (with its date). An index with
 * no value yet is simply not shown; one with a value but no earlier close shows its level only. Hidden below xl — it is context, never the only place a
 * figure appears.
 */
export function MarketStrip() {
  const { state } = useApiResource(fetchStrip);
  if (state.kind !== "ok") return null;
  const { base_currency: base, fx, indices } = state.data;
  // An index with a stored value but no earlier one to compare (a series that has run for one day, PX)
  // is shown as its level with that stated; an index with no value at all is not shown.
  const shownIndices = indices.filter((i) => i.change_pct != null || i.last_close != null);
  if (shownIndices.length === 0 && fx.length === 0) return null;

  return (
    <ul
      aria-label="Markets"
      className="ml-auto hidden items-center gap-[22px] font-mono text-xs whitespace-nowrap text-text-muted xl:flex"
    >
      {shownIndices.map((i) => {
        const about = stripTooltip(i);
        return (
          <li key={i.symbol} title={about}>
            {i.label}{" "}
            {i.change_pct != null ? (
              <span className={cn("num", i.change_pct > 0 ? "text-gain" : i.change_pct < 0 ? "text-loss" : undefined)}>{signedPct(i.change_pct)}</span>
            ) : (
              <span className="num">
                {level.format(i.last_close!)} <span className="font-sans">(first close, no change yet)</span>
              </span>
            )}
            <span className="sr-only">, {about}</span>
          </li>
        );
      })}
      {fx.filter((r) => r.rate != null).map((r) => (
        <li key={r.currency} title={r.rate_date ? `${r.source.toUpperCase()} reference rate of ${r.rate_date}` : undefined}>
          {r.currency}/{base ?? r.base_currency} <span className="num">{rate.format(r.rate!)}</span>
        </li>
      ))}
    </ul>
  );
}
