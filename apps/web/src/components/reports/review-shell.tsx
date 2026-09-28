"use client";

import { LoaderCircleIcon, PlayIcon, RefreshCwIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { AlertMessage } from "@/components/alert-message";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { ReportsShell, type ShellItem } from "@/components/reports/reports-shell";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobSummary, type ResearchSummary } from "@/lib/api/client";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { jobHref } from "@/lib/jobs/job-detail";
import { reviewHref } from "@/lib/review/monthly";

function monthOf(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(new Date(iso));
}

function reviewItems(reviews: readonly ResearchSummary[]): ShellItem[] {
  return reviews.map((r) => ({
    id: r.id,
    href: reviewHref(r.id),
    kind: "Monthly",
    dateLabel: monthOf(r.created_at),
    at: r.created_at,
    title: r.headline || r.title,
    meta: r.headline ? r.title : undefined,
  }));
}

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: ResearchSummary[] };

/**
 * The monthly-review pages' frame: the reviews (newest first) beside `children`, with
 * "Run review now" (and its job's live status), reload and the link to search reviews.
 */
export function ReviewShell({
  selectedId,
  children,
}: {
  selectedId: number | "latest";
  children: (reviews: ResearchSummary[] | null) => ReactNode;
}) {
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

  const items = load.kind === "ok" ? load.items : null;
  const written = finished && queued && items ? items.find((r) => r.job_id === queued.id) : undefined;
  const selected = selectedId === "latest" ? (items?.[0]?.id ?? null) : selectedId;

  return (
    <ReportsShell
      tab="reviews"
      items={items ? reviewItems(items) : null}
      error={load.kind === "error" ? load.message : null}
      selectedId={selected}
      emptyList={
        <EmptyState title="No monthly reviews yet">
          The review is written on the first weekday of each month (Config: Jobs, &ldquo;Monthly review&rdquo;), or now with
          &ldquo;Run review now&rdquo;.
        </EmptyState>
      }
      aside={
        <div className="flex flex-wrap justify-end gap-2">
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
          <Button
            size="sm"
            onClick={() => void runNow()}
            disabled={pending || (watched !== null && watched.status !== "succeeded" && watched.status !== "failed")}
          >
            {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
            Run review now
          </Button>
        </div>
      }
      notices={
        <>
          {queueError ? <AlertMessage title="Review not started">{queueError}</AlertMessage> : null}
          {queued && watched ? (
            <div role="status" className="flex flex-wrap items-center gap-2 border border-border bg-surface-muted p-3 text-sm">
              <span>Monthly review:</span>
              <JobStatusBadge status={watched.status} />
              <Link href={jobHref(queued.id)} className="text-accent-ink hover:underline">
                job #{queued.id}
              </Link>
              <span className="text-text-muted">A manual run is not limited to the first weekday.</span>
              {watched.status === "failed" && watched.error ? <span className="text-text">{watched.error}</span> : null}
              {written ? (
                <Link href={reviewHref(written.id)} className="font-medium text-accent-ink hover:underline">
                  Open the new review
                </Link>
              ) : null}
            </div>
          ) : null}
        </>
      }
    >
      {children(items)}
    </ReportsShell>
  );
}
