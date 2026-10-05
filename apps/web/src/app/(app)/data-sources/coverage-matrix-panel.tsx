"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import {
  COVERAGE_STATES,
  coverageMatrix,
  STATE_LABEL,
  STATE_MEANING,
  stateCounts,
  type CoverageState,
} from "@/lib/data-sources/data-sources";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const fetchCoverage = () => getBrowserApiClient().GET("/data/coverage", { params: { query: {} } });

/** Chip colours. Gain/loss hues are reserved for money, so states use the signal and accent tokens. */
const STATE_CHIP: Record<CoverageState, string> = {
  available: "border-accent bg-accent-soft text-text",
  stale: "border-accent-300 bg-surface text-text",
  missing: "border-warning-signal/50 bg-warning-signal-subtle text-text",
  not_covered: "border-border border-dashed bg-transparent text-text-muted",
  paid_only: "border-neutral-signal/60 bg-neutral-signal-subtle text-text",
  access_unverified: "border-unconfirmed/50 bg-unconfirmed-subtle text-text",
  quota_exhausted: "border-warning-signal bg-warning-signal-subtle text-text",
  disputed: "border-disputed/60 bg-disputed-subtle text-text",
};

/** Provider × dataset: the worst state across every security or country in the cell. */
export function CoverageMatrixPanel() {
  const { state, reload } = useApiResource(fetchCoverage);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => (data ? coverageMatrix(data) : []), [data]);
  const counts = useMemo(() => (data ? stateCounts(data) : null), [data]);

  return (
    <section id="matrix" aria-labelledby="matrix-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="matrix-heading" className="text-[22px] leading-tight font-semibold">
            What each source covers
          </h2>
          <p className="max-w-[68ch] text-sm text-text-muted">
            One row per provider and dataset. The state is the <strong className="font-medium text-text">worst</strong>{" "}
            one found, so a single missing security is never averaged away. &ldquo;Available&rdquo; counts the securities
            or countries that are fine out of those checked.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Reload
        </Button>
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading coverage…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={`Could not load coverage: ${state.title}`}>{state.detail}</AlertMessage>
      ) : null}

      {data && counts ? (
        <>
          <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
            {COVERAGE_STATES.map((s) => (
              <li key={s} className="flex items-center gap-1.5" title={STATE_MEANING[s]}>
                <span aria-hidden="true" className={cn("inline-block h-3 w-5 border", STATE_CHIP[s])} />
                <span>
                  {STATE_LABEL[s]} <span className="num text-text-muted">{counts[s]}</span>
                </span>
              </li>
            ))}
          </ul>

          {rows.length === 0 ? (
            <EmptyState title="No coverage recorded yet">
              Coverage appears after a source job has checked a security or country. Sources run on their own once their
              API key is set and the worker has started; you can also run a source job from this page.
            </EmptyState>
          ) : (
            <div className="relative overflow-x-auto border border-border">
              <table className="w-full min-w-[42rem] text-sm">
                <caption className="sr-only">Coverage per provider and dataset, showing the worst state in each</caption>
                <thead className="bg-surface-muted">
                  <tr className="border-b border-border text-xs text-text-muted">
                    <th scope="col" className="px-3 py-2 text-left font-medium">Provider</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Dataset</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">State</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Available</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Periods filled</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Why</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={`${r.provider}/${r.dataset}`} className="border-b border-border align-top last:border-0">
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        {i === 0 || rows[i - 1].provider !== r.provider ? (
                          <span className="font-medium">{r.provider}</span>
                        ) : (
                          <span className="sr-only">{r.provider}</span>
                        )}
                      </th>
                      <td className="px-3 py-2 font-mono text-xs">{r.dataset}</td>
                      <td className="px-3 py-2">
                        <span
                          title={STATE_MEANING[r.cell.state]}
                          className={cn("inline-block border px-1.5 py-0.5 text-xs font-medium", STATE_CHIP[r.cell.state])}
                        >
                          {STATE_LABEL[r.cell.state]}
                        </span>
                      </td>
                      <td className="num px-3 py-2 text-right">
                        {r.cell.available} of {r.cell.entries}
                      </td>
                      <td className="num px-3 py-2 text-right">
                        {r.cell.ratio === null ? <span className="text-text-muted">Not reported</span> : formatPct(r.cell.ratio, 0)}
                      </td>
                      <td className="max-w-[28rem] px-3 py-2 text-xs text-text-muted">
                        {r.cell.reasons.length > 0
                          ? r.cell.reasons.join("; ")
                          : r.cell.oldestLatest
                            ? `Oldest data ${r.cell.oldestLatest}`
                            : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}
