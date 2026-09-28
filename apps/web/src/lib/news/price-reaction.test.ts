import { describe, expect, it } from "vitest";

import { priceReaction, storyDay } from "./price-reaction";

const bars = [
  { date: "2026-09-21", close: 100 },
  { date: "2026-09-22", close: 101 },
  { date: "2026-09-23", close: 102 },
  { date: "2026-09-25", close: 99.96 },
  { date: "2026-09-24", close: 102 },
];

describe("priceReaction", () => {
  it("compares the last close before the story's day with the first on or after it", () => {
    const r = priceReaction(bars, "2026-09-25", { before: 2, after: 1 });
    expect(r).not.toBeNull();
    expect(r!.from).toBe("2026-09-24");
    expect(r!.to).toBe("2026-09-25");
    expect(r!.pct).toBeCloseTo(-0.02, 6);
    expect(r!.window).toEqual([102, 102, 99.96]);
  });

  it("uses the next session for a day without a close (weekend)", () => {
    const r = priceReaction([...bars, { date: "2026-09-28", close: 101 }], "2026-09-26");
    expect(r?.from).toBe("2026-09-25");
    expect(r?.to).toBe("2026-09-28");
    expect(priceReaction(bars, "2026-09-22")?.from).toBe("2026-09-21");
  });

  it("is null without a close before, or with none yet after", () => {
    expect(priceReaction(bars, "2026-09-21")).toBeNull();
    expect(priceReaction(bars, "2026-09-26")).toBeNull();
    expect(priceReaction([], "2026-09-26")).toBeNull();
  });
});

describe("storyDay", () => {
  it("is the calendar day in the given zone", () => {
    expect(storyDay("2026-09-26T23:30:00Z", "UTC")).toBe("2026-09-26");
    expect(storyDay("2026-09-26T23:30:00Z", "Europe/Prague")).toBe("2026-09-27");
  });
});
