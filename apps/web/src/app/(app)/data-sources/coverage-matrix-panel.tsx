"use client";

import { ChevronRightIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
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
import { label } from "@/lib/labels";
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

type MatrixRow = ReturnType<typeof coverageMatrix>[number];

/** "income_statement" → "Income statement". */
function datasetWords(dataset: string): string {
  const s = dataset.replaceAll("_", " ").replaceAll("/", ", ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : dataset;
}

/** Rows grouped per provider with counts by state; providers with more problems first. */
function byProvider(rows: readonly MatrixRow[]) {
  const map = new Map<string, MatrixRow[]>();
  for (const r of rows) map.set(r.provider, [...(map.get(r.provider) ?? []), r]);
  return [...map.entries()]
    .map(([provider, list]) => {
      const counts = Object.fromEntries(COVERAGE_STATES.map((s) => [s, list.filter((r) => r.cell.state === s).length])) as Record<CoverageState, number>;
      return { provider, rows: list, counts, problems: list.length - counts.available };
    })
    .sort((a, b) => b.problems - a.problems || a.provider.localeCompare(b.provider));
}

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
          <h2 id="matrix-heading" className="panel-title">
            What each source covers
          </h2>
          <p className="max-w-[68ch] text-sm text-text-muted">
            One row per provider, the ones with the most problems first; open one for its datasets. Each dataset shows the{" "}
            <strong className="font-medium text-text">worst</strong> state found, so a single missing security is never
            averaged away.
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
                <span aria-hidden="true" className={cn("inline-block h-3 w-5 rounded-mark border", STATE_CHIP[s])} />
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
            <ul className="space-y-2" aria-label="Coverage per provider">
              {byProvider(rows).map((p) => (
                <li key={p.provider}>
                  <details className="group rounded-card bg-surface-muted px-4 py-3">
                    <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-x-4 gap-y-1 [&::-webkit-details-marker]:hidden">
                      <span className="flex items-center gap-2 text-sm font-medium">
                        <ChevronRightIcon aria-hidden="true" className="size-4 text-text-muted transition-transform group-open:rotate-90" />
                        {label("provider", p.provider)}
                        <span className="font-normal text-text-muted">
                          {p.rows.length} {p.rows.length === 1 ? "dataset" : "datasets"}
                        </span>
                      </span>
                      <span className="flex flex-wrap gap-1.5 text-xs">
                        {COVERAGE_STATES.filter((s) => p.counts[s] > 0).map((s) => (
                          <span key={s} title={STATE_MEANING[s]} className={cn("rounded-mark border px-1.5 py-0.5 font-medium", STATE_CHIP[s])}>
                            {p.counts[s]} {STATE_LABEL[s].toLowerCase()}
                          </span>
                        ))}
                      </span>
                    </summary>
                    <div className="mt-3 overflow-x-auto">
                      <table className="w-full min-w-[36rem] text-sm">
                        <caption className="sr-only">Datasets from {label("provider", p.provider)}, with the worst state in each</caption>
                        <thead>
                          <tr className="border-b border-border-soft text-xs text-text-muted">
                            <th scope="col" className="py-2 pr-3 text-left font-medium">Dataset</th>
                            <th scope="col" className="px-3 py-2 text-left font-medium">State</th>
                            <th scope="col" className="px-3 py-2 text-right font-medium">Available</th>
                            <th scope="col" className="px-3 py-2 text-right font-medium">Periods filled</th>
                            <th scope="col" className="py-2 pl-3 text-left font-medium">Why</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.rows.map((r) => (
                            <tr key={r.dataset} className="border-b border-border-soft align-top last:border-0">
                              <th scope="row" className="py-2 pr-3 text-left font-normal" title={r.dataset}>
                                {datasetWords(r.dataset)}
                              </th>
                              <td className="px-3 py-2">
                                <span title={STATE_MEANING[r.cell.state]} className={cn("inline-block rounded-mark border px-1.5 py-0.5 text-xs font-medium", STATE_CHIP[r.cell.state])}>
                                  {STATE_LABEL[r.cell.state]}
                                </span>
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {r.cell.available} of {r.cell.entries}
                              </td>
                              <td className="num px-3 py-2 text-right">
                                {r.cell.ratio === null ? <span className="text-text-muted">Not reported</span> : formatPct(r.cell.ratio, 0)}
                              </td>
                              <td className="max-w-[28rem] py-2 pl-3 text-xs text-text-muted">
                                {r.cell.reasons.length > 0 ? r.cell.reasons.join("; ") : r.cell.oldestLatest ? `Oldest data ${r.cell.oldestLatest}` : ""}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  );
}
