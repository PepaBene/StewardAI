"use client";

import { useCallback } from "react";

import { StatCard } from "@/components/portfolio/stat-card";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { downsample } from "@/lib/portfolio/downsample";

const WINDOW_DAYS = 90;
const SPARKLINE_POINTS = 12;

/**
 * A KPI stat tile fed by the flow-adjusted portfolio history: the return over the window
 * plus a 12-point sparkline (visualization E), coloured only when the delta is the story
 * (i.e. always here, since the whole tile IS the delta).
 */
export function PerformanceStatCard() {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: WINDOW_DAYS } } }),
    [],
  );
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  if (!data || data.gap || data.points.length < 2) {
    return (
      <StatCard
        label={`Performance (${WINDOW_DAYS}d)`}
        value="—"
        sub={data?.gap === "no portfolio snapshot yet" ? "No snapshot yet" : "Not enough history yet"}
      />
    );
  }

  const values = data.points.map((p) => p.close);
  const start = values[0];
  const end = values[values.length - 1];
  const returnFraction = start > 0 ? end / start - 1 : null;
  const tone = gainLossTone(returnFraction);

  return (
    <StatCard
      label={`Performance (${WINDOW_DAYS}d)`}
      value={formatPercent(returnFraction, { signed: true })}
      valueClassName={tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : ""}
      trend={tone}
      sub="Flow-adjusted: deposits and withdrawals excluded"
      sparkline={downsample(values, SPARKLINE_POINTS)}
    />
  );
}
