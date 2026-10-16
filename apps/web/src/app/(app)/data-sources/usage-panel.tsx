"use client";

import { useMemo, useState } from "react";

import { HBar, LineChart } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { dailySeries, endpointTotals, parseUsage, providersOf } from "@/lib/data-sources/usage";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { label } from "@/lib/labels";

const fetchUsage = () => getBrowserApiClient().GET("/sources/usage", { params: { query: {} } });

/** Credits spent per day across the recorded quota windows, for one provider or all of them. */
export function UsagePanel() {
  const { state } = useApiResource(fetchUsage);
  const data = dataOf(state);
  const rows = useMemo(() => (data ? parseUsage(data) : []), [data]);
  const providers = useMemo(() => providersOf(rows), [rows]);
  const [provider, setProvider] = useState<string | null>(null);
  const [metric, setMetric] = useState<"credits" | "requests">("credits");
  const series = useMemo(() => dailySeries(rows, provider, metric), [rows, provider, metric]);
  const totals = useMemo(() => endpointTotals(rows, provider, metric).slice(0, 8), [rows, provider, metric]);
  const top = totals[0]?.total ?? 1;

  return (
    <Panel
      id="usage"
      title="Usage over time"
      description="Credits or requests recorded per day in the quota ledger. A day with no ledger row has no point: nothing was recorded, which is not the same as nothing being spent."
    >
      <ResourceNotice state={state} what="usage" />
      {data && rows.length === 0 ? <EmptyState title="No usage recorded yet">Usage appears after the first metered provider call.</EmptyState> : null}
      {rows.length > 0 ? (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <div role="group" aria-label="Provider" className="flex flex-wrap gap-1.5">
              <Button size="sm" variant={provider === null ? "default" : "outline"} aria-pressed={provider === null} onClick={() => setProvider(null)}>
                All providers
              </Button>
              {providers.map((p) => (
                <Button key={p} size="sm" variant={provider === p ? "default" : "outline"} aria-pressed={provider === p} onClick={() => setProvider(p)}>
                  {p}
                </Button>
              ))}
            </div>
            <div role="group" aria-label="Measure" className="flex gap-1.5">
              {(["credits", "requests"] as const).map((m) => (
                <Button key={m} size="sm" variant={metric === m ? "default" : "outline"} aria-pressed={metric === m} onClick={() => setMetric(m)}>
                  {m === "credits" ? "Credits" : "Requests"}
                </Button>
              ))}
            </div>
          </div>
          {series.length >= 2 ? (
            <LineChart
              data={series}
              formatValue={(v) => formatNumber(v, v % 1 === 0 ? 0 : 1)}
              label={metric === "credits" ? "Credits per day" : "Requests per day"}
              ariaLabel={`${metric === "credits" ? "Credits" : "Requests"} per day, ${provider ?? "all providers"}`}
            />
          ) : (
            <p className="text-sm text-text-muted">Fewer than two days with a figure for this selection, so there is no line to draw.</p>
          )}
          <ul className="divide-y divide-border-soft rounded-card bg-surface-muted px-3">
            {totals.map((t) => (
              <li key={t.key} className="grid grid-cols-1 items-center gap-x-4 gap-y-1 px-3 py-1.5 sm:grid-cols-[minmax(10rem,16rem)_1fr_6rem]">
                <span className="text-sm font-medium">{label("provider", t.key)}</span>
                <HBar value={t.total} max={top} label={`${t.key} ${metric}`} formatValue={(v) => formatNumber(v, v % 1 === 0 ? 0 : 1)} />
                <span className="num text-right text-xs text-text-muted">
                  {formatNumber(t.total, t.total % 1 === 0 ? 0 : 1)} {metric}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-text-muted">Totals cover the windows the ledger returned, not all time.</p>
        </div>
      ) : null}
    </Panel>
  );
}
