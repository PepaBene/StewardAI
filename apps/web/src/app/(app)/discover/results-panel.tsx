"use client";

import { ChevronDownIcon, ChevronRightIcon, DownloadIcon, EyeIcon, LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format";
import {
  describeFilter,
  formatMetric,
  funnel,
  funnelBalances,
  MAX_COMPARE,
  type DiscoveryResult,
  type Kind,
  type MetricInfo,
  type ResultRow,
} from "@/lib/discover/discover";
import { cn } from "@/lib/utils";

/** Why a row passed: each filter's threshold against the actual value, then any extra notes. */
function WhyRow({ row, catalogue, colSpan }: { row: ResultRow; catalogue: readonly MetricInfo[]; colSpan: number }) {
  return (
    <tr className="border-b border-border bg-surface-muted/50">
      <td colSpan={colSpan} className="px-4 py-3">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-semibold">Why this row matched</h3>
            {row.checks.length === 0 ? (
              <p className="text-xs text-text-muted">No conditions are set, so every issuer with data is listed.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {row.checks.map((c) => {
                  const info = catalogue.find((m) => m.id === c.metric);
                  const unit = info?.unit ?? "";
                  const actual = formatMetric({ value: c.actual as string | null }, unit);
                  return (
                    <li key={`${c.metric}-${c.op}`} className="flex flex-wrap gap-x-2">
                      <span
                        className={cn(
                          "font-medium",
                          c.outcome === "pass" && "text-gain",
                          c.outcome === "fail" && "text-loss",
                          c.outcome === "unknown" && "text-unconfirmed",
                        )}
                      >
                        {c.outcome === "pass" ? "Passed" : c.outcome === "fail" ? "Failed" : "Unknown"}
                      </span>
                      <span>{info?.label ?? c.metric}</span>
                      <span className="text-text-muted">
                        {actual ?? c.reason ?? "no value"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <div className="space-y-1 text-xs text-text-muted">
            {(row.notes ?? []).map((n) => (
              <p key={n}>{n}</p>
            ))}
            {row.unknown.length > 0 ? <p>Unknown for this row: {row.unknown.join(", ")}. Unknown is kept visible, never read as zero.</p> : null}
          </div>
        </div>
      </td>
    </tr>
  );
}

export function ResultsPanel({
  kind,
  result,
  rows,
  catalogue,
  columns,
  selected,
  onToggleSelect,
  onLoadMore,
  loadingMore,
  onExport,
  exporting,
  onWatch,
  watched,
}: {
  kind: Kind;
  result: DiscoveryResult;
  rows: ResultRow[];
  catalogue: readonly MetricInfo[];
  columns: readonly string[];
  selected: ReadonlySet<string>;
  onToggleSelect: (key: string) => void;
  onLoadMore: () => void;
  loadingMore: boolean;
  onExport: () => void;
  exporting: boolean;
  onWatch: (key: string) => void;
  watched: ReadonlySet<string>;
}) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const steps = funnel(result);
  const shown = columns.map((id) => catalogue.find((m) => m.id === id)).filter((m): m is MetricInfo => Boolean(m));
  const colSpan = shown.length + 4;
  const toggle = (key: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section aria-labelledby="results-heading" className="blueprint space-y-4 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="results-heading" className="text-[22px] leading-tight font-semibold">
            {result.matched} of {result.universe_size} {kind === "fund" ? "funds" : "instruments"} match
          </h2>
          <p className="max-w-[72ch] text-sm text-text-muted">
            {result.universe_label}: {result.universe_definition} Data date {formatDate(result.as_of)}.{" "}
            {result.filters.length > 0
              ? `Conditions: ${result.filters.map((f) => describeFilter(f, catalogue)).join("; ")}.`
              : "No conditions, so everything with data is listed."}
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={onExport} disabled={exporting}>
          {exporting ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <DownloadIcon aria-hidden="true" />}
          Export CSV
        </Button>
      </div>

      <div className="space-y-2">
        <StackedBar title="Where each member ended up" segments={steps.map((s) => ({ label: s.label, value: s.value }))} />
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {steps.map((s) => (
            <li key={s.id}>
              <span className="num font-medium">{s.value}</span> <span className="text-text-muted">{s.label.toLowerCase()}</span>
            </li>
          ))}
        </ul>
        {!funnelBalances(result) ? (
          <p className="text-xs text-loss" role="alert">
            These counts do not add up to the universe size. Report this run.
          </p>
        ) : null}
        {result.filter_coverage.length > 0 ? (
          <ul className="flex flex-wrap gap-x-5 gap-y-0.5 text-xs text-text-muted" aria-label="Coverage of each filtered metric">
            {result.filter_coverage.map((c) => (
              <li key={c.metric}>
                {c.label}: <span className="num">{c.known}</span> of <span className="num">{c.total}</span> have a value
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      {result.warnings.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-unconfirmed" role="note">
          {result.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState title="Nothing matches">Loosen a condition, or keep members with a missing number to see what the gaps hide.</EmptyState>
      ) : (
        <div className="relative overflow-x-auto border border-border">
          <table className="w-full min-w-[56rem] text-sm">
            <caption className="sr-only">Matches. Select up to {MAX_COMPARE} rows to compare. Each row has an explanation of why it matched.</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className="w-10 px-3 py-2 text-left font-medium">
                  <span className="sr-only">Compare</span>
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  {kind === "fund" ? "Fund" : "Issuer"}
                </th>
                {shown.map((m) => (
                  <th key={m.id} scope="col" className="px-3 py-2 text-right font-medium">
                    {m.label}
                    {result.filters.some((f) => f.metric === m.id) ? <span className="block text-[11px] font-normal text-accent-ink">in screen</span> : null}
                  </th>
                ))}
                <th scope="col" className="px-3 py-2 text-right font-medium">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isOpen = open.has(row.key);
                return (
                  <RowGroup key={row.key}>
                    <tr className="border-b border-border align-top">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          aria-label={`Compare ${row.name ?? row.key}`}
                          checked={selected.has(row.key)}
                          disabled={!selected.has(row.key) && selected.size >= MAX_COMPARE}
                          onChange={() => onToggleSelect(row.key)}
                        />
                      </td>
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        {kind === "stock" ? (
                          <Link href={`/holdings/${encodeURIComponent(row.key)}`} className="font-medium hover:text-accent-ink hover:underline">
                            {row.name ?? row.key}
                          </Link>
                        ) : (
                          <Link href="/funds" className="font-medium hover:text-accent-ink hover:underline">
                            {row.name ?? row.key}
                          </Link>
                        )}
                        <span className="block font-mono text-xs text-text-muted">{row.key}</span>
                      </th>
                      {shown.map((m) => {
                        const text = formatMetric(row.values[m.id], m.unit);
                        const reason = row.values[m.id]?.reason;
                        return (
                          <td key={m.id} className={cn("num px-3 py-2 text-right", text === null && "text-text-muted")}>
                            {text ?? (
                              <span title={reason ?? "no value"} className="border border-dashed border-border px-1 text-xs">
                                Unknown
                              </span>
                            )}
                          </td>
                        );
                      })}
                      <td className="px-3 py-2 text-right whitespace-nowrap">
                        <Button type="button" variant="ghost" size="sm" aria-expanded={isOpen} onClick={() => toggle(row.key)}>
                          {isOpen ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
                          Why
                        </Button>
                        {kind === "stock" ? (
                          <Button type="button" variant="ghost" size="sm" disabled={watched.has(row.key)} onClick={() => onWatch(row.key)}>
                            <EyeIcon aria-hidden="true" />
                            {watched.has(row.key) ? "Watching" : "Watch"}
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                    {isOpen ? <WhyRow row={row} catalogue={catalogue} colSpan={colSpan} /> : null}
                  </RowGroup>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 text-xs text-text-muted">
        <span>
          Showing {rows.length} of {result.matched}.
        </span>
        {result.next_cursor ? (
          <Button type="button" variant="outline" size="sm" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            Load more
          </Button>
        ) : null}
      </div>

      {result.excluded.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-accent-ink hover:underline">
            Why {result.universe_size - result.matched} member{result.universe_size - result.matched === 1 ? " was" : "s were"} left out
          </summary>
          <ul className="mt-2 space-y-1.5 text-xs">
            {result.excluded.map((g) => (
              <li key={g.reason}>
                <span className="num font-medium">{g.count}</span> <span>{g.reason}</span>
                <span className="block text-text-muted">
                  {g.keys.join(", ")}
                  {g.count > g.keys.length ? `, and ${g.count - g.keys.length} more` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <details className="text-sm">
        <summary className="cursor-pointer text-accent-ink hover:underline">Definitions used</summary>
        <dl className="mt-2 space-y-1 text-xs">
          {Object.entries(result.formula_definitions).map(([id, text]) => (
            <div key={id}>
              <dt className="inline font-medium">{catalogue.find((m) => m.id === id)?.label ?? id}: </dt>
              <dd className="inline text-text-muted">{text}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-2 text-xs text-text-faint num">
          method {result.method_version} · definition {result.definition_hash.slice(0, 10)} · result {result.result_hash.slice(0, 10)}
        </p>
      </details>
      <ul className="space-y-0.5 text-xs text-text-muted">
        {result.caveats.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </section>
  );
}

function RowGroup({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
