"use client";

import {
  ChevronDownIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  FileSearchIcon,
  LoaderCircleIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { DriftStatusBadge } from "@/components/portfolio/drift-status-badge";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { HoldingNews } from "@/components/news/holding-news";
import { StatCard } from "@/components/portfolio/stat-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type FactOut,
  type HoldingDetail,
  type JobSummary,
  type KpiStatus,
  type VerdictOut,
} from "@/lib/api/client";
import {
  formatFactValue,
  formatObserved,
  formatTarget,
  humanizeMetric,
} from "@/lib/evidence/confidence";
import { formatDate, formatMoney, formatPercent, formatRelative, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const POLL_MS = 3000;
const POLL_LIMIT_MS = 10 * 60 * 1000;
const TERMINAL = new Set(["succeeded", "failed"]);

export function HoldingView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const [watching, setWatching] = useState<{ jobId: number; since: number } | null>(null);
  const detail = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  // After queueing a job, re-read the page until that job has finished.
  const watchedJob = watching
    ? [detail?.last_earnings_check, detail?.last_verdict_run].find((j) => j?.id === watching.jobId)
    : undefined;
  const busy = watching !== null && !(watchedJob && TERMINAL.has(watchedJob.status));
  useEffect(() => {
    if (!busy || !watching) return;
    const timer = setInterval(() => {
      if (Date.now() - watching.since > POLL_LIMIT_MS) setWatching(null);
      else reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, watching, reload]);

  return (
    <div className="space-y-8">
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading {ticker}…
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
      {detail ? (
        <HoldingContent
          detail={detail}
          busy={busy}
          onQueued={(job) => {
            setWatching({ jobId: job.id, since: Date.now() });
            reload();
          }}
          reload={reload}
        />
      ) : null}
    </div>
  );
}

export function HoldingContent({
  detail,
  busy,
  onQueued,
  reload,
}: {
  detail: HoldingDetail;
  busy: boolean;
  onQueued: (job: JobSummary) => void;
  reload: () => void;
}) {
  const { instrument, thesis } = detail;
  const name = instrument?.name ?? detail.profile?.company_name ?? detail.ticker;
  const facts = useMemo(() => new Map(detail.facts.map((f) => [f.id, f])), [detail.facts]);

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/holdings" className="hover:text-text hover:underline">
              Holdings
            </Link>{" "}
            / <span className="font-mono">{detail.ticker}</span>
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
        </div>
        {detail.freshness ? (
          <FreshnessLabel syncedAt={detail.freshness.synced_at} snapshotDate={detail.freshness.snapshot_date} />
        ) : null}
      </div>

      <PositionCards detail={detail} />

      <JobActions detail={detail} busy={busy} onQueued={onQueued} />

      <section aria-labelledby="thesis-heading" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="thesis-heading" className="text-lg font-semibold">
            Thesis
          </h2>
          <Button asChild variant="ghost" size="sm">
            <Link href={`/config/theses#${encodeURIComponent(detail.ticker)}`}>
              <PencilIcon aria-hidden="true" />
              Edit thesis
            </Link>
          </Button>
        </div>
        {thesis ? (
          <div className="space-y-3 rounded-lg border border-border bg-surface-raised p-4 text-sm">
            <p className="whitespace-pre-line">{thesis.why_owned}</p>
            {thesis.exit_conditions && thesis.exit_conditions.length > 0 ? (
              <div>
                <p className="font-medium">Exit conditions</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-text-muted">
                  {thesis.exit_conditions.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {thesis.review_date ? (
              <p className="text-text-muted">Review by {formatDate(thesis.review_date)}</p>
            ) : null}
          </div>
        ) : (
          <EmptyState title="No thesis yet">
            Write why you own {detail.ticker} and the KPIs that must hold in the{" "}
            <Link className="text-accent underline-offset-4 hover:underline" href="/config/theses">
              theses editor
            </Link>
            .
          </EmptyState>
        )}
      </section>

      {thesis && thesis.kpis && thesis.kpis.length > 0 ? (
        <KpiTable kpis={detail.kpis} facts={facts} />
      ) : null}

      <HoldingNews ticker={detail.ticker} />

      <FactsSection facts={detail.facts} reload={reload} onQueued={onQueued} />
    </>
  );
}

function PositionCards({ detail }: { detail: HoldingDetail }) {
  const { position, instrument } = detail;
  const base = detail.base_currency ?? "";
  const tone = gainLossTone(position?.ppl);
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        label={`Value (${base || "base"})`}
        value={position ? formatMoney(position.value_base, base) : "Not held"}
        sub={position ? `${position.quantity} × ${position.current_price} ${position.instrument_currency}` : null}
      />
      <StatCard
        label="Weight"
        value={formatPercent(position?.weight)}
        sub={
          position ? (
            <span className="inline-flex items-center gap-1">
              target {formatPercent(position.target_weight)}
              <DriftStatusBadge status={position.drift_status} />
            </span>
          ) : null
        }
      />
      <StatCard
        label="Unrealised P/L"
        value={position ? formatMoney(position.ppl, position.account_currency, { signed: true }) : "—"}
        valueClassName={tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : ""}
        sub={position ? formatPercent(position.ppl_pct, { signed: true }) : null}
      />
      <StatCard
        label="Next report date"
        value={instrument?.next_report_date ? formatDate(instrument.next_report_date) : "Unknown"}
        sub={
          instrument?.report_date_source ? (
            <a
              href={instrument.report_date_source.startsWith("http") ? instrument.report_date_source : undefined}
              className="inline-flex items-center gap-1 hover:text-text hover:underline"
              target="_blank"
              rel="noreferrer"
            >
              source <ExternalLinkIcon aria-hidden="true" className="size-3" />
            </a>
          ) : instrument?.last_report_date ? (
            `last read ${formatDate(instrument.last_report_date)}`
          ) : (
            "set by the weekly calendar job"
          )
        }
      />
    </div>
  );
}

function JobLine({ label, job }: { label: string; job: JobSummary | null | undefined }) {
  if (!job) return null;
  const at = job.finished_at ?? job.started_at ?? job.created_at;
  return (
    <p className="text-sm text-text-muted">
      {label}: <span className="font-medium text-text">{job.status}</span>
      {" · "}
      <time dateTime={at}>{formatRelative(new Date(at))}</time>
      {job.status === "failed" && job.error ? <span className="block text-text">{job.error}</span> : null}
    </p>
  );
}

function JobActions({
  detail,
  busy,
  onQueued,
}: {
  detail: HoldingDetail;
  busy: boolean;
  onQueued: (job: JobSummary) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [showUrl, setShowUrl] = useState(false);
  const [url, setUrl] = useState("");
  const [reportDate, setReportDate] = useState("");

  async function run(kind: "check" | "verdicts") {
    setPending(true);
    setError(null);
    try {
      const api = getBrowserApiClient();
      const result =
        kind === "check"
          ? await api.POST("/jobs/earnings-check", {
              body: {
                ticker: detail.ticker,
                ...(url.trim() ? { url: url.trim() } : {}),
                ...(reportDate ? { report_date: reportDate } : {}),
              },
            })
          : await api.POST("/jobs/thesis-verdicts", { body: { ticker: detail.ticker } });
      if (result.data) onQueued(result.data);
      else setError(`Could not queue the job (HTTP ${result.response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-label="Checks" className="space-y-2 rounded-lg border border-border bg-surface-muted p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => void run("check")} disabled={pending || busy}>
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Run earnings check
        </Button>
        <Button size="sm" variant="outline" onClick={() => void run("verdicts")} disabled={pending || busy}>
          <RefreshCwIcon aria-hidden="true" />
          Re-run verdicts
        </Button>
        <Button size="sm" variant="ghost" aria-expanded={showUrl} onClick={() => setShowUrl((v) => !v)}>
          {showUrl ? <ChevronDownIcon aria-hidden="true" /> : <ChevronRightIcon aria-hidden="true" />}
          Use a specific document
        </Button>
      </div>
      {showUrl ? (
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
          <div className="space-y-1">
            <Label htmlFor="check-url">Release or report URL</Label>
            <Input id="check-url" type="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="check-date">Report date</Label>
            <Input id="check-date" type="date" value={reportDate} onChange={(e) => setReportDate(e.target.value)} />
          </div>
        </div>
      ) : null}
      <JobLine label="Last earnings check" job={detail.last_earnings_check} />
      <JobLine label="Last verdict run" job={detail.last_verdict_run} />
      {error ? <AlertMessage title="Not queued">{error}</AlertMessage> : null}
    </section>
  );
}

function KpiTable({ kpis, facts }: { kpis: KpiStatus[]; facts: Map<number, FactOut> }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section aria-labelledby="kpi-heading" className="space-y-3">
      <h2 id="kpi-heading" className="text-lg font-semibold">
        Thesis KPIs
      </h2>
      <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
        <table className="w-full min-w-[48rem] text-sm">
          <caption className="sr-only">Latest verdict per KPI; open a row for its evidence and history</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-text-muted">
              <th scope="col" className="px-3 py-2 text-left font-medium">KPI</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Target</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Observed</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Verdict</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {kpis.map(({ kpi, current, history }) => {
              const expanded = open === kpi.id;
              return (
                <KpiRows
                  key={kpi.id}
                  kpiId={kpi.id}
                  label={humanizeMetric(kpi.metric)}
                  target={formatTarget(kpi.operator, kpi.threshold, kpi.unit)}
                  unit={kpi.unit}
                  basis={kpi.basis}
                  current={current ?? null}
                  history={history}
                  expanded={expanded}
                  onToggle={() => setOpen(expanded ? null : kpi.id)}
                  facts={facts}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function KpiRows({
  kpiId,
  label,
  target,
  unit,
  basis,
  current,
  history,
  expanded,
  onToggle,
  facts,
}: {
  kpiId: string;
  label: string;
  target: string;
  unit: string;
  basis: string;
  current: VerdictOut | null;
  history: VerdictOut[];
  expanded: boolean;
  onToggle: () => void;
  facts: Map<number, FactOut>;
}) {
  const panelId = `kpi-${kpiId}`;
  return (
    <>
      <tr className="border-b border-border last:border-0">
        <th scope="row" className="px-3 py-2 text-left font-normal">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-controls={panelId}
            className="inline-flex items-start gap-1 rounded-sm text-left hover:text-accent"
          >
            {expanded ? (
              <ChevronDownIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            ) : (
              <ChevronRightIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            )}
            <span>
              <span className="block font-medium text-text">{label}</span>
              <span className="block font-mono text-xs text-text-muted">
                {kpiId} · {basis}
              </span>
            </span>
          </button>
        </th>
        <td className="num px-3 py-2 text-right">{target}</td>
        <td className="px-3 py-2">{current?.period ?? "—"}</td>
        <td className="num px-3 py-2 text-right">{formatObserved(current?.observed_value, unit)}</td>
        <td className="px-3 py-2">
          {current ? (
            <span className="inline-flex flex-wrap items-center gap-1">
              <VerdictStatusBadge status={current.status} />
              {current.stale ? (
                <span className="text-xs text-text-muted" title="Judged under an older theses config">
                  (old config)
                </span>
              ) : null}
            </span>
          ) : (
            <span className="text-text-muted">Not judged yet</span>
          )}
        </td>
        <td className="px-3 py-2">{current ? <ConfidenceBadge confidence={current.confidence} /> : null}</td>
      </tr>
      {expanded ? (
        <tr className="border-b border-border bg-surface-muted/60">
          <td colSpan={6} id={panelId} className="space-y-4 px-4 py-3">
            {current ? <VerdictEvidence verdict={current} facts={facts} /> : <p>No verdict yet.</p>}
            {history.length > 1 ? (
              <div>
                <p className="mb-1 text-xs font-medium tracking-wide text-text-muted uppercase">History</p>
                <ul className="divide-y divide-border rounded-md border border-border bg-surface-raised">
                  {history.map((v) => (
                    <li key={v.id} className="flex flex-wrap items-center gap-3 px-3 py-1.5">
                      <span className="w-24">{v.period ?? "—"}</span>
                      <span className="num w-28 text-right">{formatObserved(v.observed_value, unit)}</span>
                      <VerdictStatusBadge status={v.status} />
                      <ConfidenceBadge confidence={v.confidence} />
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function VerdictEvidence({ verdict, facts }: { verdict: VerdictOut; facts: Map<number, FactOut> }) {
  const used = verdict.fact_ids.map((id) => facts.get(id)).filter((f): f is FactOut => Boolean(f));
  return (
    <div className="space-y-2 text-sm">
      <p>{verdict.rationale}</p>
      {used.length === 0 ? (
        <p className="text-text-muted">No verified figure behind this verdict.</p>
      ) : (
        <ul className="space-y-2">
          {used.map((f) => (
            <li key={f.id}>
              <FactEvidence fact={f} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EvidenceLinks({ fact }: { fact: FactOut }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-3 text-xs">
      <Link
        href={`/evidence/${fact.id}`}
        className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline"
      >
        <FileSearchIcon aria-hidden="true" className="size-3.5" />
        Evidence
      </Link>
      <a
        href={fact.source_url}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1 text-text-muted hover:text-text hover:underline"
      >
        Source <ExternalLinkIcon aria-hidden="true" className="size-3" />
      </a>
    </span>
  );
}

function FactEvidence({ fact }: { fact: FactOut }) {
  return (
    <figure className="rounded-md border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {humanizeMetric(fact.metric)} {fact.period}
        </span>
        <span className="num">{formatFactValue(fact)}</span>
        <ConfidenceBadge confidence={fact.confidence} />
        <EvidenceLinks fact={fact} />
      </div>
      <blockquote className="mt-2 border-l-2 border-border pl-3 font-mono text-xs break-words text-text-muted">
        {fact.snippet}
        {fact.unit_snippet ? <span className="mt-1 block">{fact.unit_snippet}</span> : null}
      </blockquote>
    </figure>
  );
}

function FactsSection({
  facts,
  reload,
  onQueued,
}: {
  facts: FactOut[];
  reload: () => void;
  onQueued: (job: JobSummary) => void;
}) {
  const shown = facts.filter((f) => f.status !== "rejected");
  const rejected = facts.filter((f) => f.status === "rejected");
  return (
    <section aria-labelledby="facts-heading" className="space-y-3">
      <h2 id="facts-heading" className="text-lg font-semibold">
        Extracted figures
      </h2>
      {facts.length === 0 ? (
        <EmptyState title="No figures yet">
          Run an earnings check to read this company&rsquo;s latest results release.
        </EmptyState>
      ) : (
        <FactTable facts={shown} reload={reload} onQueued={onQueued} />
      )}
      {rejected.length > 0 ? (
        <details className="rounded-lg border border-border bg-surface-raised p-3 text-sm">
          <summary className="cursor-pointer font-medium">
            {rejected.length} figure{rejected.length === 1 ? "" : "s"} rejected by the validator
          </summary>
          <FactTable facts={rejected} reload={reload} onQueued={onQueued} />
        </details>
      ) : null}
    </section>
  );
}

function FactTable({
  facts,
  reload,
  onQueued,
}: {
  facts: FactOut[];
  reload: () => void;
  onQueued: (job: JobSummary) => void;
}) {
  if (facts.length === 0) return null;
  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface-raised">
      <table className="w-full min-w-[48rem] text-sm">
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-text-muted">
            <th scope="col" className="px-3 py-2 text-left font-medium">Period</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Metric</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Confidence</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Source</th>
          </tr>
        </thead>
        <tbody>
          {facts.map((f) => (
            <tr key={f.id} className="border-b border-border align-top last:border-0">
              <td className="px-3 py-2 whitespace-nowrap">{f.period}</td>
              <th scope="row" className="px-3 py-2 text-left font-normal">
                {humanizeMetric(f.metric)}
                {f.basis === "adjusted" ? <span className="ml-1 text-xs text-text-muted">(adjusted)</span> : null}
              </th>
              <td className="num px-3 py-2 text-right whitespace-nowrap">{formatFactValue(f)}</td>
              <td className="px-3 py-2">
                <ConfidenceBadge confidence={f.confidence} />
                {f.status_reason ? <p className="mt-1 max-w-xs text-xs text-text-muted">{f.status_reason}</p> : null}
                {f.status === "unverified" ? <ReviewButtons fact={f} reload={reload} onQueued={onQueued} /> : null}
              </td>
              <td className="px-3 py-2">
                <EvidenceLinks fact={f} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReviewButtons({
  fact,
  reload,
  onQueued,
}: {
  fact: FactOut;
  reload: () => void;
  onQueued: (job: JobSummary) => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const reasonId = `review-reason-${fact.id}`;
  async function review(action: "accept" | "reject") {
    // The API logs every decision with the owner's reason; it is required.
    if (!reason.trim()) {
      setError("Give a reason first.");
      return;
    }
    setPending(true);
    setError(null);
    const result = await getBrowserApiClient()
      .POST("/facts/{fact_id}/review", {
        params: { path: { fact_id: fact.id } },
        body: { action, reason: reason.trim() },
      })
      .catch((err: unknown) => ({ data: undefined, error: err, response: { status: 0 } as Response }));
    setPending(false);
    if (result.data) onQueued(result.data.verdict_job);
    else setError(`Review failed (HTTP ${result.response.status}).`);
    reload();
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <Label htmlFor={reasonId} className="sr-only">
        Reason for the decision
      </Label>
      <Input
        id={reasonId}
        value={reason}
        maxLength={500}
        placeholder="Reason"
        onChange={(e) => setReason(e.target.value)}
        className="h-8 w-40"
      />
      <Button size="sm" variant="outline" disabled={pending} onClick={() => void review("accept")}>
        Accept
      </Button>
      <Button size="sm" variant="ghost" disabled={pending} onClick={() => void review("reject")}>
        Reject
      </Button>
      {error ? <span className={cn("text-xs text-text")}>{error}</span> : null}
    </div>
  );
}
