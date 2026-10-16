"use client";

import { CircleAlertIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef } from "react";

import { AlertMessage } from "@/components/alert-message";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
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
      {!job ? (
        <PageHeader
          title={<>Job run <span className="num">#{id}</span></>}
          description={
            <Link href="/activity#job-log" className="text-accent-ink underline underline-offset-2">
              Back to Activity
            </Link>
          }
        />
      ) : null}
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
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {jobTypeLabel(job.job_type)} <span className="num font-normal text-text-muted">#{job.id}</span>
            <JobStatusBadge status={job.status} className="font-sans text-sm" />
          </span>
        }
        description={
          <>
            {job.trigger === "manual" ? "Started by hand" : "Started on schedule"} ·{" "}
            <Link href="/activity#job-log" className="text-accent-ink underline underline-offset-2">
              Back to Activity
            </Link>
          </>
        }
        aside={
          <Button variant="outline" size="sm" onClick={onReload}>
            {running ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
            {running ? "Following the run…" : "Reload"}
          </Button>
        }
      />

      <Blueprint as="div">
        <dl className="grid divide-border-soft sm:grid-cols-2 lg:grid-cols-5 lg:divide-x">
          <Tile label="Queued">
            <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time>
            <Sub>{formatRelative(new Date(job.created_at), now)}</Sub>
          </Tile>
          <Tile label="Started">{job.started_at ? formatDateTime(job.started_at) : "Not yet"}</Tile>
          <Tile label="Finished">{job.finished_at ? formatDateTime(job.finished_at) : running ? "Still running" : "—"}</Tile>
          <Tile label="Duration" big>
            {duration === null ? "—" : formatDuration(duration)}
          </Tile>
          <Tile label="Model cost" big>
            <span title={cost === null ? undefined : `${cost} USD`}>{cost === null ? "No model calls recorded" : formatCost(cost)}</span>
            {models.length > 0 ? <Sub>{models.join(", ")}</Sub> : null}
          </Tile>
        </dl>
      </Blueprint>

      {failure ? (
        <AlertMessage title={failure.step ? `This run stopped at the step “${failure.step}”` : "This run stopped with an error"}>
          <p>Nothing was lost; the next scheduled run tries again, or reload the page and start it by hand from Data sources.</p>
          <details className="mt-1 text-xs">
            <summary className="cursor-pointer">Details</summary>
            <p className="mt-1 font-mono break-words whitespace-pre-wrap">{failure.error}</p>
          </details>
        </AlertMessage>
      ) : null}

      {params ? (
        <Blueprint as="div" className="p-5">
          <details>
            <summary className="cursor-pointer text-lg font-semibold">Settings this run started with</summary>
            <pre className="mt-3 overflow-x-auto rounded-control bg-surface-muted p-3 font-mono text-xs">{params}</pre>
          </details>
        </Blueprint>
      ) : null}

      <Blueprint aria-labelledby="steps-heading" className="space-y-3 p-5">
        <h2 id="steps-heading" className="text-[22px] leading-tight font-semibold">
          Steps <span className="num text-sm font-normal text-text-muted">({job.steps.length})</span>
        </h2>
        {job.steps.length === 0 ? (
          <p className="text-sm text-text-muted">{running ? "No step has started yet." : "This run recorded no steps."}</p>
        ) : (
          <ol className="divide-y divide-border-soft border-t border-border-soft">
            {job.steps.map((step) => (
              <Step key={step.step_index} step={step} now={now} />
            ))}
          </ol>
        )}
      </Blueprint>
    </>
  );
}

function Step({ step, now }: { step: JobStepOut; now: Date }) {
  const duration = durationBetween(step.started_at, step.finished_at ?? (step.status === "running" ? now.toISOString() : null));
  const detail = prettyJson(step.detail);
  return (
    <li className="space-y-2 py-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{step.name.replaceAll("_", " ")}</span>
          <JobStatusBadge status={step.status} />
        </p>
        <p className="num flex flex-wrap gap-x-3 text-text-muted">
          <time dateTime={step.started_at}>{formatDateTime(step.started_at)}</time>
          <span>{duration === null ? "—" : formatDuration(duration)}</span>
          {step.model ? <span className="text-xs">{step.model}</span> : null}
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
          <pre className="mt-2 max-h-96 overflow-auto border border-border bg-surface-muted p-3 font-mono text-xs">{detail}</pre>
        </details>
      ) : null}
    </li>
  );
}

function Tile({ label, big, children }: { label: string; big?: boolean; children: React.ReactNode }) {
  return (
    <div className="space-y-1 border-b border-border-soft px-5 py-4 last:border-b-0 lg:border-b-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className={big ? "num font-heading text-[26px] leading-tight font-semibold" : "num text-sm font-medium"}>{children}</dd>
    </div>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return <span className="block font-sans text-xs font-normal text-text-muted">{children}</span>;
}
