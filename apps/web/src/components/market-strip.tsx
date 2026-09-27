"use client";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const fetchStrip = () => getBrowserApiClient().GET("/market/strip");

const rate = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
const pct = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** "+0.42%" / "−0.18%" from a fraction, with a true minus sign. */
function signedPct(fraction: number): string {
  const v = fraction * 100;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${pct.format(Math.abs(v))}%`;
}

/**
 * The header's market strip: the configured indices' last daily move (Config → Jobs →
 * Prices → Market strip) and the reference FX rates today's valuation uses. Every figure
 * is a stored daily close or reference rate, dated in its tooltip; an index with no data
 * yet is simply not shown. Hidden below xl — it is context, never the only place a figure
 * appears.
 */
export function MarketStrip() {
  const { state } = useApiResource(fetchStrip);
  if (state.kind !== "ok") return null;
  const { base_currency: base, fx, indices } = state.data;
  const shownIndices = indices.filter((i) => i.change_pct != null);
  if (shownIndices.length === 0 && fx.length === 0) return null;

  return (
    <ul
      aria-label="Markets"
      className="ml-auto hidden items-center gap-[22px] font-mono text-xs whitespace-nowrap text-text-muted xl:flex"
    >
      {shownIndices.map((i) => (
        <li key={i.symbol} title={i.as_of ? `${i.label}: close of ${i.as_of} vs ${i.previous_date ?? "previous close"}` : undefined}>
          {i.label}{" "}
          <span className={cn("num", i.change_pct! > 0 ? "text-gain" : i.change_pct! < 0 ? "text-loss" : undefined)}>
            {signedPct(i.change_pct!)}
          </span>
        </li>
      ))}
      {fx.filter((r) => r.rate != null).map((r) => (
        <li key={r.currency} title={r.rate_date ? `${r.source.toUpperCase()} reference rate of ${r.rate_date}` : undefined}>
          {r.currency}/{base ?? r.base_currency} <span className="num">{rate.format(r.rate!)}</span>
        </li>
      ))}
    </ul>
  );
}
