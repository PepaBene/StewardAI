import type { JobDetail, JobStatus, JobStepOut } from "@/lib/api/client";
import { scheduleLabel } from "@/lib/config/jobs-form";

/*
 * Display rules for one job run (`GET /jobs/{job_id}`): its type, status, how long each
 * step took and what the model calls cost. Figures are the backend's; only summed here.
 */

export function jobHref(id: number): string {
  return `/jobs/${id}`;
}

/** Job types that are not schedules (queued by another job or by hand). */
const OTHER_JOB_LABELS: Record<string, string> = {
  earnings_check: "Earnings check",
  thesis_verdicts: "Thesis verdicts",
  event_escalation: "Event escalation",
};

export function jobTypeLabel(jobType: string): string {
  return OTHER_JOB_LABELS[jobType] ?? scheduleLabel(jobType);
}

export const JOB_STATUS: Record<JobStatus, { label: string; tone: "neutral" | "info" | "warning" }> = {
  queued: { label: "Queued", tone: "neutral" },
  waiting: { label: "Waiting for another run", tone: "neutral" },
  running: { label: "Running", tone: "info" },
  succeeded: { label: "Succeeded", tone: "neutral" },
  failed: { label: "Failed", tone: "warning" },
};

export function isTerminal(status: JobStatus): boolean {
  return status === "succeeded" || status === "failed";
}

/** "850 ms", "12 s", "3 min 4 s", "1 h 2 min". */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min${seconds % 60 ? ` ${seconds % 60} s` : ""}`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}

/** Time between two ISO timestamps, or null while the second is missing. */
export function durationBetween(start: string | null, end: string | null): number | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(ms) ? ms : null;
}

/** Sum of the steps' model cost; null when no step recorded one. */
export function totalCost(steps: Pick<JobStepOut, "cost_usd">[]): number | null {
  const costs = steps.map((s) => s.cost_usd).filter((c): c is number => typeof c === "number" && Number.isFinite(c));
  return costs.length === 0 ? null : costs.reduce((a, b) => a + b, 0);
}

/** "$0.0123": model costs are often fractions of a cent. */
export function formatCost(usd: number | null | undefined, locale?: string): string {
  if (typeof usd !== "number" || !Number.isFinite(usd)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: usd !== 0 && Math.abs(usd) < 1 ? 4 : 2,
  }).format(usd);
}

/** The models the run called, in step order, once each. */
export function modelsUsed(steps: Pick<JobStepOut, "model">[]): string[] {
  return [...new Set(steps.map((s) => s.model).filter((m): m is string => Boolean(m)))];
}

/** The error to show for the run: its own, else the first failed step's. */
export function runError(job: Pick<JobDetail, "error" | "steps">): { error: string; step: string | null } | null {
  if (job.error) return { error: job.error, step: null };
  const failed = job.steps.find((s) => s.status === "failed" && s.error);
  return failed ? { error: failed.error!, step: failed.name } : null;
}

/** Pretty JSON for a detail or params object; null when there is nothing in it. */
export function prettyJson(value: Record<string, unknown> | null | undefined): string | null {
  if (!value || Object.keys(value).length === 0) return null;
  return JSON.stringify(value, null, 2);
}
