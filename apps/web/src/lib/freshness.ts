import type { JobSummary } from "@/lib/api/client";
import { formatDateTime, formatRelative, formatTime } from "@/lib/format";

/*
 * "As of 14:05 · 3 h ago" labels for the news feed and every report view. The time is the
 * timestamp of the data on screen (the last news check, a digest's window end, a note's
 * creation), never the time the page was loaded, and each kind of data has the age after
 * which it is shown as stale: a stale number shown as current is worse than an absent one.
 */

const HOUR = 3_600_000;

export type FreshnessKind = "news" | "daily_digest" | "weekly_digest" | "escalation";

/** Age after which the data is marked stale, with the reason shown next to it. */
export const STALE_AFTER: Record<FreshnessKind, { hours: number; why: string }> = {
  // The news check runs every 2 h from 06:00 to 22:00: the overnight gap is 8 h.
  news: { hours: 10, why: "the news check runs every 2 hours from 06:00 to 22:00" },
  // Written on weekdays at 07:00: older than a day and a bit is not today's digest.
  daily_digest: { hours: 26, why: "a daily digest is written every weekday morning" },
  weekly_digest: { hours: 8 * 24, why: "a weekly digest is written every Sunday" },
  // An impact note describes the event as it stood when written.
  escalation: { hours: 72, why: "an impact note describes the event as it stood when it was written" },
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
};

function formatThreshold(hours: number): string {
  return hours % 24 === 0 && hours >= 48 ? `${hours / 24} days` : `${hours} h`;
}

/**
 * The freshness of data timestamped `at`, as seen at `now`. A timestamp in the future
 * (clock skew) counts as current. `forceStale` marks it stale whatever its age, with that
 * reason (e.g. the last run failed). Returns null without a usable timestamp.
 */
export function freshness(
  at: string | null | undefined,
  kind: FreshnessKind,
  now: Date,
  { locale, forceStale }: { locale?: string; forceStale?: string } = {},
): Freshness | null {
  if (!at) return null;
  const date = new Date(at);
  if (Number.isNaN(date.getTime())) return null;
  const ageMs = Math.max(0, now.getTime() - date.getTime());
  const ageHours = ageMs / HOUR;
  const { hours, why } = STALE_AFTER[kind];
  const tooOld = ageHours > hours;
  const sameDay = date.toDateString() === now.toDateString();
  return {
    at: date.toISOString(),
    asOf: sameDay ? formatTime(date, locale) : formatDateTime(date, { locale }),
    age: formatRelative(date, now),
    ageHours,
    stale: tooOld || Boolean(forceStale),
    staleReason: forceStale ?? (tooOld ? `older than ${formatThreshold(hours)}: ${why}` : null),
  };
}

export function digestFreshnessKind(kind: string): FreshnessKind {
  return kind === "weekly" ? "weekly_digest" : "daily_digest";
}

/**
 * What the news feed is as of: the last news check. A finished check dates the feed; a
 * failed one leaves the feed as of an earlier check, so it is stale; a check still queued
 * or running is shown from when it started.
 */
export function ingestFreshness(
  job: Pick<JobSummary, "status" | "created_at" | "started_at" | "finished_at" | "error"> | null,
  now: Date,
  locale?: string,
): (Freshness & { status: JobSummary["status"] }) | null {
  if (!job) return null;
  const at = job.finished_at ?? job.started_at ?? job.created_at;
  const forceStale = job.status === "failed" ? "the last news check failed" : undefined;
  const f = freshness(at, "news", now, { locale, forceStale });
  return f ? { ...f, status: job.status } : null;
}

/** The words before the time of the news feed's freshness label. */
export function ingestPrefix(status: JobSummary["status"]): string {
  if (status === "succeeded") return "News as of";
  if (status === "failed") return "Last news check failed at";
  return `News check ${status} since`;
}
