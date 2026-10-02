"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { DataStateBadge, PreviewBanner, Segmented, StateLegend } from "@/components/data-state";
import { BudgetBurn } from "@/components/sources/budget-burn";
import { CoverageMatrix } from "@/components/sources/coverage-matrix";
import { Button } from "@/components/ui/button";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { DATA_STATES, type DataState } from "@/lib/data-state/states";
import { formatDateTime } from "@/lib/format";
import { resourceData, useDataResource } from "@/lib/hooks/use-data-resource";
import { filterRows, hasOverage, stateCounts, type ScopeFilter, type StateFilter } from "@/lib/source-coverage/logic";
import { loadSourceCoverage } from "@/lib/source-coverage/client";
import { PROVIDER_LABELS, type ProviderId } from "@/lib/source-coverage/types";

const SCOPES = [
  { id: "all", label: "Held + watchlist" },
  { id: "held", label: "Held" },
  { id: "watchlist", label: "Watchlist" },
] as const satisfies readonly { id: ScopeFilter; label: string }[];

/**
 * Source coverage: which provider datasets are usable for which securities, their freshness,
 * and how much of each free allowance is spent. A failed reload keeps the last report on
 * screen beside the error, the same rule the matrix applies to a failing provider.
 */
export function SourceCoveragePanel() {
  const { state: load, reload } = useDataResource(loadSourceCoverage);
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [state, setState] = useState<StateFilter>("all");
  const [provider, setProvider] = useState<ProviderId | "all">("all");
  // Ages ("3 h ago") are measured against the report's own generation time, so server and
  // client render the same text and the matrix never reports a fixture as hours old.
  const report = resourceData(load);
  const now = useMemo(() => (report ? new Date(report.generatedAt) : new Date(0)), [report]);
  const counts = useMemo(() => (report ? stateCounts(report.rows, scope) : null), [report, scope]);
  const rows = useMemo(() => (report ? filterRows(report.rows, { scope, state, provider }) : []), [report, scope, state, provider]);
  const providers = useMemo(() => [...new Set(report?.rows.map((r) => r.provider) ?? [])], [report]);

  return (
    <Blueprint as="section" id="source-coverage" aria-labelledby="source-coverage-heading" className="scroll-mt-24 space-y-4 px-5 py-5">
      <PanelHeading
        kicker="Free data sources"
        title={<span id="source-coverage-heading">Source coverage and request budget</span>}
        action={
          <Button variant="ghost" size="sm" onClick={reload}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />
      <p className="max-w-prose text-sm text-text-muted">
        Every provider dataset, for held securities and the watchlist, with its access state, coverage denominator and how fresh its
        newest verified value is. A failing refresh keeps the last verified value; a paid-only or unverified endpoint collects nothing.
      </p>
      <PreviewBanner endpoint="GET /data/coverage · /sources/access · /sources/usage" />

      {load.kind === "loading" ? (
        <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading source coverage…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load source coverage">{load.message}</AlertMessage> : null}

      {report && counts ? (
        <>
          <p role="status" className="num flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span>Generated {formatDateTime(report.generatedAt)}</span>
            <span className={hasOverage(report.budgets, report.spendUsd) ? "font-semibold text-text" : "text-text-muted"}>
              {hasOverage(report.budgets, report.spendUsd)
                ? "Overage recorded: this breaks the zero-cost gate"
                : `Provider spend $${report.spendUsd.toFixed(2)} · no paid endpoint or overage used`}
            </span>
          </p>

          <section aria-labelledby="source-budget-heading" className="space-y-2">
            <h3 id="source-budget-heading" className="font-heading text-lg font-semibold">
              Request budget
            </h3>
            <BudgetBurn budgets={report.budgets} />
          </section>

          <section aria-labelledby="source-matrix-heading" className="space-y-3">
            <h3 id="source-matrix-heading" className="font-heading text-lg font-semibold">
              Dataset matrix
            </h3>
            <div className="flex flex-wrap items-center gap-3">
              <Segmented label="Universe" options={SCOPES} value={scope} onChange={setScope} />
              <label className="flex items-center gap-1.5 text-xs text-text-muted">
                Provider
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as ProviderId | "all")}
                  className="border border-border bg-surface-raised px-2 py-1 text-xs text-text"
                >
                  <option value="all">All providers</option>
                  {providers.map((p) => (
                    <option key={p} value={p}>
                      {PROVIDER_LABELS[p]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div role="group" aria-label="Filter by state" className="flex flex-wrap gap-1.5">
              <button
                type="button"
                aria-pressed={state === "all"}
                onClick={() => setState("all")}
                className="border border-border px-2 py-0.5 text-xs font-medium aria-pressed:bg-accent aria-pressed:text-accent-foreground"
              >
                All states
              </button>
              {DATA_STATES.filter((s) => counts[s] > 0).map((s: DataState) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={state === s}
                  onClick={() => setState(state === s ? "all" : s)}
                  className="inline-flex items-center gap-1 outline-hidden focus-visible:outline-2 focus-visible:outline-focus aria-pressed:outline-2 aria-pressed:outline-text"
                >
                  <DataStateBadge state={s} />
                  <span className="num text-xs text-text-muted">{counts[s]}</span>
                </button>
              ))}
            </div>
            {rows.length === 0 ? (
              <EmptyState title="No datasets match">Clear a filter to see every provider dataset.</EmptyState>
            ) : (
              <CoverageMatrix rows={rows} now={now} />
            )}
          </section>

          <details className="border border-border-soft">
            <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">
              What each state means
            </summary>
            <div className="border-t border-border-soft p-3">
              <StateLegend counts={counts} />
            </div>
          </details>
        </>
      ) : null}
    </Blueprint>
  );
}
