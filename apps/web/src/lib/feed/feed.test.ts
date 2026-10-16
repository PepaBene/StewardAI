import { describe, expect, it } from "vitest";

import { buildDays, countKinds, dayLabel, normaliseTitle } from "./feed";

const at = (day: number, hour = 12) => new Date(2026, 2, day, hour).toISOString();
const entry = (id: string, title: string, when: string, kind = "a") => ({ id, title, at: when, kind });

describe("feed helpers", () => {
  it("normalises titles", () => {
    expect(normaliseTitle("Fed holds  rates!")).toBe("fed holds rates");
  });

  it("folds repeated headlines within a day and keeps the newest as head", () => {
    const days = buildDays([entry("1", "Fed holds rates", at(4, 15)), entry("2", "fed holds rates.", at(4, 9)), entry("3", "Oil falls", at(4, 8))], new Date(2026, 2, 4, 18));
    expect(days).toHaveLength(1);
    expect(days[0].groups).toHaveLength(2);
    expect(days[0].groups[0].head.id).toBe("1");
    expect(days[0].groups[0].similar.map((e) => e.id)).toEqual(["2"]);
  });

  it("does not fold the same headline across days", () => {
    const days = buildDays([entry("1", "Fed holds rates", at(4)), entry("2", "Fed holds rates", at(3))], new Date(2026, 2, 4, 18));
    expect(days.map((d) => d.groups.length)).toEqual([1, 1]);
  });

  it("can turn folding off", () => {
    const days = buildDays([entry("1", "Same", at(4)), entry("2", "Same", at(4))], new Date(2026, 2, 4), false);
    expect(days[0].groups).toHaveLength(2);
  });

  it("labels today and yesterday", () => {
    const now = new Date(2026, 2, 4, 18);
    expect(dayLabel("2026-03-04", now)).toBe("Today");
    expect(dayLabel("2026-03-03", now)).toBe("Yesterday");
    expect(dayLabel("unknown", now)).toBe("Date unknown");
  });

  it("counts kinds in first-seen order", () => {
    expect(countKinds([entry("1", "a", at(1), "x"), entry("2", "b", at(1), "y"), entry("3", "c", at(1), "x")])).toEqual([
      { kind: "x", count: 2 },
      { kind: "y", count: 1 },
    ]);
  });
});
