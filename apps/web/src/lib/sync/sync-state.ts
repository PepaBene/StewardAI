/*
 * What the sync indicator shows, derived from the latest `job` / `job_step` rows the
 * frontend reads straight from Postgres (RLS: owner only) and follows via Realtime.
 */

export type JobStatus = "queued" | "waiting" | "running" | "succeeded" | "failed";

export type JobRow = {
  id: number;
  status: JobStatus;
  trigger: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
};

export type StepRow = {
  job_id: number;
  step_index: number;
  name: string;
  status: "running" | "succeeded" | "failed";
};

export const JOB_COLUMNS = "id,status,trigger,created_at,started_at,finished_at,error";
export const STEP_COLUMNS = "job_id,step_index,name,status";

/** Human names for the portfolio sync's steps (backend: steward/portfolio/sync.py). */
export const STEP_LABELS: Record<string, string> = {
  load_config: "Reading config",
  trading212_account: "Reading account",
  trading212_positions: "Reading positions",
  fx_rates: "Fetching FX rates",
  snapshot: "Saving snapshot",
  transactions: "Reading transactions",
  drift: "Checking drift",
};

export type SyncView =
  | { kind: "loading" }
  | { kind: "never" }
  | { kind: "active"; phase: "queued" | "waiting" | "running"; stepLabel: string | null; jobId: number }
  | {
      kind: "idle";
      lastSuccessAt: string | null;
      failure: { at: string; error: string | null } | null;
    };

export function describeSync(
  latest: JobRow | null,
  lastSuccess: JobRow | null,
  step: StepRow | null,
): SyncView {
  if (latest === null) return { kind: "never" };
  if (latest.status === "queued" || latest.status === "waiting" || latest.status === "running") {
    const current = step && step.job_id === latest.id ? step : null;
    return {
      kind: "active",
      phase: latest.status,
      stepLabel: current ? (STEP_LABELS[current.name] ?? current.name) : null,
      jobId: latest.id,
    };
  }
  const lastSuccessAt = lastSuccess?.finished_at ?? null;
  const failure =
    latest.status === "failed"
      ? { at: latest.finished_at ?? latest.created_at, error: latest.error }
      : null;
  return { kind: "idle", lastSuccessAt, failure };
}

export function isSyncActive(view: SyncView): boolean {
  return view.kind === "active";
}
