import { describe, expect, it } from "vitest";

import { ageInDays, ageWords } from "./data-age-badge";

describe("ageInDays", () => {
  const today = new Date("2026-10-08T15:00:00Z");
  it("counts whole days and never goes negative", () => {
    expect(ageInDays("2026-10-08", today)).toBe(0);
    expect(ageInDays("2026-10-01", today)).toBe(7);
    expect(ageInDays("2026-10-20", today)).toBe(0);
  });
  it("is null for a missing or malformed date", () => {
    expect(ageInDays(null, today)).toBeNull();
    expect(ageInDays("yesterday", today)).toBeNull();
  });
  it("words the age", () => {
    expect(ageWords(0)).toBe("today");
    expect(ageWords(1)).toBe("1 day old");
    expect(ageWords(3)).toBe("3 days old");
    expect(ageWords(null)).toBe("unknown date");
  });
});
