"use client";

import { Segmented } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ATLAS_KINDS, SUMMARY_GROUPS, type AtlasFilters, type SummaryGroupBy } from "@/lib/coverage/types";
import { DEFAULT_ATLAS_FILTERS } from "@/lib/coverage/url";
import { providerLabel } from "@/lib/source-coverage/providers";
import type { EntityKind } from "@/lib/source-coverage/types";

import { KIND_LABEL } from "./atlas-matrix";

const GROUP_LABEL: Record<SummaryGroupBy, string> = {
  metric: "Dataset",
  source: "Provider",
  period: "Latest period",
  state: "State",
  security: "Security",
  geography: "Geography",
};

const selectClass =
  "h-9 w-full min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-text-muted">
      {label}
      {children}
    </label>
  );
}

export type FilterOptions = { providers: readonly string[]; datasets: readonly string[]; states: readonly string[] };

/**
 * The atlas filters. Option lists come from the unfiltered ledger summary; every selection is
 * sent to the backend, so what the matrix shows is the backend's filtered answer. The current
 * value is always kept as an option so a URL-supplied filter that matches nothing stays visible.
 */
export function AtlasFiltersBar({ filters, options, onChange }: { filters: AtlasFilters; options: FilterOptions; onChange: (f: AtlasFilters) => void }) {
  const set = (patch: Partial<AtlasFilters>) => onChange({ ...filters, ...patch });
  const withCurrent = (list: readonly string[], cur: string) => (cur && !list.includes(cur) ? [cur, ...list] : list);
  const dirty = JSON.stringify({ ...filters, group: "" }) !== JSON.stringify({ ...DEFAULT_ATLAS_FILTERS, group: "" });
  return (
    <form role="search" aria-label="Coverage atlas filters" className="flex flex-col gap-3" onSubmit={(e) => e.preventDefault()}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Field label="Dataset">
          <select value={filters.dataset} onChange={(e) => set({ dataset: e.target.value })} className={selectClass}>
            <option value="">All datasets</option>
            {withCurrent(options.datasets, filters.dataset).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Entity kind">
          <select value={filters.kind} onChange={(e) => set({ kind: e.target.value as EntityKind | "any" })} className={selectClass}>
            <option value="any">All kinds</option>
            {ATLAS_KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Entity key (exact)">
          <Input value={filters.entity} placeholder="e.g. AAPL or DE" onChange={(e) => set({ entity: e.target.value })} />
        </Field>
        <Field label="Period contains">
          <Input type="date" value={filters.period} onChange={(e) => set({ period: e.target.value })} />
        </Field>
        <Field label="Provider">
          <select value={filters.provider} onChange={(e) => set({ provider: e.target.value })} className={selectClass}>
            <option value="">All providers</option>
            {withCurrent(options.providers, filters.provider).map((p) => (
              <option key={p} value={p}>
                {providerLabel(p)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Ledger state">
          <select value={filters.state} onChange={(e) => set({ state: e.target.value })} className={selectClass}>
            <option value="">All states</option>
            {withCurrent(options.states, filters.state).map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Group the summary by"
          value={filters.group}
          onChange={(group) => set({ group })}
          options={SUMMARY_GROUPS.map((g) => ({ id: g, label: GROUP_LABEL[g] }))}
        />
        {dirty ? (
          <Button type="button" size="sm" variant="ghost" onClick={() => onChange({ ...DEFAULT_ATLAS_FILTERS, group: filters.group })}>
            Clear filters
          </Button>
        ) : null}
      </div>
    </form>
  );
}
