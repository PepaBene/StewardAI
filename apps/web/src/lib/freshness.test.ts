import { describe, expect, it } from "vitest";

import { digestFreshnessKind, freshness, ingestFreshness, ingestPrefix, STALE_AFTER } from "./freshness";

const L = "en-US";
const now = new Date("2026-09-25T12:00:00Z");
const hoursAgo = (h: number) => new Date(now.getTime() - h * 3_600_000).toISOString();

describe("freshness", () => {
  it("labels current data with its time and age", () => {
    const f = freshness(hoursAgo(3), "news", now, { locale: L })!;
    expect(f.age).toBe("3 h ago");
    expect(f.stale).toBe(false);
    expect(f.staleReason).toBeNull();
    expect(f.asOf).toMatch(/\d{1,2}:\d{2}/);
    expect(f.at).toBe(hoursAgo(3));
  });

  it("marks data stale once it is older than its kind's threshold", () => {
    expect(freshness(hoursAgo(9.9), "news", now)!.stale).toBe(false);
    const f = freshness(hoursAgo(11), "news", now)!;
    expect(f.stale).toBe(true);
    expect(f.staleReason).toBe(`older than 10 h: ${STALE_AFTER.news.why}`);
    expect(freshness(hoursAgo(25), "daily_digest", now)!.stale).toBe(false);
    expect(freshness(hoursAgo(27), "daily_digest", now)!.stale).toBe(true);
    expect(freshness(hoursAgo(7 * 24), "weekly_digest", now)!.stale).toBe(false);
    const weekly = freshness(hoursAgo(9 * 24), "weekly_digest", now)!;
    expect(weekly.stale).toBe(true);
    expect(weekly.staleReason).toMatch(/^older than 8 days/);
    expect(freshness(hoursAgo(80), "escalation", now)!.age).toBe("3 days ago");
  });

  it("shows the date of data from another day", () => {
    expect(freshness(hoursAgo(72), "escalation", now, { locale: L })!.asOf).toContain("2026");
  });

  it("forces stale with a reason, and treats a future timestamp as current", () => {
    const forced = freshness(hoursAgo(1), "news", now, { forceStale: "the last news check failed" })!;
    expect(forced).toMatchObject({ stale: true, staleReason: "the last news check failed" });
    const future = freshness(new Date(now.getTime() + 60_000).toISOString(), "news", now)!;
    expect(future).toMatchObject({ stale: false, age: "just now", ageHours: 0 });
  });

  it("returns null without a usable timestamp", () => {
    expect(freshness(null, "news", now)).toBeNull();
    expect(freshness("not a date", "news", now)).toBeNull();
  });

  it("picks the digest threshold by kind", () => {
    expect(digestFreshnessKind("weekly")).toBe("weekly_digest");
    expect(digestFreshnessKind("daily")).toBe("daily_digest");
  });
});

describe("news feed freshness", () => {
  const job = {
    status: "succeeded" as const,
    created_at: hoursAgo(2.2),
    started_at: hoursAgo(2.1),
    finished_at: hoursAgo(2),
    error: null,
  };

  it("dates the feed by the last finished news check", () => {
    expect(ingestFreshness(job, now)).toMatchObject({ status: "succeeded", age: "2 h ago", stale: false });
    expect(ingestFreshness(null, now)).toBeNull();
  });

  it("is stale when the last check failed, and dates a running one from its start", () => {
    expect(ingestFreshness({ ...job, status: "failed", error: "boom" }, now)).toMatchObject({
      stale: true,
      staleReason: "the last news check failed",
    });
    expect(ingestFreshness({ ...job, status: "running", finished_at: null }, now)!.at).toBe(job.started_at);
    expect(ingestFreshness({ ...job, status: "queued", started_at: null, finished_at: null }, now)!.at).toBe(job.created_at);
  });

  it("words the label by the check's status", () => {
    expect(ingestPrefix("succeeded")).toBe("News as of");
    expect(ingestPrefix("failed")).toBe("Last news check failed at");
    expect(ingestPrefix("running")).toBe("News check running since");
  });
});

describe("schedule-aware freshness", () => {
  // A Friday daily digest (window end Fri 25 Sep 05:00 UTC); daily digests run on weekdays,
  // so the next one is due on Monday 28 Sep 05:00 UTC.
  const friday = "2026-09-25T05:00:00Z";
  const monday = "2026-09-28T05:00:00Z";

  it("keeps a Friday daily digest current over the weekend", () => {
    for (const at of ["2026-09-26T09:00:00Z", "2026-09-27T20:00:00Z", "2026-09-28T06:59:00Z"]) {
      const f = freshness(friday, "daily_digest", new Date(at), { nextDueAt: monday })!;
      expect(f.ageHours).toBeGreaterThan(26);
      expect(f.stale).toBe(false);
      expect(f.staleReason).toBeNull();
      expect(f.nextDueAt).toBe(new Date(monday).toISOString());
      expect(f.nextDue).toMatch(/^Next due /);
    }
  });

  it("marks it stale once the next digest is more than 2 h overdue, saying when it was due", () => {
    const f = freshness(friday, "daily_digest", new Date("2026-09-28T07:01:00Z"), { nextDueAt: monday, locale: L })!;
    expect(f.stale).toBe(true);
    expect(f.staleReason).toMatch(/^the next daily digest was due at .+ and no newer digest has been written$/);
  });

  it("gives the news feed a 30 min grace after the next check was due", () => {
    const now = new Date("2026-09-25T12:00:00Z");
    const due = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();
    expect(freshness(hoursAgo(2.3), "news", now, { nextDueAt: due(29) })!.stale).toBe(false);
    const late = freshness(hoursAgo(2.3), "news", now, { nextDueAt: due(31), locale: L })!;
    expect(late.stale).toBe(true);
    expect(late.staleReason).toMatch(/^the next news check was due at \d{1,2}:\d{2}/);
    // Overnight: 11 h old, but the next check (06:00) is not due yet.
    expect(freshness(hoursAgo(11), "news", now, { nextDueAt: due(-30) })!.stale).toBe(false);
  });

  it("falls back to the fixed thresholds without a schedule", () => {
    expect(freshness(hoursAgo(27), "daily_digest", now, { nextDueAt: null })!.stale).toBe(true);
    expect(freshness(hoursAgo(27), "daily_digest", now, { nextDueAt: "garbage" })!.staleReason).toMatch(/^older than 26 h/);
    // Impact notes have no schedule: a due time is ignored.
    expect(freshness(hoursAgo(80), "escalation", now, { nextDueAt: hoursAgo(-10) })!.stale).toBe(true);
  });

  it("still forces stale when the last run failed", () => {
    const job = { status: "failed" as const, created_at: hoursAgo(1), started_at: hoursAgo(1), finished_at: hoursAgo(0.9), error: "boom" };
    expect(ingestFreshness(job, now, L, hoursAgo(-1))).toMatchObject({ stale: true, staleReason: "the last news check failed" });
    const ok = { ...job, status: "succeeded" as const, error: null };
    expect(ingestFreshness(ok, now, L, hoursAgo(-1))).toMatchObject({ stale: false });
    expect(ingestFreshness(ok, now, L, hoursAgo(1))!.stale).toBe(true);
  });
});
