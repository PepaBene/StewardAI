"use client";

import { ClipboardCheckIcon, LoaderCircleIcon, PlayIcon, RefreshCwIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { ReportsTabs } from "@/components/news/reports-tabs";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobSummary, type ResearchSummary } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { jobHref } from "@/lib/jobs/job-detail";
import { reviewHref } from "@/lib/review/monthly";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: ResearchSummary[] };

/** Monthly reviews, newest first, and "Run review now". */
export function ReviewsView() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [queued, setQueued] = useState<JobSummary | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const watched = useJobStatus(queued);
  const finished = watched?.status === "succeeded";

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/reviews", { params: { query: { limit: 100 } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, finished]);

  async function runNow() {
    setPending(true);
    setQueueError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/monthly-review");
      if (data) setQueued(data);
      else setQueueError(`Not queued: the API answered HTTP ${response.status}.`);
    } catch (err) {
      setQueueError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  const written = finished && queued && load.kind === "ok" ? load.items.find((r) => r.job_id === queued.id) : undefined;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Monthly reviews: every strategy rule, hard limit and band with pass, breach or unknown; each holding&rsquo;s
            thesis KPIs; flags; and proposed actions, each citing the rules or KPIs it rests on. Written on the first weekday
            of the month.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/reports?type=review">
              <SearchIcon aria-hidden="true" />
              Search reviews
            </Link>
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
          <Button size="sm" onClick={() => void runNow()} disabled={pending || (watched !== null && watched.status !== "succeeded" && watched.status !== "failed")}>
            {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
            Run review now
          </Button>
        </div>
      </div>

      {queueError ? <AlertMessage title="Review not started">{queueError}</AlertMessage> : null}
      {queued && watched ? (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-muted p-3 text-sm">
          <span>Monthly review:</span>
          <JobStatusBadge status={watched.status} />
          <Link href={jobHref(queued.id)} className="text-accent hover:underline">
            job #{queued.id}
          </Link>
          <span className="text-text-muted">A manual run is not limited to the first weekday.</span>
          {watched.status === "failed" && watched.error ? <span className="text-text">{watched.error}</span> : null}
          {written ? (
            <Link href={reviewHref(written.id)} className="font-medium text-accent hover:underline">
              Open the new review
            </Link>
          ) : null}
        </div>
      ) : null}

      <ReportsTabs active="reviews" />

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading reviews…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the reviews">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.items.length === 0 ? (
        <EmptyState title="No monthly reviews yet">
          The review is written on the first weekday of each month (Config: Jobs, &ldquo;Monthly review&rdquo;), or now with
          &ldquo;Run review now&rdquo;.
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.items.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised">
          {load.items.map((r) => (
            <li key={r.id}>
              <Link href={reviewHref(r.id)} className="block space-y-1 p-4 hover:bg-surface-muted">
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 font-medium">
                    <ClipboardCheckIcon aria-hidden="true" className="size-4 text-text-muted" />
                    {r.title}
                  </span>
                  <time dateTime={r.created_at} className="num text-sm text-text-muted">
                    {formatDateTime(r.created_at)}
                  </time>
                </span>
                {r.headline ? <span className="block text-sm text-text-muted">{r.headline}</span> : null}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
