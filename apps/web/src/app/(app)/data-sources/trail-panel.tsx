"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { SELECT_CLASS } from "@/components/reference/form-bits";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const RULES = ["credit_stress", "trade_exposure", "patent_status", "company_graph"] as const;
const OUTCOMES = ["raised", "throttled", "revision_ignored", "baseline", "not_material", "no_baseline", "persisting", "review_needed", "below_severity", "blocked_by_gate"] as const;
type Rule = (typeof RULES)[number];
type Outcome = (typeof OUTCOMES)[number];
const label = (s: string) => s.replaceAll("_", " ");

/**
 * GET /alerts/free-source-trail: every time a free-source alert rule ran and what it decided,
 * newest first, including the runs that raised nothing and why. Filter by rule or outcome.
 */
export function TrailPanel() {
  const [rule, setRule] = useState<Rule | "">("");
  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [beforeId, setBeforeId] = useState<number | null>(null);
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/alerts/free-source-trail", {
        params: { query: { limit: 50, ...(rule ? { rule } : {}), ...(outcome ? { outcome } : {}), ...(beforeId !== null ? { before_id: beforeId } : {}) } },
      }),
    [rule, outcome, beforeId],
  );
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => data?.rows ?? [], [data]);

  return (
    <section id="trail" aria-labelledby="trail-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="trail-heading" className="panel-title">
            Alert decisions
          </h2>
          <p className="max-w-[68ch] text-sm text-text-muted">What each free-source alert rule decided, newest first, including the times it stayed quiet and why.</p>
        </div>
        <div className="flex gap-3">
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Rule
            <select className={SELECT_CLASS} value={rule} onChange={(e) => { setRule(e.target.value as Rule | ""); setBeforeId(null); }}>
              <option value="">All rules</option>
              {RULES.map((r) => <option key={r} value={r}>{label(r)}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Outcome
            <select className={SELECT_CLASS} value={outcome} onChange={(e) => { setOutcome(e.target.value as Outcome | ""); setBeforeId(null); }}>
              <option value="">All outcomes</option>
              {OUTCOMES.map((o) => <option key={o} value={o}>{label(o)}</option>)}
            </select>
          </label>
        </div>
      </div>

      {state.kind === "loading" && !data ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading decisions…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load the trail: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {data && rows.length === 0 ? <EmptyState title="No decisions recorded">Run the free-source alerts job below to evaluate the rules.</EmptyState> : null}

      {rows.length > 0 ? (
        <ol className="relative space-y-0 border-l border-border pl-4">
          {rows.map((r) => (
            <li key={r.id} className="relative pb-3 last:pb-0">
              <span aria-hidden="true" className={`absolute top-1.5 -left-[21px] size-2.5 border ${r.outcome === "raised" ? "border-accent bg-accent" : "border-border bg-surface"}`} />
              <p className="text-sm">
                <span className="font-medium">{label(r.rule)}</span>
                {r.ticker ? <span className="font-mono"> {r.ticker}</span> : <span className="text-text-muted"> {r.scope}</span>}
                <span className="text-text-muted">: {label(r.outcome)}</span>
                {typeof r.severity === "number" ? <span className="text-text-muted">, severity {r.severity}</span> : null}
              </p>
              <p className="text-xs text-text-muted">
                <span className="num">{formatDateTime(r.created_at)}</span>
                {r.reason ? `. ${r.reason}` : ""}
                {r.job_id ? (
                  <>
                    {" "}
                    <Link href={`/jobs/${r.job_id}`} className="text-accent-ink hover:underline">
                      Job #{r.job_id}
                    </Link>
                  </>
                ) : null}
              </p>
            </li>
          ))}
        </ol>
      ) : null}

      {data?.next_before_id != null ? (
        <Button type="button" variant="outline" size="sm" onClick={() => setBeforeId(data.next_before_id ?? null)}>
          Show older decisions
        </Button>
      ) : null}
      {beforeId !== null ? (
        <Button type="button" variant="ghost" size="sm" onClick={() => setBeforeId(null)}>
          Back to newest
        </Button>
      ) : null}
    </section>
  );
}
