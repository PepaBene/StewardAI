import { describe, expect, it } from "vitest";

import { expandCron, wallClockNow } from "./cron";

// Wednesday 14 October 2026, 08:00 wall-clock.
const WED = new Date(Date.UTC(2026, 9, 14, 8, 0));
const MIDNIGHT = new Date(Date.UTC(2026, 9, 14, 0, 0));

describe("expandCron", () => {
  it("expands a daily time over seven days", () => {
    const runs = expandCron("30 6 * * *", MIDNIGHT)!;
    expect(runs).toHaveLength(7);
    expect(runs[0]).toEqual({ day: 0, minute: 6 * 60 + 30 });
  });

  it("leaves out runs earlier than the start on the first day", () => {
    const runs = expandCron("30 6 * * *", WED)!;
    expect(runs.map((r) => r.day)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("handles weekday ranges (Mon-Fri)", () => {
    const runs = expandCron("0 7 * * 1-5", MIDNIGHT)!;
    // Wed, Thu, Fri, then Mon, Tue.
    expect(runs.map((r) => r.day)).toEqual([0, 1, 2, 5, 6]);
  });

  it("handles lists, steps and names", () => {
    expect(expandCron("0 */6 * * *", MIDNIGHT)!.filter((r) => r.day === 0).map((r) => r.minute / 60)).toEqual([0, 6, 12, 18]);
    expect(expandCron("15,45 9 * * sat,sun", MIDNIGHT)!.map((r) => [r.day, r.minute])).toEqual([
      [3, 555],
      [3, 585],
      [4, 555],
      [4, 585],
    ]);
    expect(expandCron("10-20/5 12 * * *", MIDNIGHT)!.filter((r) => r.day === 0).map((r) => r.minute - 720)).toEqual([10, 15, 20]);
  });

  it("treats 7 as Sunday", () => {
    expect(expandCron("0 0 * * 7", MIDNIGHT)!.map((r) => r.day)).toEqual([4]);
  });

  it("matches day-of-month or day-of-week when both are set", () => {
    // 15th of the month, or any Friday.
    const runs = expandCron("0 12 15 * 5", MIDNIGHT)!;
    expect(runs.map((r) => r.day)).toEqual([1, 2]);
  });

  it("honours a month list", () => {
    expect(expandCron("0 12 * 1 *", MIDNIGHT)).toEqual([]);
    expect(expandCron("0 12 * 10 *", MIDNIGHT)).toHaveLength(7);
  });

  it("rejects malformed expressions", () => {
    expect(expandCron("* * * *", MIDNIGHT)).toBeNull();
    expect(expandCron("61 * * * *", MIDNIGHT)).toBeNull();
    expect(expandCron("*/0 * * * *", MIDNIGHT)).toBeNull();
    expect(expandCron("a b c d e", MIDNIGHT)).toBeNull();
    expect(expandCron("5-1 * * * *", MIDNIGHT)).toBeNull();
  });
});

describe("wallClockNow", () => {
  it("reads the local time of a time zone", () => {
    const utc = new Date(Date.UTC(2026, 0, 15, 23, 30));
    const tokyo = wallClockNow("Asia/Tokyo", utc);
    expect([tokyo.getUTCDate(), tokyo.getUTCHours(), tokyo.getUTCMinutes()]).toEqual([16, 8, 30]);
  });
});
