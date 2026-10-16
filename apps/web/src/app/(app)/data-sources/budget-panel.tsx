"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { budgetRows, type BudgetRow } from "@/lib/data-sources/data-sources";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { label } from "@/lib/labels";

const fetchAccess = () => getBrowserApiClient().GET("/sources/access", { params: { query: {} } });

function tone(burn: number) {
  return burn >= 0.9 ? "loss" : burn >= 0.7 ? "warning" : "accent";
}

/** How much of each free quota the current window has spent. The governor blocks calls before any paid threshold. */
export function BudgetPanel() {
  const { state } = useApiResource(fetchAccess);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => (data ? budgetRows(data) : []), [data]);
  const tracked = rows.filter((r) => r.burn !== null);
  const untracked = rows.filter((r) => r.burn === null);

  return (
    <section id="budget" aria-labelledby="budget-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div>
        <h2 id="budget-heading" className="panel-title">
          Free quota used
        </h2>
        <p className="max-w-[68ch] text-sm text-text-muted">
          Credits spent in the current window against the free allowance. A bar turns amber from 70% and red from 90%.
          Endpoints without a tracked window are listed below without a bar.
        </p>
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading quota…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={`Could not load quota: ${state.title}`}>{state.detail}</AlertMessage>
      ) : null}

      {data && rows.length === 0 ? (
        <EmptyState title="No provider endpoints recorded">Run the source audit to register each provider endpoint.</EmptyState>
      ) : null}

      {tracked.length > 0 ? (
        <ul className="divide-y divide-border-soft rounded-card bg-surface-muted px-3">
          {tracked.map((r) => (
            <BudgetItem key={r.key} row={r} />
          ))}
        </ul>
      ) : null}

      {untracked.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink hover:underline">
            {untracked.length} endpoint{untracked.length === 1 ? "" : "s"} without a tracked quota
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-text-muted">
            {untracked.map((r) => (
              <li key={r.key}>
                <span className="font-medium text-text">{label("provider", r.key)}</span>: {r.unknownReason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function BudgetItem({ row }: { row: BudgetRow }) {
  const burn = row.burn ?? 0;
  return (
    <li className="grid grid-cols-1 items-center gap-x-4 gap-y-1 px-3 py-2 sm:grid-cols-[minmax(10rem,16rem)_1fr_9rem]">
      <span className="text-sm font-medium">{label("provider", row.key)}</span>
      <HBar value={burn} tone={tone(burn)} label={`${row.key} quota used`} formatValue={(v) => formatPct(v, 0)} />
      <span className="num text-right text-xs text-text-muted">
        {row.used} of {row.quota} per {row.period}
      </span>
    </li>
  );
}
