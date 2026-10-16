"use client";

import { LandmarkIcon, LoaderCircleIcon, SendIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { DeliveryChips, ReportsShell, type ShellItem } from "@/components/reports/reports-shell";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ReportSummary } from "@/lib/api/client";
import { digestFreshnessKind } from "@/lib/freshness";
import { jobHref } from "@/lib/jobs/job-detail";
import { shortDay } from "@/lib/news/stats";

export type DigestPeriod = "daily" | "weekly";

function weekday(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { weekday: "short" }).format(new Date(iso));
}

function itemsMeta(r: ReportSummary): ReactNode {
  if (r.nothing_material) return "Nothing material";
  const parts: string[] = [];
  if (typeof r.story_count === "number") parts.push(`${r.story_count} ${r.story_count === 1 ? "item" : "items"}`);
  if (typeof r.citation_count === "number") parts.push(`${r.citation_count} ${r.citation_count === 1 ? "citation" : "citations"}`);
  return <span className="num">{parts.join(" · ") || "—"}</span>;
}

export function digestItems(reports: readonly ReportSummary[], period: DigestPeriod): ShellItem[] {
  return reports.map((r) => ({
    id: r.id,
    href: `/reports/${r.id}`,
    kind: period === "weekly" ? "Weekly" : "Daily",
    dateLabel: `${weekday(r.created_at)} ${shortDay(new Date(r.created_at))}`,
    at: r.created_at,
    title: r.headline || r.title,
    meta: itemsMeta(r),
    chips: <DeliveryChips delivery={r.delivery} />,
    quiet: !!r.nothing_material,
  }));
}

/**
 * The digest pages' frame: the list of digests of one period beside `children`, with
 * "Write digest now", "Collect macro data now" (both linking the queued job) and the
 * freshness of the latest digest with its next due time.
 */
export function DigestShell({
  period,
  selectedId,
  children,
}: {
  period: DigestPeriod;
  /** The digest shown on the right; "latest" follows the list's first. */
  selectedId: number | "latest";
  children: (reports: ReportSummary[] | null) => ReactNode;
}) {
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState<{ text: string; jobId: number } | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [pending, setPending] = useState<"digest" | "macro" | null>(null);

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

  // Queue one job, with the button disabled while the request is in flight (no duplicates).
  async function queueJob(kind: "digest" | "macro") {
    setPending(kind);
    setQueued(null);
    setQueueError(null);
    try {
      const api = getBrowserApiClient();
      const { data, response } =
        kind === "digest" ? await api.POST("/jobs/digest", { body: { kind: period } }) : await api.POST("/jobs/macro-collect");
      if (!data) setQueueError(`Could not queue: the API answered HTTP ${response.status}.`);
      else
        setQueued({
          text:
            kind === "digest"
              ? `The ${period} digest is being written (job #${data.id}). It appears here when done.`
              : `Macro data is being collected (job #${data.id}); the next digest lists the new figures.`,
          jobId: data.id,
        });
    } catch (err) {
      setQueueError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  const selected = selectedId === "latest" ? (reports?.[0]?.id ?? null) : selectedId;

  return (
    <ReportsShell
      tab={period}
      items={reports ? digestItems(reports, period) : null}
      error={error}
      selectedId={selected}
      emptyList={
        <EmptyState title={`No ${period} digests yet`}>
          {period === "daily" ? "The daily digest is written on weekdays at 07:00." : "The weekly digest is written on Sundays at 18:00."}
        </EmptyState>
      }
      aside={
        <div className="flex flex-col items-end gap-2">
          {reports && reports.length > 0 ? (
            <AsOfLabel
              at={reports[0].created_at}
              kind={digestFreshnessKind(period)}
              nextDueAt={reports[0].next_due_at}
              prefix={`Latest ${period} digest written`}
            />
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => void queueJob("macro")} disabled={pending !== null}>
              {pending === "macro" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <LandmarkIcon aria-hidden="true" />}
              Collect macro data now
            </Button>
            <Button variant="outline" size="sm" onClick={() => void queueJob("digest")} disabled={pending !== null}>
              {pending === "digest" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SendIcon aria-hidden="true" />}
              Write {period} digest now
            </Button>
          </div>
        </div>
      }
      notices={
        <>
          {queued ? (
            <AlertMessage tone="info" title="Queued">
              {queued.text}{" "}
              <Link href={jobHref(queued.jobId)} className="text-accent-ink hover:underline">
                Follow the job
              </Link>
            </AlertMessage>
          ) : null}
          {queueError ? <AlertMessage title="Not queued">{queueError}</AlertMessage> : null}
        </>
      }
    >
      {children(reports)}
    </ReportsShell>
  );
}
