import { describe, expect, it } from "vitest";

import { ackTimes, ageLabel, escalationActions, formatMinutes, parseStatusTab, replaceEscalation, weeklyTotals } from "./escalation";

describe("escalation lifecycle", () => {
  it("reads the status tab, open by default", () => {
    expect(parseStatusTab(null)).toBe("open");
    expect(parseStatusTab("resolved")).toBe("resolved");
    expect(parseStatusTab("all")).toBe("all");
    expect(parseStatusTab("bogus")).toBe("open");
  });

  it("offers the next steps from each state", () => {
    expect(escalationActions("open").map((a) => a.to)).toEqual(["acknowledged", "resolved"]);
    expect(escalationActions("acknowledged").map((a) => a.to)).toEqual(["resolved", "open"]);
    expect(escalationActions("resolved").map((a) => a.label)).toEqual(["Reopen"]);
  });

  it("formats ages and durations compactly", () => {
    const now = new Date("2026-09-27T12:00:00Z");
    expect(ageLabel("2026-09-27T11:15:00Z", now)).toBe("45m");
    expect(ageLabel("2026-09-27T09:00:00Z", now)).toBe("3h");
    expect(ageLabel("2026-09-25T09:00:00Z", now)).toBe("2d");
    expect(ageLabel("2026-09-28T09:00:00Z", now)).toBe("0m");
    expect(formatMinutes(130)).toBe("2h 10m");
    expect(formatMinutes(120)).toBe("2h");
    expect(formatMinutes(44.6)).toBe("45m");
    expect(formatMinutes(60 * 76)).toBe("3d 4h");
  });

  it("measures time to acknowledge, oldest first, skipping unacknowledged", () => {
    expect(
      ackTimes([
        { id: 2, created_at: "2026-09-26T10:00:00Z", acknowledged_at: "2026-09-26T10:30:00Z" },
        { id: 1, created_at: "2026-09-25T10:00:00Z", acknowledged_at: "2026-09-25T12:00:00Z" },
        { id: 3, created_at: "2026-09-27T10:00:00Z", acknowledged_at: null },
      ]),
    ).toEqual([
      { id: 1, createdAt: "2026-09-25T10:00:00Z", minutes: 120 },
      { id: 2, createdAt: "2026-09-26T10:00:00Z", minutes: 30 },
    ]);
  });

  it("sums per-day counts into full weeks ending today", () => {
    const perDay = Array.from({ length: 16 }, (_, i) => ({
      date: `2026-09-${String(i + 1).padStart(2, "0")}`,
      opened: 1,
      acknowledged: i % 2,
    }));
    const weeks = weeklyTotals(perDay);
    expect(weeks).toEqual([
      { start: "2026-09-03", opened: 7, acknowledged: 3 },
      { start: "2026-09-10", opened: 7, acknowledged: 4 },
    ]);
  });

  it("replaces one item in place", () => {
    expect(replaceEscalation([{ id: 1, s: "a" }, { id: 2, s: "b" }], { id: 2, s: "c" })).toEqual([
      { id: 1, s: "a" },
      { id: 2, s: "c" },
    ]);
  });
});
