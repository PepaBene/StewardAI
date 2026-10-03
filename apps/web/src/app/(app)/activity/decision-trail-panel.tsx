"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { SelectField, TextField } from "@/components/config/fields";
import { EmptyState } from "@/components/empty-state";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  appendPage,
  EMPTY_TRAIL_FILTER,
  evidenceFields,
  OUTCOME_META,
  outcomeMeta,
  ruleLabel,
  summarize,
  TRAIL_OUTCOMES,
  TRAIL_RULE_LABELS,
  TRAIL_RULES,
  validTicker,
  type TrailFilter,
  type TrailOutcome,
  type TrailRow,
  type TrailRule,
} from "@/lib/activity/decision-trail";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const PAGE = 50;
const RULE_OPTIONS: { value: TrailRule | ""; label: string }[] = [
  { value: "", label: "All rules" },
  ...TRAIL_RULES.map((r) => ({ value: r, label: TRAIL_RULE_LABELS[r] })),
];
const OUTCOME_OPTIONS: { value: TrailOutcome | ""; label: string }[] = [
  { value: "", label: "All outcomes" },
  ...TRAIL_OUTCOMES.map((o) => ({ value: o, label: OUTCOME_META[o].label })),
];

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string; rows: TrailRow[]; next: number | null }
  | { kind: "ok"; rows: TrailRow[]; next: number | null; paging: boolean };

const KIND_CLASS = {
  alerted: "border-accent text-text",
  suppressed: "border-border text-text-muted",
  informational: "border-border-soft text-text-muted",
  provisional: "border-dashed border-border text-text-muted",
} as const;

/**
 * Decision trail: why each free-source alert (credit stress, trade exposure, patent status,
 * confirmed company graph) fired, was throttled, deduplicated or held back. Silent outcomes
 * (value below threshold, identical re-fetch) are not stored by the backend and only appear
 * in the job step detail, which this panel says plainly.
 */
export function DecisionTrailPanel() {
  const [filter, setFilter] = useState<TrailFilter>(EMPTY_TRAIL_FILTER);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const ticker = validTicker(filter.ticker);
  const tickerInvalid = filter.ticker.trim() !== "" && ticker === null;

  const fetchPage = useCallback(
    async (before: number | null) => {
      const { data, response } = await getBrowserApiClient().GET("/alerts/free-source-trail", {
        params: {
          query: {
            rule: filter.rule || undefined,
            outcome: filter.outcome || undefined,
            ticker: ticker ?? undefined,
            limit: PAGE,
            before_id: before ?? undefined,
          },
        },
      });
      if (!data) throw new Error(`The API answered HTTP ${response.status}.`);
      return data;
    },
    [filter.rule, filter.outcome, ticker],
  );

  useEffect(() => {
    if (tickerInvalid) return;
    let cancelled = false;
    fetchPage(null)
      .then((d) => !cancelled && setLoad({ kind: "ok", rows: d.rows, next: d.next_before_id, paging: false }))
      .catch((e: unknown) => !cancelled && setLoad({ kind: "error", message: e instanceof Error ? e.message : String(e), rows: [], next: null }));
    return () => {
      cancelled = true;
    };
  }, [fetchPage, tickerInvalid]);

  function change(next: TrailFilter) {
    setFilter(next);
    // Show the spinner right away, unless the ticker is invalid (then no request is made).
    if (next.ticker.trim() === "" || validTicker(next.ticker) !== null) setLoad({ kind: "loading" });
  }

  async function more() {
    if (load.kind !== "ok" || load.next === null) return;
    const { rows, next } = load;
    setLoad({ kind: "ok", rows, next, paging: true });
    try {
      const d = await fetchPage(next);
      setLoad({ kind: "ok", rows: appendPage(rows, d.rows), next: d.next_before_id, paging: false });
    } catch (e) {
      setLoad({ kind: "error", message: e instanceof Error ? e.message : String(e), rows, next });
    }
  }

  const rows = load.kind === "loading" ? [] : load.rows;
  const counts = summarize(rows);
  return (
    <section id="decision-trail" aria-labelledby="decision-trail-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div>
        <h2 id="decision-trail-heading" className="text-[22px] leading-tight font-semibold">
          Decision trail
        </h2>
        <p className="max-w-prose text-sm text-text-muted">
          Why each credit-stress, trade-exposure, patent-status and company-graph alert was raised, throttled, left for review or
          held back by a verification gate. Observations below the threshold and identical re-fetches are not stored; they appear
          only in the step detail of the{" "}
          <a href="#job-log" className="text-accent-ink hover:underline">free_source_alerts job</a>. Thresholds are in{" "}
          <Link href="/config/event-rules" className="text-accent-ink hover:underline">Config: Event rules</Link>.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <SelectField label="Rule" path="rule" errors={{}} value={filter.rule} options={RULE_OPTIONS} onChange={(rule) => change({ ...filter, rule })} />
        <SelectField label="Outcome" path="outcome" errors={{}} value={filter.outcome} options={OUTCOME_OPTIONS} onChange={(outcome) => change({ ...filter, outcome })} />
        <TextField
          label="Ticker"
          path="ticker"
          errors={tickerInvalid ? { ticker: ["Use letters, digits, . _ - only."] } : {}}
          value={filter.ticker}
          spellCheck={false}
          onChange={(ticker) => change({ ...filter, ticker })}
        />
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the decision trail…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the decision trail">{load.message}</AlertMessage> : null}
      {load.kind !== "loading" && rows.length === 0 && load.kind !== "error" ? (
        <EmptyState title="No decisions recorded">
          The free-source alert job has not recorded a decision{filter.rule || filter.outcome || ticker ? " for this filter" : " yet"}. It runs after the macro and company-research collectors.
        </EmptyState>
      ) : null}
      {rows.length > 0 ? (
        <>
          <p className="num text-xs text-text-muted" aria-live="polite">
            {rows.length} loaded: {counts.alerted} raised, {counts.suppressed} suppressed, {counts.informational} informational
            {counts.provisional > 0 ? `, ${counts.provisional} provisional (gate closed)` : ""}.
          </p>
          <ol className="divide-y divide-border-soft border border-border" aria-label="Alert decisions, newest first">
            {rows.map((r) => (
              <TrailItem key={r.id} row={r} />
            ))}
          </ol>
          {load.kind === "ok" && load.next !== null ? (
            <Button variant="outline" size="sm" disabled={load.paging} onClick={() => void more()}>
              {load.paging ? "Loading…" : "Older decisions"}
            </Button>
          ) : (
            <p className="text-xs text-text-muted">End of the trail.</p>
          )}
        </>
      ) : null}
    </section>
  );
}

export function TrailItem({ row }: { row: TrailRow }) {
  const meta = outcomeMeta(row.outcome);
  const fields = evidenceFields(row.evidence);
  return (
    <li className="space-y-1.5 p-3 text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={cn("border px-1.5 py-0.5 text-xs font-medium", KIND_CLASS[meta.kind])} title={meta.meaning}>
          {meta.label}
        </span>
        <span className="font-medium">{ruleLabel(row.rule)}</span>
        {row.ticker ? (
          <Link href={`/holdings/${encodeURIComponent(row.ticker)}`} className="font-mono text-xs text-accent-ink hover:underline">
            {row.ticker}
          </Link>
        ) : null}
        {row.severity !== null ? <span className="text-xs text-text-muted">severity {row.severity}</span> : null}
        <time dateTime={row.created_at} className="num text-xs text-text-muted">
          {formatDateTime(row.created_at)}
        </time>
      </p>
      <p className="break-words">{row.reason}</p>
      <p className="text-xs text-text-muted">{meta.meaning}</p>
      {fields.length > 0 || row.job_id !== null || row.news_item_id !== null ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-accent-ink select-none hover:underline">Evidence for scope {row.scope}</summary>
          <dl className="mt-1.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5">
            {fields.map((f) => (
              <div key={f.key} className="contents">
                <dt className="text-text-muted">{f.key.replace(/_/g, " ")}</dt>
                <dd className="num break-words">
                  {f.href ? (
                    <a href={f.href} target="_blank" rel="noreferrer noopener" className="text-accent-ink hover:underline">
                      {f.text}
                    </a>
                  ) : (
                    f.text
                  )}
                </dd>
              </div>
            ))}
            {row.job_id !== null ? (
              <div className="contents">
                <dt className="text-text-muted">job</dt>
                <dd className="num"><Link href={`/jobs/${row.job_id}`} className="text-accent-ink hover:underline">#{row.job_id}</Link></dd>
              </div>
            ) : null}
            {row.news_item_id !== null ? (
              <div className="contents">
                <dt className="text-text-muted">news item</dt>
                <dd className="num">#{row.news_item_id}</dd>
              </div>
            ) : null}
          </dl>
        </details>
      ) : null}
    </li>
  );
}
