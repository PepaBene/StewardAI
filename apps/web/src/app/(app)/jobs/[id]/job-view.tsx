"use client";

import { CircleAlertIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";

import { AlertMessage } from "@/components/alert-message";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobDetail, type JobStepOut } from "@/lib/api/client";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import {
  durationBetween,
  formatCost,
  formatDuration,
  isTerminal,
  jobTypeLabel,
  modelsUsed,
  prettyJson,
  runError,
  totalCost,
} from "@/lib/jobs/job-detail";

const POLL_MS = 3000;
const POLL_LIMIT_MS = 10 * 60 * 1000;

/** One job run with its steps: follow an on-demand run to completion, or read why it failed. */
export function JobView({ id }: { id: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/jobs/{job_id}", { params: { path: { job_id: id } } }),
    [id],
  );
  const { state, reload } = useApiResource(fetcher);
  const job = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const since = useRef(0);

  // Re-read while the run is not finished (up to 10 minutes after the page opened).
  const running = job !== null && !isTerminal(job.status);
  useEffect(() => {
    if (!running) return;
    if (since.current === 0) since.current = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - since.current < POLL_LIMIT_MS) reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running, reload]);

  return (
    <div className="space-y-6">
      <p className="text-sm text-text-muted">
        <Link href="/activity#job-log" className="hover:text-text hover:underline">
          Activity
        </Link>{" "}
        / Job <span className="num">#{id}</span>
      </p>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading job…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.status === 404 ? `No job #${id}` : state.title}>
          <p>{state.status === 404 ? "The API has no job with this number." : state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}
      {job ? <Job job={job} running={running} onReload={reload} /> : null}
    </div>
  );
}

function Job({ job, running, onReload }: { job: JobDetail; running: boolean; onReload: () => void }) {
  const now = useNow(5_000);
  const cost = totalCost(job.steps);
  const models = modelsUsed(job.steps);
  const failure = runError(job);
  const params = prettyJson(job.params);
  const duration = durationBetween(job.started_at, job.finished_at ?? (running && job.started_at ? now.toISOString() : null));
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-2 page-title">
            {jobTypeLabel(job.job_type)} <span className="num font-normal text-text-muted">#{job.id}</span>
            <JobStatusBadge status={job.status} />
          </h1>
          <p className="mt-1 text-sm text-text-muted">
            <span className="font-mono">{job.job_type}</span> · {job.trigger === "manual" ? "started by hand" : `trigger: ${job.trigger}`}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={onReload}>
          {running ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
          {running ? "Following the run…" : "Reload"}
        </Button>
      </div>

      <dl className="grid max-w-3xl grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 rounded-lg border border-border bg-surface-raised p-4 text-sm">
        <dt className="text-text-muted">Queued</dt>
        <dd className="num">
          <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time> · {formatRelative(new Date(job.created_at), now)}
        </dd>
        <dt className="text-text-muted">Started</dt>
        <dd className="num">{job.started_at ? formatDateTime(job.started_at) : "Not yet"}</dd>
        <dt className="text-text-muted">Finished</dt>
        <dd className="num">{job.finished_at ? formatDateTime(job.finished_at) : running ? "Still running" : "—"}</dd>
        <dt className="text-text-muted">Duration</dt>
        <dd className="num">{duration === null ? "—" : formatDuration(duration)}</dd>
        <dt className="text-text-muted">Model cost</dt>
        <dd className="num" title={cost === null ? undefined : `${cost} USD`}>
          {cost === null ? "No model calls recorded" : formatCost(cost)}
          {models.length > 0 ? <span className="text-text-muted"> · {models.join(", ")}</span> : null}
        </dd>
      </dl>

      {failure ? (
        <AlertMessage title={failure.step ? `Failed at step “${failure.step}”` : "The run failed"}>
          <p className="font-mono text-xs whitespace-pre-wrap">{failure.error}</p>
        </AlertMessage>
      ) : null}

      {params ? (
        <section aria-labelledby="params-heading" className="space-y-2">
          <h2 id="params-heading" className="text-[22px] leading-tight font-semibold">
            Parameters
          </h2>
          <pre className="max-w-3xl overflow-x-auto rounded-md border border-border bg-surface-muted p-3 font-mono text-xs">{params}</pre>
        </section>
      ) : null}

      <section aria-labelledby="steps-heading" className="space-y-2">
        <h2 id="steps-heading" className="text-[22px] leading-tight font-semibold">
          Steps <span className="num text-sm font-normal text-text-muted">({job.steps.length})</span>
        </h2>
        {job.steps.length === 0 ? (
          <p className="text-sm text-text-muted">{running ? "No step has started yet." : "This run recorded no steps."}</p>
        ) : (
          <ol className="space-y-2">
            {job.steps.map((step) => (
              <Step key={step.step_index} step={step} now={now} />
            ))}
          </ol>
        )}
      </section>
    </>
  );
}

function Step({ step, now }: { step: JobStepOut; now: Date }) {
  const duration = durationBetween(step.started_at, step.finished_at ?? (step.status === "running" ? now.toISOString() : null));
  const detail = prettyJson(step.detail);
  return (
    <li className="space-y-2 rounded-lg border border-border bg-surface-raised p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex flex-wrap items-center gap-2">
          <span className="num text-text-muted">{step.step_index + 1}.</span>
          <span className="font-medium">{step.name}</span>
          <JobStatusBadge status={step.status} />
        </p>
        <p className="num flex flex-wrap gap-x-3 text-text-muted">
          <time dateTime={step.started_at}>{formatDateTime(step.started_at)}</time>
          <span>{duration === null ? "—" : formatDuration(duration)}</span>
          {step.model ? <span className="font-mono text-xs">{step.model}</span> : null}
          <span title={step.cost_usd === null ? "No model cost recorded" : `${step.cost_usd} USD`}>
            {step.cost_usd === null ? "no cost" : formatCost(step.cost_usd)}
          </span>
        </p>
      </div>
      {step.error ? (
        <p className="flex gap-1.5 text-text">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
          <span className="font-mono text-xs whitespace-pre-wrap">{step.error}</span>
        </p>
      ) : null}
      {detail ? (
        <details>
          <summary className="cursor-pointer text-text-muted hover:text-text">Detail</summary>
          <pre className="mt-2 max-h-96 overflow-auto rounded-md border border-border bg-surface-muted p-3 font-mono text-xs">{detail}</pre>
        </details>
      ) : null}
    </li>
  );
}
