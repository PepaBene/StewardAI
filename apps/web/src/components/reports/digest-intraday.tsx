"use client";

import { useCallback, type CSSProperties } from "react";

import { Sparkline } from "@/components/charts";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { portfolioSpark } from "@/lib/prices/intraday";

/** The sparkline's line colour on the accent hero: its token re-pointed at the accent's
 * foreground for this subtree (a descendant-selector override did not reach the path). */
export const ON_ACCENT = { "--chart-line": "var(--accent-foreground)" } as CSSProperties;

/**
 * The daily digest hero's intraday line: the portfolio's value path over the latest trading
 * day up to the digest's window end (`GET /portfolio/intraday?until=`), from Yahoo Finance's
 * 5-minute bars (unofficial). Only the holdings with bars that day are valued, and the
 * caption says how many. Intraday bars are kept a few days, so an older digest (or a Yahoo
 * outage) shows a one-line "daily closes only" note instead. White on the accent hero.
 */
export function DigestIntradaySpark({ until }: { until: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/portfolio/intraday", { params: { query: { until } } }),
    [until],
  );
  const { state } = useApiResource(fetcher);
  if (state.kind === "loading") return null;
  const data = state.kind === "ok" ? state.data : state.previous;
  const spark = portfolioSpark(data);
  if (!spark || !data) {
    return <p className="mt-1.5 text-[11px] opacity-85">Intraday: not available, daily closes only</p>;
  }
  return (
    <div className="mt-2">
      <div className="w-32" style={ON_ACCENT}>
        <Sparkline
          values={spark.values}
          width={128}
          height={26}
          label={`Portfolio value, ${spark.caption}`}
          formatValue={(v) => Math.round(v).toLocaleString()}
          className="h-[26px]"
        />
      </div>
      <p className="mt-1 text-[11px] opacity-85" title={data.note ?? undefined}>
        {spark.caption}
        {data.change_pct != null ? ` · ${formatPct(data.change_pct, 2, true)}` : ""}
      </p>
    </div>
  );
}
