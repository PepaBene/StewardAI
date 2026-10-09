"use client";

import { PlusIcon, XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  defaultOp,
  groupMetrics,
  MAX_FILTERS,
  nextMetric,
  OP_LABEL,
  unitSuffix,
  type FilterDraft,
  type MetricInfo,
} from "@/lib/discover/discover";
import { cn } from "@/lib/utils";

export const SELECT =
  "h-9 border border-border bg-surface-raised px-2 text-sm text-text focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none";

/** How much of the universe has a value for a metric, shown before the filter is applied. */
export function CoverageRail({ info }: { info: MetricInfo }) {
  const share = info.total > 0 ? info.known / info.total : 0;
  return (
    <div className="flex items-center gap-2 text-xs text-text-muted">
      <span
        role="img"
        aria-label={`${info.known} of ${info.total} have a value`}
        className="relative h-1.5 w-28 shrink-0 bg-border-soft"
      >
        <span className="absolute inset-y-0 left-0 bg-accent-300" style={{ width: `${Math.round(share * 100)}%` }} />
      </span>
      <span className="num">
        {info.known} of {info.total} have a value
      </span>
    </div>
  );
}

export function FilterBuilder({
  catalogue,
  drafts,
  onChange,
  nextId,
}: {
  catalogue: readonly MetricInfo[];
  drafts: FilterDraft[];
  onChange: (next: FilterDraft[]) => void;
  nextId: () => number;
}) {
  const groups = groupMetrics(catalogue);
  const update = (id: number, patch: Partial<FilterDraft>) => onChange(drafts.map((d) => (d.id === id ? { ...d, ...patch } : d)));

  return (
    <div className="space-y-3">
      <ul className="space-y-3" aria-label="Filters">
        {drafts.map((d, i) => {
          const info = catalogue.find((m) => m.id === d.metric);
          const categorical = info?.kind === "category";
          return (
            <li key={d.id} className="space-y-1.5 border-l-2 border-border-soft pl-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-12 text-xs text-text-muted">{i === 0 ? "Where" : "and"}</span>
                <select
                  aria-label={`Metric ${i + 1}`}
                  value={d.metric}
                  onChange={(e) => {
                    const next = catalogue.find((m) => m.id === e.target.value);
                    update(d.id, { metric: e.target.value, op: defaultOp(next), value: "" });
                  }}
                  className={cn(SELECT, "min-w-[15rem]")}
                >
                  {groups.map((g) => (
                    <optgroup key={g.group} label={g.label}>
                      {g.metrics.map((m) => (
                        <option key={m.id} value={m.id} disabled={!m.enabled}>
                          {m.label}
                          {m.enabled ? ` (${m.known}/${m.total})` : " (unavailable)"}
                        </option>
                      ))}
                    </optgroup>
                  ))}
                </select>
                {categorical ? (
                  <span className="text-sm text-text-muted">{OP_LABEL.in}</span>
                ) : (
                  <select
                    aria-label={`Condition ${i + 1}`}
                    value={d.op}
                    onChange={(e) => update(d.id, { op: e.target.value as FilterDraft["op"] })}
                    className={SELECT}
                  >
                    <option value="gte">{OP_LABEL.gte}</option>
                    <option value="lte">{OP_LABEL.lte}</option>
                  </select>
                )}
                <span className="flex items-center gap-1">
                  <Input
                    aria-label={`Value ${i + 1}`}
                    inputMode={categorical ? "text" : "decimal"}
                    placeholder={categorical ? "DE, FR, NL" : ""}
                    value={d.value}
                    onChange={(e) => update(d.id, { value: e.target.value })}
                    className={cn(categorical ? "w-48" : "w-24 text-right")}
                  />
                  {!categorical && info ? <span className="text-sm text-text-muted">{unitSuffix(info.unit)}</span> : null}
                </span>
                <Button type="button" variant="ghost" size="sm" aria-label={`Remove filter ${i + 1}`} onClick={() => onChange(drafts.filter((x) => x.id !== d.id))}>
                  <XIcon aria-hidden="true" />
                </Button>
              </div>
              {info ? (
                <div className="space-y-0.5 pl-14">
                  {info.enabled ? (
                    <CoverageRail info={info} />
                  ) : (
                    <p className="max-w-[72ch] text-xs text-unconfirmed" role="note">
                      Filter unavailable: {info.disabled_reason}
                    </p>
                  )}
                  <p className="max-w-[72ch] text-xs text-text-faint">
                    {info.formula} <span className="num">[{info.period}, definition v{info.definition_version}]</span>
                  </p>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={drafts.length >= MAX_FILTERS || catalogue.length === 0}
        onClick={() => {
          const metric = nextMetric(drafts, catalogue);
          onChange([...drafts, { id: nextId(), metric, op: defaultOp(catalogue.find((m) => m.id === metric)), value: "" }]);
        }}
      >
        <PlusIcon aria-hidden="true" />
        Add condition
      </Button>
    </div>
  );
}
