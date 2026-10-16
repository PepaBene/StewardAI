import { describe, expect, it } from "vitest";

import { activityLevel, calendarWeeks, countByDay } from "./calendar";

describe("activity calendar", () => {
  it("counts per day, ignoring the time part and blanks", () => {
    const m = countByDay(["2026-10-14T09:00:00Z", "2026-10-14", null, "2026-10-15T23:59:00Z"]);
    expect(m.get("2026-10-14")).toBe(2);
    expect(m.get("2026-10-15")).toBe(1);
  });

  it("lays out Monday-first weeks ending with the week of the end date", () => {
    // 2026-10-16 is a Friday.
    const weeks = calendarWeeks(new Map([["2026-10-14", 3]]), "2026-10-16", 2);
    expect(weeks.map((w) => w.start)).toEqual(["2026-10-05", "2026-10-12"]);
    const last = weeks[1].days;
    expect(last[2]).toEqual({ date: "2026-10-14", count: 3, inRange: true });
    expect(last[4].inRange).toBe(true);
    expect(last[5].inRange).toBe(false);
  });

  it("steps activity into four levels of the busiest day", () => {
    expect(activityLevel(0, 4)).toBe(0);
    expect(activityLevel(1, 4)).toBe(1);
    expect(activityLevel(2, 4)).toBe(2);
    expect(activityLevel(3, 4)).toBe(3);
    expect(activityLevel(4, 4)).toBe(4);
  });
});
