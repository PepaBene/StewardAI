"use client";

import { useEffect, useState } from "react";

import { getBrowserApiClient, type JobStatus, type JobSummary } from "@/lib/api/client";
import { isTerminal } from "@/lib/jobs/job-detail";

const POLL_MS = 3000;
const POLL_LIMIT_MS = 30 * 60 * 1000;

export type WatchedJob = {
  id: number;
  status: JobStatus;
  error: string | null;
  /** Polling stopped before the job finished (it may still finish: follow the job page). */
  gaveUp: boolean;
};

/**
 * Follows one queued job (`GET /jobs/{id}` every 3 s) until it succeeds or fails, for up to
 * 30 minutes. Pass null to follow nothing.
 */
export function useJobStatus(job: Pick<JobSummary, "id" | "status"> | null): WatchedJob | null {
  const [state, setState] = useState<WatchedJob | null>(null);
  const jobId = job?.id ?? null;
  const initial = job?.status ?? "queued";

  useEffect(() => {
    if (jobId === null) return;
    let cancelled = false;
    const since = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function tick() {
      try {
        const { data } = await getBrowserApiClient().GET("/jobs/{job_id}", { params: { path: { job_id: jobId! } } });
        if (cancelled) return;
        if (data) {
          setState({ id: jobId!, status: data.status, error: data.error, gaveUp: false });
          if (isTerminal(data.status)) return;
        }
      } catch {
        // Unreachable for a moment: keep trying until the limit.
      }
      if (cancelled) return;
      if (Date.now() - since > POLL_LIMIT_MS) {
        setState((s) => (s ? { ...s, gaveUp: true } : s));
        return;
      }
      timer = setTimeout(() => void tick(), POLL_MS);
    }
    timer = setTimeout(() => void tick(), POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, initial]);

  if (jobId === null) return null;
  return state && state.id === jobId ? state : { id: jobId, status: initial, error: null, gaveUp: false };
}
