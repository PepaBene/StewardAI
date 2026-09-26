import type { JobSummary } from "@/lib/api/client";
import { formatDateTime, formatRelative, formatTime } from "@/lib/format";

/*
 * "As of 14:05 · 3 h ago" labels for the news feed and every report view. The time is the
 * timestamp of the data on screen (the last news check, a digest's window end, a note's
 * creation), never the time the page was loaded. A stale number shown as current is worse
 * than an absent one, so each label says when the data stops being current:
 *
 * - When the API knows the schedule (`next_due_at` of a digest, `next_ingest_due_at` of the
 *   news feed), the data is current until the next run was due, plus a grace margin for the
 *   run itself. A Friday daily digest is therefore not stale over a weekday-only weekend.
 * - Otherwise (no schedule, or the job is disabled), a fixed age per kind applies.
 */

const HOUR = 3_600_000;

export type FreshnessKind = "news" | "daily_digest" | "weekly_digest" | "escalation";

/** Age after which the data is marked stale when no schedule is known, with the reason. */
export const STALE_AFTER: Record<FreshnessKind, { hours: number; why: string }> = {
  // The news check runs every 2 h from 06:00 to 22:00: the overnight gap is 8 h.
  news: { hours: 10, why: "the news check runs every 2 hours from 06:00 to 22:00" },
  // Written on weekdays at 07:00: older than a day and a bit is not today's digest.
  daily_digest: { hours: 26, why: "a daily digest is written every weekday morning" },
  weekly_digest: { hours: 8 * 24, why: "a weekly digest is written every Sunday" },
  // An impact note describes the event as it stood when written.
  escalation: { hours: 72, why: "an impact note describes the event as it stood when it was written" },
};

/**
 * Margin after a scheduled run's due time before the data counts as stale: the run needs
 * time to start and finish (a news check takes minutes, a digest a model call or two).
 * Null: the kind has no schedule.
 */
export const DUE_GRACE: Record<FreshnessKind, { minutes: number; what: string; missing: string } | null> = {
  news: { minutes: 30, what: "the next news check", missing: "no newer check has finished" },
  daily_digest: { minutes: 120, what: "the next daily digest", missing: "no newer digest has been written" },
  weekly_digest: { minutes: 120, what: "the next weekly digest", missing: "no newer digest has been written" },
  escalation: null,
};

export type Freshness = {
  /** ISO timestamp of the data. */
  at: string;
  /** "14:05" today, else "25 Sep 2026, 14:05". */
  asOf: string;
  /** "3 h ago". */
  age: string;
  ageHours: number;
  stale: boolean;
  /** "older than 10 h: the news check runs every 2 hours…", when stale. */
  staleReason: string | null;
  /** ISO time the next scheduled run was (or is) due, when known. */
  nextDueAt: string | null;
  /** "Next due 07:00" / "Next due 28 Sep 2026, 07:00", when known. */
  nextDue: string | null;
};

function formatThreshold(hours: number): string {
  return hours % 24 === 0 && hours >= 48 ? `${hours / 24} days` : `${hours} h`;
}

function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "14:05" when on the same day as `now`, else "25 Sep 2026, 14:05". */
function when(date: Date, now: Date, locale?: string): string {
  return date.toDateString() === now.toDateString() ? formatTime(date, locale) : formatDateTime(date, { locale });
}

/**
 * The freshness of data timestamped `at`, as seen at `now`. A timestamp in the future
 * (clock skew) counts as current. `forceStale` marks it stale whatever its age, with that
 * reason (e.g. the last run failed). `nextDueAt` (the next scheduled run after the one that
 * produced the data) replaces the fixed age threshold: stale only once it is more than the
 * kind's grace margin past. Returns null without a usable timestamp.
 */
export function freshness(
  at: string | null | undefined,
  kind: FreshnessKind,
  now: Date,
  { locale, forceStale, nextDueAt }: { locale?: string; forceStale?: string; nextDueAt?: string | null } = {},
): Freshness | null {
  const date = parseDate(at);
  if (!date) return null;
  const ageMs = Math.max(0, now.getTime() - date.getTime());
  const ageHours = ageMs / HOUR;
  const grace = DUE_GRACE[kind];
  const due = grace ? parseDate(nextDueAt) : null;

  let tooOld: boolean;
  let reason: string | null;
  if (due && grace) {
    tooOld = now.getTime() > due.getTime() + grace.minutes * 60_000;
    reason = tooOld ? `${grace.what} was due at ${when(due, now, locale)} and ${grace.missing}` : null;
  } else {
    const { hours, why } = STALE_AFTER[kind];
    tooOld = ageHours > hours;
    reason = tooOld ? `older than ${formatThreshold(hours)}: ${why}` : null;
  }

  return {
    at: date.toISOString(),
    asOf: when(date, now, locale),
    age: formatRelative(date, now),
    ageHours,
    stale: tooOld || Boolean(forceStale),
    staleReason: forceStale ?? reason,
    nextDueAt: due ? due.toISOString() : null,
    nextDue: due ? `Next due ${when(due, now, locale)}` : null,
  };
}

export function digestFreshnessKind(kind: string | null | undefined): FreshnessKind {
  return kind === "weekly" ? "weekly_digest" : "daily_digest";
}

/**
 * What the news feed is as of: the last news check. A finished check dates the feed; a
 * failed one leaves the feed as of an earlier check, so it is stale; a check still queued
 * or running is shown from when it started. `nextDueAt` is the feed's `next_ingest_due_at`.
 */
export function ingestFreshness(
  job: Pick<JobSummary, "status" | "created_at" | "started_at" | "finished_at" | "error"> | null,
  now: Date,
  locale?: string,
  nextDueAt?: string | null,
): (Freshness & { status: JobSummary["status"] }) | null {
  if (!job) return null;
  const at = job.finished_at ?? job.started_at ?? job.created_at;
  const forceStale = job.status === "failed" ? "the last news check failed" : undefined;
  const f = freshness(at, "news", now, { locale, forceStale, nextDueAt });
  return f ? { ...f, status: job.status } : null;
}

/** The words before the time of the news feed's freshness label. */
export function ingestPrefix(status: JobSummary["status"]): string {
  if (status === "succeeded") return "News as of";
  if (status === "failed") return "Last news check failed at";
  return `News check ${status} since`;
}
