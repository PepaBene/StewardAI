"use client";

import { LoaderCircleIcon, SendIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ReportSummary } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

type Period = "daily" | "weekly";

export function ReportsView() {
  const params = useSearchParams();
  const period: Period = params.get("period") === "weekly" ? "weekly" : "daily";
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/reports", { params: { query: { type: "digest", period } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) {
          setReports(data);
          setError(null);
        } else setError(`The API answered HTTP ${response.status}.`);
      })
      .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      cancelled = true;
    };
  }, [period]);

  async function sendNow() {
    const { data, response } = await getBrowserApiClient().POST("/jobs/digest", { body: { kind: period } });
    setQueued(
      data
        ? `The ${period} digest is being written (job #${data.id}). It appears here when done.`
        : `Could not queue: HTTP ${response.status}.`,
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Daily and weekly digests. Every sentence links to the news items or figures it was written
            from; a day with nothing material says so.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void sendNow()}>
          <SendIcon aria-hidden="true" />
          Write {period} digest now
        </Button>
      </div>
      {queued ? <AlertMessage tone="info" title="Queued">{queued}</AlertMessage> : null}

      <nav aria-label="Digest period" className="flex gap-1 border-b border-border">
        {(["daily", "weekly"] as const).map((p) => (
          <Link
            key={p}
            href={p === "daily" ? "/reports" : "/reports?period=weekly"}
            aria-current={p === period ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm capitalize",
              p === period ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
            )}
          >
            {p}
          </Link>
        ))}
      </nav>

      {error ? <AlertMessage title="Could not load reports">{error}</AlertMessage> : null}
      {reports === null && !error ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading…
        </p>
      ) : null}
      {reports?.length === 0 ? (
        <EmptyState title={`No ${period} digests yet`}>
          {period === "daily" ? "The daily digest is written on weekdays at 07:00." : "The weekly digest is written on Sundays at 18:00."}
        </EmptyState>
      ) : null}
      {reports && reports.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised">
          {reports.map((r) => (
            <li key={r.id}>
              <Link href={`/reports/${r.id}`} className="block space-y-1 p-4 hover:bg-surface-muted">
                <span className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{r.title}</span>
                  <time dateTime={r.created_at} className="num text-sm text-text-muted">
                    {formatDate(new Date(r.created_at))}
                  </time>
                </span>
                <span className="block text-sm text-text-muted">{r.headline}</span>
                <span className="flex flex-wrap gap-2 text-xs text-text-muted">
                  {Object.entries(r.delivery).map(([channel, d]) => (
                    <span key={channel} className="rounded-sm border border-border px-1.5 py-0.5">
                      {channel}: {d.status}
                    </span>
                  ))}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
