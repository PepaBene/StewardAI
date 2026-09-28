import type { ActivityCoverage, JobListItem, ReferenceGaps } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { durationBetween, formatDuration } from "@/lib/jobs/job-detail";

import { JOB_TYPES } from "./activity";

/*
 * The Activity page's overview: the job-run grid (job types × days), the first-time rate,
 * the month's AI spend against its cap, and the four coverage ratios. Pure rules from
 * `GET /jobs`, `GET /costs/summary`, `GET /activity/coverage`, `GET /news/sources/health`
 * and `GET /reference/gaps`.
 */

type CostSummary = components["schemas"]["CostSummary"];
type SourcesHealth = components["schemas"]["SourcesHealth"];

export const GRID_DAYS = 21;

function dayKey(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/** The last `count` calendar days ending today, oldest first (YYYY-MM-DD). */
export function gridDays(now: Date, count = GRID_DAYS, timeZone?: string): string[] {
  const days: string[] = [];
  // Step back in 24 h steps from noon of today, which stays inside each calendar day across DST.
  const noon = new Date(`${dayKey(now, timeZone)}T12:00:00Z`);
  for (let i = count - 1; i >= 0; i--) days.push(new Date(noon.getTime() - i * 86_400_000).toISOString().slice(0, 10));
  return days;
}

export type GridCell = "ok" | "failed" | "retried" | "none";

export type JobGridRow = { jobType: string; cells: GridCell[]; last: JobListItem | null; runs: number };

/**
 * One row per job type that ran in the window (in the worker's registry order, unknown types
 * last), one cell per day: failed if any run that day failed, retried if any needed more than
 * one attempt, ok if a run succeeded, otherwise none (not scheduled, or only queued/running).
 */
export function jobGrid(jobs: readonly JobListItem[], days: readonly string[], timeZone?: string): JobGridRow[] {
  const index = new Map(days.map((d, i) => [d, i]));
  const rows = new Map<string, JobGridRow>();
  const rank = (s: GridCell) => ({ none: 0, ok: 1, retried: 2, failed: 3 })[s];
  for (const job of jobs) {
    const i = index.get(dayKey(new Date(job.created_at), timeZone));
    if (i === undefined) continue;
    let row = rows.get(job.job_type);
    if (!row) {
      row = { jobType: job.job_type, cells: days.map(() => "none" as GridCell), last: null, runs: 0 };
      rows.set(job.job_type, row);
    }
    row.runs += 1;
    const cell: GridCell =
      job.status === "failed" ? "failed" : job.status === "succeeded" ? (job.attempts > 1 ? "retried" : "ok") : "none";
    if (rank(cell) > rank(row.cells[i])) row.cells[i] = cell;
    if (!row.last || job.created_at > row.last.created_at) row.last = job;
  }
  const order = (t: string) => {
    const k = (JOB_TYPES as readonly string[]).indexOf(t);
    return k < 0 ? JOB_TYPES.length : k;
  };
  return [...rows.values()].sort((a, b) => order(a.jobType) - order(b.jobType) || a.jobType.localeCompare(b.jobType));
}

/** Share of finished runs that succeeded on their first attempt. Null when none finished. */
export function firstTimeRate(jobs: readonly Pick<JobListItem, "status" | "attempts">[]): { rate: number; finished: number } | null {
  const finished = jobs.filter((j) => j.status === "succeeded" || j.status === "failed");
  if (finished.length === 0) return null;
  const first = finished.filter((j) => j.status === "succeeded" && j.attempts <= 1).length;
  return { rate: first / finished.length, finished: finished.length };
}

/** "18:02 · 41s" for a run today, "26 Sep · 11m" before. */
export function lastRunNote(job: Pick<JobListItem, "created_at" | "started_at" | "finished_at">, now: Date, timeZone?: string): string {
  const at = new Date(job.started_at ?? job.created_at);
  const when =
    dayKey(at, timeZone) === dayKey(now, timeZone)
      ? new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hour12: false }).format(at)
      : (() => {
          const parts = new Intl.DateTimeFormat("en-US", { timeZone, day: "numeric", month: "short" }).formatToParts(at);
          return `${parts.find((p) => p.type === "day")?.value} ${parts.find((p) => p.type === "month")?.value}`;
        })();
  const ms = durationBetween(job.started_at, job.finished_at);
  return ms === null ? when : `${when} · ${formatDuration(ms)}`;
}

/** Oldest instant a paged job fetch still needs: midnight UTC of the grid's first day. */
export function gridStart(days: readonly string[]): string {
  return `${days[0]}T00:00:00Z`;
}

// ---------------------------------------------------------------------------
// AI spend
// ---------------------------------------------------------------------------

export type Spend = {
  total: number;
  cap: number | null;
  /** Share of the budget month elapsed (0..1). */
  elapsed: number;
  /** Linear month-end projection (total / elapsed); null too early in the month to say. */
  projected: number | null;
  /** Whether the projection passes the cap. */
  projectedOver: boolean;
};

export function spend(summary: Pick<CostSummary, "total_usd" | "cap_usd" | "period_start" | "period_end">, now: Date): Spend {
  const start = Date.parse(summary.period_start);
  const end = Date.parse(summary.period_end);
  const elapsed = end > start ? Math.min(1, Math.max(0, (now.getTime() - start) / (end - start))) : 1;
  const projected = elapsed >= 0.1 ? summary.total_usd / elapsed : null;
  return {
    total: summary.total_usd,
    cap: summary.cap_usd,
    elapsed,
    projected,
    projectedOver: projected !== null && summary.cap_usd !== null && projected > summary.cap_usd,
  };
}

// ---------------------------------------------------------------------------
// Coverage rings
// ---------------------------------------------------------------------------

export type CoverageRatio = { value: number; total: number; note: string };

export function priceCoverage(c: Pick<ActivityCoverage, "holdings">): CoverageRatio {
  const ok = c.holdings.filter((h) => h.price_symbol && !h.prices_gap);
  const missing = c.holdings.filter((h) => !h.price_symbol || h.prices_gap).map((h) => h.ticker);
  return { value: ok.length, total: c.holdings.length, note: missing.length ? `gap: ${missing.join(", ")}` : "every holding current" };
}

export function earningsCoverage(c: Pick<ActivityCoverage, "holdings">): CoverageRatio {
  const ok = c.holdings.filter((h) => h.earnings_last_status === "succeeded");
  const failed = c.holdings.filter((h) => h.earnings_last_status === "failed").length;
  const never = c.holdings.filter((h) => !h.earnings_last_check).length;
  const parts = [failed ? `${failed} failed` : null, never ? `${never} never checked` : null].filter(Boolean);
  return { value: ok.length, total: c.holdings.length, note: parts.length ? parts.join(", ") : "last check succeeded for each" };
}

export function sourceCoverage(h: Pick<SourcesHealth, "sources" | "window_hours">): CoverageRatio {
  const enabled = h.sources.filter((s) => s.enabled);
  return {
    value: enabled.filter((s) => s.healthy).length,
    total: enabled.length,
    note: `enabled feeds healthy (${h.window_hours} h)`,
  };
}

export function identityCoverage(c: Pick<ActivityCoverage, "holdings">, gaps: Pick<ReferenceGaps, "identity">): CoverageRatio {
  const bad = new Set(gaps.identity.map((g) => g.ticker));
  const held = c.holdings.map((h) => h.ticker);
  const unresolved = held.filter((t) => bad.has(t));
  return {
    value: held.length - unresolved.length,
    total: held.length,
    note: unresolved.length ? `unresolved: ${unresolved.join(", ")}` : "FIGI, LEI and ISIN resolved",
  };
}
