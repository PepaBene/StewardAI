import { describe, expect, it } from "vitest";

import { dayLabel, eventTypeCounts, groupByDay, storyEventType } from "./feed-days";

const item = (id: number, at: string, type: string | null = null) => ({ published_at: at, event_type: null, story: { id, event_type: type } });

describe("groupByDay", () => {
  it("buckets consecutive stories by local day, keeping order", () => {
    const a = [item(1, "2026-10-16T10:00:00")];
    const b = [item(2, "2026-10-16T08:00:00")];
    const c = [item(3, "2026-10-15T20:00:00")];
    const days = groupByDay([a, b, c]);
    expect(days.map((d) => d.groups.length)).toEqual([2, 1]);
  });
});

describe("dayLabel", () => {
  const now = new Date("2026-10-16T12:00:00");
  it("names today and yesterday", () => {
    expect(dayLabel("2026-10-16T01:00:00", now)).toBe("Today");
    expect(dayLabel("2026-10-15T23:00:00", now)).toBe("Yesterday");
    expect(dayLabel("2026-10-12T09:00:00", now)).toBe("Mon 12 Oct");
  });
});

describe("eventTypeCounts / storyEventType", () => {
  it("counts per story, most common first, unclassified as empty key", () => {
    const groups = [[item(1, "x", "earnings")], [item(2, "x", "earnings")], [item(3, "x")]];
    expect(eventTypeCounts(groups)).toEqual([
      { type: "earnings", count: 2 },
      { type: "", count: 1 },
    ]);
    expect(storyEventType(groups[2])).toBeNull();
  });
});
