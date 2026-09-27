"use client";

import {
  ArrowRightIcon,
  CheckIcon,
  CircleAlertIcon,
  LoaderCircleIcon,
  MinusIcon,
  PauseCircleIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { EvidenceLinks } from "@/components/evidence/evidence-links";
import { FactReviewForm, type ReviewResolution } from "@/components/evidence/fact-review-form";
import { ProvenanceTag } from "@/components/evidence/provenance-tag";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type FactOut,
  type HoldingDetail,
  type ReconciliationOut,
  type ReviewItem,
  type VerdictStatus,
} from "@/lib/api/client";
import { NEVER_JUDGED } from "@/lib/evidence/board";
import { formatFactValue, formatObserved, formatTarget, humanizeMetric } from "@/lib/evidence/confidence";
import { factProvenance } from "@/lib/evidence/provenance";
import {
  affectedKpis,
  formatInFactScale,
  kpiStatusSnapshot,
  parseReviewTicker,
  REVIEW_ACTIONS,
  reviewQueueHref,
  scaleHint,
  verdictRunOutcome,
  type VerdictRunOutcome,
} from "@/lib/evidence/review";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 30_000;

type Resolved = ReviewResolution & { ticker: string; name: string | null };

export function ReviewView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ticker = parseReviewTicker(new URLSearchParams(params.toString()));
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/facts/review-queue", { params: { query: { ticker: ticker ?? undefined } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const [resolved, setResolved] = useState<Resolved[]>([]);
  // Facts decided here leave the list at once (and stay out after their result is dismissed).
  const [removed, setRemoved] = useState<ReadonlySet<number>>(() => new Set());
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  const items = useMemo(
    () => (data ?? []).filter((i) => !removed.has(i.fact.id) && (!ticker || i.ticker === ticker)),
    [data, removed, ticker],
  );

  const setTicker = (next: string | null) =>
    router.replace(next ? reviewQueueHref(next) : pathname, { scroll: false });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="page-title">Review queue</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Figures the validator held or reconciliation quarantined. None of them is used in a verdict until you
            accept or correct it. Every decision needs a reason and re-judges the holding&rsquo;s verdicts.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Reload
        </Button>
      </div>
      <HoldingsTabs />

      {resolved.length > 0 ? (
        <section aria-labelledby="resolved-heading" className="space-y-3">
          <h2 id="resolved-heading" className="text-[22px] leading-tight font-semibold">
            Resolved just now
          </h2>
          <ul className="space-y-3">
            {resolved.map((r) => (
              <li key={r.fact.id}>
                <ResolutionCard
                  resolution={r}
                  onDismiss={() => setResolved((all) => all.filter((x) => x.fact.id !== r.fact.id))}
                />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <QueueFilter ticker={ticker} items={data ?? []} onChange={setTicker} />

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the review queue…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      {data ? (
        items.length === 0 ? (
          <EmptyState title={ticker ? `Nothing waits for review for ${ticker}` : "Nothing waits for review"}>
            Held and quarantined figures appear here after an earnings check.{" "}
            {ticker ? (
              <Link href="/review" className="text-accent underline-offset-4 hover:underline">
                Show every holding
              </Link>
            ) : (
              <Link href="/theses" className="text-accent underline-offset-4 hover:underline">
                Open the thesis board
              </Link>
            )}
            .
          </EmptyState>
        ) : (
          <QueueGroups
            items={items}
            onResolved={(item, resolution) => {
              setRemoved((ids) => new Set(ids).add(resolution.fact.id));
              setResolved((all) => [{ ...resolution, ticker: item.ticker, name: item.name }, ...all]);
              // A correction or restatement can change other items; refresh the queue quietly.
              reload();
            }}
          />
        )
      ) : null}
    </div>
  );
}

function QueueFilter({
  ticker,
  items,
  onChange,
}: {
  ticker: string | null;
  items: ReviewItem[];
  onChange: (ticker: string | null) => void;
}) {
  const id = useId();
  const tickers = useMemo(() => {
    const names = new Map<string, string | null>();
    for (const i of items) if (!names.has(i.ticker)) names.set(i.ticker, i.name);
    return [...names.entries()];
  }, [items]);

  if (ticker) {
    return (
      <p className="flex flex-wrap items-center gap-2 text-sm">
        Showing <span className="font-mono">{ticker}</span> only.
        <Button variant="outline" size="sm" onClick={() => onChange(null)}>
          <XIcon aria-hidden="true" />
          Show every holding
        </Button>
        <Link
          href={`/holdings/${encodeURIComponent(ticker)}`}
          className="text-accent underline-offset-4 hover:underline"
        >
          Open the holding page
        </Link>
      </p>
    );
  }
  if (tickers.length < 2) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id}>Holding</Label>
      <select
        id={id}
        value=""
        onChange={(e) => onChange(e.target.value || null)}
        className="h-8 rounded-md border border-border bg-surface-raised px-2 text-sm text-text focus-visible:outline-2 focus-visible:outline-focus"
      >
        <option value="">Every holding</option>
        {tickers.map(([t, name]) => (
          <option key={t} value={t}>
            {name ? `${name} (${t})` : t}
          </option>
        ))}
      </select>
    </div>
  );
}

function QueueGroups({
  items,
  onResolved,
}: {
  items: ReviewItem[];
  onResolved: (item: ReviewItem, resolution: ReviewResolution) => void;
}) {
  const groups = useMemo(() => {
    const byTicker = new Map<string, ReviewItem[]>();
    for (const item of items) byTicker.set(item.ticker, [...(byTicker.get(item.ticker) ?? []), item]);
    return [...byTicker.entries()];
  }, [items]);

  return (
    <div className="space-y-8">
      <p className="num text-sm text-text-muted" role="status">
        {items.length} figure{items.length === 1 ? "" : "s"} wait{items.length === 1 ? "s" : ""} for review across{" "}
        {groups.length} holding{groups.length === 1 ? "" : "s"}.
      </p>
      {groups.map(([ticker, group]) => {
        const headingId = `review-${ticker}`.replace(/[^A-Za-z0-9_-]/g, "-");
        return (
          <section key={ticker} aria-labelledby={headingId} className="space-y-3">
            <h2 id={headingId} className="flex flex-wrap items-baseline gap-2 text-[22px] leading-tight font-semibold">
              <Link href={`/holdings/${encodeURIComponent(ticker)}`} className="hover:text-accent hover:underline">
                {group[0].name ?? ticker}
              </Link>
              <span className="font-mono text-xs font-normal text-text-muted">{ticker}</span>
              <span className="num text-sm font-normal text-text-muted">· {group.length} waiting</span>
            </h2>
            <ul className="space-y-4">
              {group.map((item) => (
                <li key={item.fact.id}>
                  <ReviewItemCard item={item} onResolved={(r) => onResolved(item, r)} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function HeldBadge({ status }: { status: FactOut["status"] }) {
  const quarantined = status === "quarantined";
  const Icon = quarantined ? ShieldAlertIcon : PauseCircleIcon;
  return (
    <span className="inline-flex items-center gap-1 rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text">
      <Icon aria-hidden="true" className="size-3.5 text-warning-signal" />
      {quarantined ? "Quarantined" : "Held"}
    </span>
  );
}

function ReviewItemCard({ item, onResolved }: { item: ReviewItem; onResolved: (r: ReviewResolution) => void }) {
  const { fact } = item;
  const titleId = `fact-${fact.id}-title`;
  return (
    <article aria-labelledby={titleId} className="rounded-lg border border-border bg-surface-raised">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4">
        <div className="space-y-1">
          <h3 id={titleId} className="font-semibold">
            {humanizeMetric(fact.metric)}
            {fact.basis === "adjusted" ? <span className="font-normal text-text-muted"> (adjusted)</span> : null}{" "}
            <span className="font-normal">· {fact.period}</span>
          </h3>
          <p className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            <HeldBadge status={fact.status} />
            <span>{item.why}</span>
            <span>· figure #{fact.id}</span>
            <span>· extracted {formatDate(new Date(fact.extracted_at))}</span>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <span className="flex items-center gap-2">
            <span className="num text-lg font-semibold">{formatFactValue(fact)}</span>
            <ConfidenceBadge confidence={fact.confidence} />
            <ProvenanceTag provenance={factProvenance(fact)} />
          </span>
          <span className="text-xs text-text-muted">
            as printed <span className="num font-mono text-text">{fact.value_as_written}</span>
            {fact.scale !== "units" ? ` · in ${fact.scale}` : ""}
            {fact.currency ? ` · ${fact.currency}` : ""}
          </span>
          <FeedbackControl
            target={{ kind: "fact", id: fact.id }}
            label={`figure ${humanizeMetric(fact.metric)} ${fact.period}`}
            valueHint={scaleHint(fact)}
          />
        </div>
      </header>

      <div className="grid gap-6 p-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="min-w-0 space-y-4">
          <FailedChecks item={item} />

          <section aria-label="Snippet and source" className="space-y-2">
            <p className="text-xs font-medium tracking-wide text-text-muted uppercase">Quoted from the source</p>
            <blockquote className="border-l-2 border-border pl-3 font-mono text-xs break-words">
              {fact.snippet}
              {fact.unit_snippet ? <span className="mt-1 block text-text-muted">{fact.unit_snippet}</span> : null}
            </blockquote>
            <EvidenceLinks fact={fact} showHost />
          </section>

          {fact.restates_fact_id !== null ? <Restatement fact={fact} restates={item.restates} /> : null}

          {item.reconciliation.length > 0 ? <Reconciliation fact={fact} rows={item.reconciliation} /> : null}
        </div>

        <div className="rounded-md border border-border bg-surface-muted p-3">
          <FactReviewForm fact={fact} onResolved={onResolved} />
        </div>
      </div>
    </article>
  );
}

function FailedChecks({ item }: { item: ReviewItem }) {
  const { fact } = item;
  return (
    <section aria-label="Checks it failed" className="space-y-1.5">
      <p className="text-xs font-medium tracking-wide text-text-muted uppercase">
        {item.failed_checks.length > 0 ? "The bound it broke" : "Checks"}
      </p>
      {item.failed_checks.length > 0 ? (
        <ul className="space-y-1.5 text-sm">
          {item.failed_checks.map((c, i) => (
            <li key={`${c.check}-${i}`} className="flex gap-2">
              <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
              <span>
                <span className="font-medium">{humanizeMetric(c.check)}</span>
                {c.detail ? <span className="text-text-muted"> — {c.detail}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-text-muted">
          {fact.status === "quarantined"
            ? "Every validator check passed; the filing below disagrees with the release."
            : "No failed check was recorded."}
        </p>
      )}
      {fact.status_reason ? <p className="text-xs text-text-muted">Status: {fact.status_reason}</p> : null}
    </section>
  );
}

function FactColumn({ title, fact }: { title: string; fact: FactOut }) {
  return (
    <div className="min-w-0 space-y-1.5 rounded-md border border-border p-3">
      <p className="text-xs font-medium text-text-muted">
        {title} · figure #{fact.id}
      </p>
      <p className="flex flex-wrap items-center gap-2">
        <span className="num font-semibold">{formatFactValue(fact)}</span>
        <ConfidenceBadge confidence={fact.confidence} />
        <ProvenanceTag provenance={factProvenance(fact)} />
      </p>
      <p className="text-xs text-text-muted">
        {fact.period} · extracted {formatDate(new Date(fact.extracted_at))}
        {fact.origin === "owner" ? " · your correction" : ""}
      </p>
      <blockquote className="border-l-2 border-border pl-2 font-mono text-xs break-words text-text-muted">
        {fact.snippet}
      </blockquote>
      <EvidenceLinks fact={fact} />
    </div>
  );
}

function Restatement({ fact, restates }: { fact: FactOut; restates: FactOut | null }) {
  return (
    <section aria-label="Restatement" className="space-y-2">
      <p className="text-xs font-medium tracking-wide text-text-muted uppercase">Restates a stored figure</p>
      {restates ? (
        <div className="grid gap-2 sm:grid-cols-2">
          <FactColumn title="Stored before" fact={restates} />
          <FactColumn title="This figure" fact={fact} />
        </div>
      ) : (
        <p className="text-sm text-text-muted">It restates figure #{fact.restates_fact_id}, which is no longer stored.</p>
      )}
      <p className="text-xs text-text-muted">Both values are kept; neither overwrites the other.</p>
    </section>
  );
}

const OUTCOME = {
  match: { label: "Match", Icon: CheckIcon, className: "text-neutral-signal" },
  mismatch: { label: "Mismatch", Icon: TriangleAlertIcon, className: "text-warning-signal" },
  no_data: { label: "No data", Icon: MinusIcon, className: "text-text-muted" },
} as const satisfies Record<ReconciliationOut["outcome"], unknown>;

function Reconciliation({ fact, rows }: { fact: FactOut; rows: ReconciliationOut[] }) {
  return (
    <section aria-label="Reconciliation against filings" className="space-y-2">
      <p className="text-xs font-medium tracking-wide text-text-muted uppercase">Checked against the filing (XBRL)</p>
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[36rem] text-xs">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-text-muted">
              <th scope="col" className="px-2 py-1.5 text-left font-medium">Filing</th>
              <th scope="col" className="px-2 py-1.5 text-left font-medium">Concept</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">XBRL value</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Difference</th>
              <th scope="col" className="px-2 py-1.5 text-left font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const { label, Icon, className } = OUTCOME[r.outcome];
              return (
                <tr key={`${r.accession}-${r.checked_at}-${i}`} className="border-b border-border align-top last:border-0">
                  <td className="px-2 py-1.5">
                    <span className="font-medium">{r.form ?? "—"}</span>
                    {r.accession ? <span className="block font-mono text-text-muted">{r.accession}</span> : null}
                    {r.period_end ? (
                      <span className="block text-text-muted">period end {formatDate(r.period_end)}</span>
                    ) : null}
                  </td>
                  <td className="px-2 py-1.5 break-all">
                    {r.concept ?? "—"}
                    {r.derivation ? <span className="block text-text-muted">{r.derivation}</span> : null}
                  </td>
                  <td className="num px-2 py-1.5 text-right whitespace-nowrap">{formatInFactScale(r.xbrl_value, fact)}</td>
                  <td className="num px-2 py-1.5 text-right whitespace-nowrap">
                    {formatInFactScale(r.difference, fact, { signed: true })}
                  </td>
                  <td className="px-2 py-1.5">
                    <span className="inline-flex items-center gap-1 font-medium">
                      <Icon aria-hidden="true" className={cn("size-3.5", className)} />
                      {label}
                    </span>
                    {r.detail ? <span className="block text-text-muted">{r.detail}</span> : null}
                    <span className="block text-text-muted">{formatDateTime(r.checked_at)}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-text-muted">
        Values are shown in the figure&rsquo;s printed scale; the difference is this figure minus the filing&rsquo;s.
      </p>
    </section>
  );
}

type RunState = {
  detail: HoldingDetail | null;
  outcome: VerdictRunOutcome;
  before: Map<string, VerdictStatus | null> | undefined;
  timedOut: boolean;
  error: string | null;
};

/** Polls the holding until the verdict run the review queued has finished, then shows the KPIs it moved. */
function useVerdictRun(ticker: string, jobId: number) {
  const [attempt, setAttempt] = useState(0);
  const [run, setRun] = useState<RunState>({
    detail: null,
    outcome: "pending",
    before: undefined,
    timedOut: false,
    error: null,
  });
  const before = useRef<Map<string, VerdictStatus | null> | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();

    async function poll() {
      let finished = false;
      try {
        const { data, response } = await getBrowserApiClient().GET("/holdings/{ticker}", {
          params: { path: { ticker } },
        });
        if (cancelled) return;
        if (data) {
          const outcome = verdictRunOutcome(data.last_verdict_run, jobId);
          // The first answer while the run is still queued is the state before it.
          if (outcome === "pending" && before.current === undefined) before.current = kpiStatusSnapshot(data.kpis);
          finished = outcome !== "pending";
          setRun({ detail: data, outcome, before: before.current, timedOut: false, error: null });
        } else {
          setRun((r) => ({ ...r, error: `The holding could not be read (HTTP ${response.status}).` }));
        }
      } catch (err) {
        if (cancelled) return;
        setRun((r) => ({ ...r, error: err instanceof Error ? err.message : String(err) }));
      }
      if (finished || cancelled) return;
      if (Date.now() - started >= POLL_LIMIT_MS) {
        setRun((r) => ({ ...r, timedOut: true }));
        return;
      }
      timer = setTimeout(() => void poll(), POLL_MS);
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [ticker, jobId, attempt]);

  const retry = () => {
    setRun((r) => ({ ...r, timedOut: false }));
    setAttempt((a) => a + 1);
  };
  return { run, retry };
}

function ResolutionCard({ resolution, onDismiss }: { resolution: Resolved; onDismiss: () => void }) {
  const { fact, action, reason, value, result, ticker, name } = resolution;
  const { run, retry } = useVerdictRun(ticker, result.verdict_job.id);
  const heading = useRef<HTMLHeadingElement>(null);
  const holdingHref = `/holdings/${encodeURIComponent(ticker)}`;
  const holdingName = name ?? ticker;

  // The item the owner acted on has left the list; move focus to its outcome.
  useEffect(() => heading.current?.focus(), []);

  return (
    <article className="space-y-3 rounded-lg border border-border bg-surface-raised p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 ref={heading} tabIndex={-1} className="font-semibold outline-none">
            <CheckIcon aria-hidden="true" className="mr-1 inline size-4 text-text-muted" />
            {REVIEW_ACTIONS[action].done}: {humanizeMetric(fact.metric)} {fact.period} · {holdingName}
          </h3>
          <p className="mt-1 text-text-muted">
            {action === "correct" && value !== null ? (
              <>
                New value <span className="num font-mono text-text">{value}</span> in {fact.scale}
                {result.corrected_fact_id !== null ? ` (figure #${result.corrected_fact_id})` : ""}, replacing{" "}
                <span className="num">{formatFactValue(fact)}</span>.{" "}
              </>
            ) : null}
            Reason: &ldquo;{reason}&rdquo;
          </p>
        </div>
        <Button variant="ghost" size="icon" onClick={onDismiss} aria-label={`Dismiss the result for figure #${fact.id}`}>
          <XIcon aria-hidden="true" />
        </Button>
      </div>

      <div role="status" aria-live="polite" className="space-y-2">
        <RunStatus run={run} jobId={result.verdict_job.id} holdingName={holdingName} onRetry={retry} />
      </div>
      {run.outcome === "succeeded" && run.detail ? (
        <AffectedKpiList detail={run.detail} fact={fact} correctedId={result.corrected_fact_id} before={run.before} />
      ) : null}

      <div className="flex flex-wrap gap-3">
        <Link href={holdingHref} className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline">
          Open {holdingName} <ArrowRightIcon aria-hidden="true" className="size-3.5" />
        </Link>
        <Link href="/theses" className="text-accent underline-offset-4 hover:underline">
          Thesis board
        </Link>
      </div>
    </article>
  );
}

function RunStatus({
  run,
  jobId,
  holdingName,
  onRetry,
}: {
  run: RunState;
  jobId: number;
  holdingName: string;
  onRetry: () => void;
}) {
  if (run.outcome === "succeeded") {
    return <p className="font-medium">Verdicts recomputed (job #{jobId}).</p>;
  }
  if (run.outcome === "failed") {
    const error = run.detail?.last_verdict_run?.error;
    return (
      <p className="flex items-start gap-1.5">
        <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
        <span>
          <span className="font-medium">The verdict run failed</span> (job #{jobId}){error ? `: ${error}` : "."} Re-run
          verdicts from the holding page.
        </span>
      </p>
    );
  }
  if (run.timedOut) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex items-start gap-1.5">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
          The verdict run (job #{jobId}) has not finished after 30 s. The holding page shows the result once the worker
          picks it up.
        </p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon aria-hidden="true" />
          Check again
        </Button>
      </div>
    );
  }
  return (
    <>
      <p className="flex items-center gap-2 text-text-muted">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Re-judging {holdingName}&rsquo;s verdicts (job #{jobId}
        {run.detail?.last_verdict_run?.id === jobId ? `, ${run.detail.last_verdict_run.status}` : ""})…
      </p>
      {run.error ? <p className="text-xs text-text-muted">{run.error} Retrying.</p> : null}
    </>
  );
}

function AffectedKpiList({
  detail,
  fact,
  correctedId,
  before,
}: {
  detail: HoldingDetail;
  fact: FactOut;
  correctedId: number | null;
  before: Map<string, VerdictStatus | null> | undefined;
}) {
  if (detail.kpis.length === 0) {
    return <p className="text-text-muted">This holding&rsquo;s thesis has no KPIs, so no verdict depends on the figure.</p>;
  }
  const ids = correctedId === null ? [fact.id] : [fact.id, correctedId];
  const kpis = affectedKpis(detail.kpis, { metric: fact.metric, ids }, before);
  return (
    <ul className="divide-y divide-border rounded-md border border-border" aria-label="KPIs after the new verdict run">
      {kpis.map(({ kpi, current, before: prior, changed }) => (
        <li key={kpi.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
          <span className="min-w-40 font-medium">
            {humanizeMetric(kpi.metric)}
            <span className="block font-mono text-xs font-normal text-text-muted">{kpi.id}</span>
          </span>
          <span className="num text-text-muted">target {formatTarget(kpi.operator, kpi.threshold, kpi.unit)}</span>
          {current ? (
            <span className="num">
              {current.period ?? "—"} · {formatObserved(current.observed_value, kpi.unit)}
            </span>
          ) : null}
          <span className="inline-flex flex-wrap items-center gap-1">
            {changed ? (
              <>
                <span className="text-xs text-text-muted">was</span>
                <VerdictStatusBadge status={prior ?? "unknown"} />
                <ArrowRightIcon aria-hidden="true" className="size-3.5 text-text-muted" />
                <span className="sr-only">now</span>
              </>
            ) : null}
            <VerdictStatusBadge status={current?.status ?? "unknown"} />
            {current ? <ConfidenceBadge confidence={current.confidence} /> : null}
            {!current ? <span className="text-xs text-text-muted">{NEVER_JUDGED}</span> : null}
            {!changed && before !== undefined ? <span className="text-xs text-text-muted">(unchanged)</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}
