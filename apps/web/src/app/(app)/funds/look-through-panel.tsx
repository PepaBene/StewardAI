"use client";

import { useCallback, useMemo, useState } from "react";

import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { parseLookThrough } from "@/lib/funds/funds";
import { useApiResource } from "@/lib/hooks/use-api-resource";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The look-through report as it could have been computed on a chosen day (point in time). */
export function LookThroughPanel() {
  const max = useMemo(() => today(), []);
  const [asOf, setAsOf] = useState(max);
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/funds/look-through", { params: { query: { as_of: asOf, top: 25 } } }),
    [asOf],
  );
  const { state } = useApiResource(fetcher);
  const data = dataOf(state);
  const lt = useMemo(() => (data ? parseLookThrough(data) : null), [data]);
  const maxWeight = lt ? Math.max(0, ...lt.top.map((t) => t.weight ?? 0)) : 0;

  return (
    <Panel
      id="look-through"
      title="Look-through as of a date"
      description="The largest underlying companies across direct stocks and funds, using only the holdings records known by the end of the chosen day. Shares are of total portfolio value."
    >
      <div className="flex items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="lt-as-of">As of</Label>
          <Input id="lt-as-of" type="date" max={max} value={asOf} onChange={(e) => e.target.value && setAsOf(e.target.value)} className="w-44" />
        </div>
        {lt ? (
          <p className="num pb-2 text-xs text-text-muted">
            {lt.snapshotIds.length} snapshot{lt.snapshotIds.length === 1 ? "" : "s"} used
          </p>
        ) : null}
      </div>
      <ResourceNotice state={state} what="look-through" />
      {lt && !lt.available ? (
        <EmptyState title="Nothing could be looked through on that date">No fund holdings were known by then. Try a later date.</EmptyState>
      ) : null}
      {lt && lt.available ? (
        <div className="space-y-3">
          <p className="text-xs text-text-muted">
            {lt.portfolioShareLookedThrough === null
              ? "Share of the portfolio looked through is unknown."
              : `${formatPct(lt.portfolioShareLookedThrough, 0)} of total portfolio value is direct stocks or looked-through funds; the rest is cash and funds without rows.`}
            {lt.top10Share !== null ? ` Top 10 names: ${formatPct(lt.top10Share, 0)} of the looked-through part.` : ""}
            {lt.effectiveN !== null ? ` Equivalent to about ${Math.round(lt.effectiveN)} equal-sized names.` : ""}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse text-sm">
              <caption className="sr-only">Largest underlying companies, share of total portfolio value</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Company</th>
                  <th scope="col" className="w-[40%] py-1.5 pr-3 font-medium">Of total portfolio value</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Sector</th>
                  <th scope="col" className="py-1.5 text-right font-medium">Channels</th>
                </tr>
              </thead>
              <tbody>
                {lt.top.map((t) => (
                  <tr key={t.key} className="border-b border-border-soft">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                      {t.name}
                      {t.country ? <span className="num ml-2 text-[11px] text-text-muted">{t.country}</span> : null}
                    </th>
                    <td className="py-1.5 pr-3">
                      <HBar value={t.weight} max={maxWeight || 1} label={`${t.name} share of total portfolio value`} formatValue={(v) => formatPct(v, 2)} />
                    </td>
                    <td className="py-1.5 pr-3 text-xs text-text-muted">{t.sector ?? "Unknown"}</td>
                    <td className="num py-1.5 text-right">{t.viaFunds}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="space-y-0.5 text-xs text-text-muted">
            {lt.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
