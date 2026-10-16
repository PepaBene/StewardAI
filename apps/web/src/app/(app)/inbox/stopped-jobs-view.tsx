"use client";

import Link from "next/link";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EntityName } from "@/components/entity-name";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { STOPPED_JOB_DAYS } from "@/lib/inbox-queue";
import { jobTypeLabel } from "@/lib/jobs/job-detail";

const fetchFailed = () => getBrowserApiClient().GET("/jobs", { params: { query: { status: ["failed"], limit: 100 } } });

/** Inbox › Stopped jobs: runs that failed in the last week, each with its error and its run page. */
export function StoppedJobsView() {
  const { state } = useApiResource(fetchFailed);
  const [cutoff] = useState(() => Date.now() - STOPPED_JOB_DAYS * 86_400_000);
  const jobs = state.kind === "ok" ? state.data.jobs.filter((j) => Date.parse(j.finished_at ?? j.created_at) >= cutoff) : null;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Stopped jobs"
        description={`Runs that failed in the last ${STOPPED_JOB_DAYS} days. Open one to see the step it stopped at and run it again from its page.`}
      />
      {state.kind === "error" ? <AlertMessage title="Could not load the jobs">{state.detail}</AlertMessage> : null}
      {jobs && jobs.length === 0 ? (
        <p className="blueprint px-5 py-6 text-sm text-text-muted">No job has stopped in the last {STOPPED_JOB_DAYS} days.</p>
      ) : null}
      {jobs && jobs.length > 0 ? (
        <ul className="blueprint divide-y divide-border-soft overflow-hidden">
          {jobs.map((j) => {
            const ticker = typeof j.params?.ticker === "string" ? j.params.ticker : null;
            return (
              <li key={j.id}>
                <Link href={`/jobs/${j.id}`} className="grid gap-1 px-5 py-3 hover:bg-surface-muted md:grid-cols-[minmax(0,1fr)_auto] md:gap-4">
                  <span className="min-w-0">
                    <span className="block text-[15px] text-text">
                      {jobTypeLabel(j.job_type)}
                      {ticker ? (
                        <>
                          {" for "}
                          <EntityName id={ticker} chip={false} />
                        </>
                      ) : null}
                    </span>
                    {j.error ? <span className="mt-0.5 line-clamp-2 block text-[13px] text-text-muted">{j.error}</span> : null}
                  </span>
                  <span className="num text-xs text-text-faint">
                    {formatDateTime(j.finished_at ?? j.created_at)}
                    {j.attempts > 1 ? `, ${j.attempts} attempts` : ""}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
