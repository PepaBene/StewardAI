"use client";

import { PlusIcon, XIcon } from "lucide-react";

import { Segmented } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { isComplete } from "@/lib/screens/logic";
import { METRICS, metricDef } from "@/lib/screens/metrics";
import type { Criterion, MetricId, MissingTreatment, Op, ScopeFilter } from "@/lib/screens/types";
import { MAX_CRITERIA } from "@/lib/screens/url";

const selectClass =
  "h-9 min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

/** Keeps only characters a number needs; "," and ":" would break the URL encoding. */
export function cleanNumberText(text: string): string {
  return text.replace(/[^0-9.\-]/g, "").slice(0, 12);
}

export function CriteriaEditor({
  criteria,
  onChange,
  missing,
  onMissing,
  scope,
  onScope,
}: {
  criteria: readonly Criterion[];
  onChange: (c: Criterion[]) => void;
  missing: MissingTreatment;
  onMissing: (m: MissingTreatment) => void;
  scope: ScopeFilter;
  onScope: (s: ScopeFilter) => void;
}) {
  const update = (i: number, patch: Partial<Criterion>) => onChange(criteria.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <div className="flex flex-col gap-4">
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 text-xs font-medium text-text-muted">Criteria (all must hold)</legend>
        {criteria.length === 0 ? <p className="text-sm text-text-muted">No criteria yet. Add one to run the screen over the universe.</p> : null}
        {criteria.map((c, i) => {
          const def = metricDef(c.metric)!;
          return (
            <div key={i} className="flex flex-wrap items-end gap-2">
              <label className="flex min-w-0 flex-col gap-1 text-xs text-text-muted">
                Metric
                <select value={c.metric} onChange={(e) => update(i, { metric: e.target.value as MetricId })} className={selectClass}>
                  {METRICS.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                      {m.heldOnly ? " (held only)" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-text-muted">
                Condition
                <select value={c.op} onChange={(e) => update(i, { op: e.target.value as Op })} className={selectClass}>
                  <option value="gte">at least</option>
                  <option value="lte">at most</option>
                </select>
              </label>
              <label className="flex w-28 flex-col gap-1 text-xs text-text-muted">
                Threshold ({def.unit === "pct" ? "%" : "x"})
                <Input inputMode="decimal" value={c.value} aria-invalid={!isComplete(c)} onChange={(e) => update(i, { value: cleanNumberText(e.target.value) })} placeholder="number" />
              </label>
              <Button type="button" size="icon" variant="ghost" aria-label={`Remove criterion ${i + 1}: ${def.label}`} onClick={() => onChange(criteria.filter((_, j) => j !== i))}>
                <XIcon aria-hidden="true" />
              </Button>
              {!isComplete(c) ? <span className="text-xs text-text">Enter a number; this criterion is ignored until then.</span> : null}
            </div>
          );
        })}
        <div>
          <Button type="button" size="sm" variant="outline" disabled={criteria.length >= MAX_CRITERIA} onClick={() => onChange([...criteria, { metric: "pe", op: "lte", value: "" }])}>
            <PlusIcon aria-hidden="true" />
            Add criterion
          </Button>
        </div>
      </fieldset>

      <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
        <div className="flex flex-col gap-1 text-xs text-text-muted">
          <span id="missing-label">Missing-data treatment</span>
          <Segmented
            label="Missing-data treatment"
            value={missing}
            onChange={onMissing}
            options={[
              { id: "exclude", label: "Exclude", hint: "Entities with an unknown metric are dropped from the result and counted as excluded" },
              { id: "unknown", label: "Show as unknown", hint: "Entities with an unknown metric are listed as unknown, never as matches" },
            ]}
          />
          <span className="max-w-xs">Either way an unknown metric never passes and is never read as zero.</span>
        </div>
        <div className="flex flex-col gap-1 text-xs text-text-muted">
          <span>Universe</span>
          <Segmented
            label="Universe"
            value={scope}
            onChange={onScope}
            options={[
              { id: "all", label: "Held + watchlist" },
              { id: "held", label: "Held only" },
              { id: "watchlist", label: "Watchlist only" },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
