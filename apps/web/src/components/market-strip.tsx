"use client";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");

const rate = new Intl.NumberFormat("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

/**
 * The header's right-hand market strip: the reference FX rates today's valuation used
 * (ČNB or ECB, per the strategy), one per held currency. Hidden below xl, and whenever
 * the overview is unavailable — it is context, never the only place a figure appears.
 */
export function MarketStrip() {
  const { state } = useApiResource(fetchOverview);
  if (state.kind !== "ok" || !state.data.base) return null;
  const base = state.data.base.base_currency;
  const rates = state.data.fx_rates.filter((r) => r.currency !== base);
  if (rates.length === 0) return null;

  return (
    <ul
      aria-label="Reference FX rates"
      className="ml-auto hidden items-center gap-[22px] font-mono text-xs whitespace-nowrap text-text-muted xl:flex"
    >
      {rates.map((r) => (
        <li key={r.currency} title={r.rate_date ? `${state.data.base?.fx_source.toUpperCase()} rate of ${r.rate_date}` : undefined}>
          {r.currency}/{base} <span className="num">{rate.format(r.rate)}</span>
        </li>
      ))}
    </ul>
  );
}
