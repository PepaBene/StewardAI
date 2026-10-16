"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { num } from "@/lib/parse-untyped";

const GROUPS = [
  { id: "source", label: "Source" },
  { id: "state", label: "State" },
  { id: "metric", label: "Metric" },
  { id: "security", label: "Security" },
  { id: "geography", label: "Geography" },
  { id: "period", label: "Period" },
] as const;
type GroupBy = (typeof GROUPS)[number]["id"];

/**
 * GET /data/coverage/summary: how many coverage entries exist per group and how much of what is
 * expected is available. A group with no expected periods has no ratio and says so, rather than
 * showing 0 percent.
 */
export function CoverageSummaryPanel() {
  const [groupBy, setGroupBy] = useState<GroupBy>("source");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/data/coverage/summary", { params: { query: { group_by: groupBy } } }), [groupBy]);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const groups = useMemo(() => [...(data?.groups ?? [])].sort((a, b) => b.entries - a.entries), [data]);

  return (
    <section id="coverage-summary" aria-labelledby="coverage-summary-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="coverage-summary-heading" className="panel-title">
            Coverage in numbers
          </h2>
          <p className="max-w-[68ch] text-sm text-text-muted">Entries in the coverage ledger, grouped, with the share of expected periods that have data.</p>
        </div>
        <label className="flex flex-col gap-1 text-xs text-text-muted">
          Group by
          <select className={SELECT_CLASS} value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)}>
            {GROUPS.map((g) => (
              <option key={g.id} value={g.id}>
                {g.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {state.kind === "loading" && !data ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading coverage…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load coverage: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {data?.note ? <p className="max-w-[68ch] border border-dashed border-border px-3 py-2 text-sm text-text-muted">{data.note}</p> : null}
      {data && groups.length === 0 ? <EmptyState title="Nothing recorded">No coverage entries exist yet.</EmptyState> : null}

      {groups.length > 0 ? (
        <div className="relative overflow-x-auto rounded-card bg-surface-muted">
          <table className="w-full min-w-[40rem] text-sm">
            <caption className="sr-only">Coverage entries per {groupBy}</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">{GROUPS.find((g) => g.id === groupBy)?.label}</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Entries</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">States</th>
                <th scope="col" className="w-48 px-3 py-2 text-left font-medium">Periods available</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const ratio = num(g.coverage_ratio);
                return (
                  <tr key={g.key} className="border-b border-border align-top last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-normal">{g.key || "Unknown"}</th>
                    <td className="num px-3 py-2 text-right">{g.entries}</td>
                    <td className="px-3 py-2 text-xs text-text-muted">
                      {Object.entries(g.by_state ?? {}).map(([s, n]) => `${s.replaceAll("_", " ")} ${String(n)}`).join(", ") || "None"}
                    </td>
                    <td className="px-3 py-2">
                      {ratio === null ? (
                        <span className="text-xs text-text-muted">No expected periods, so no ratio</span>
                      ) : (
                        <>
                          <HBar value={ratio} label={`${g.key} periods available`} formatValue={(v) => formatPct(v)} />
                          <span className="num text-xs text-text-muted">
                            {g.available_periods} of {g.expected_periods} periods ({formatPct(ratio)})
                          </span>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
