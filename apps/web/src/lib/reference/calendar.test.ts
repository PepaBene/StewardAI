import { describe, expect, it } from "vitest";

import { closureKey, isoToday, isPast, selectedClosures, sortClosures } from "./calendar";

const SUGGESTIONS = [
  { mic: "XNYS", date: "2026-11-26", name: "Thanksgiving Day" },
  { mic: "XETR", date: "2026-12-24", name: "Christmas Eve" },
  { mic: "XNYS", date: "2026-07-03", name: "Independence Day" },
];

describe("exchange calendar", () => {
  it("sorts closures by date, then MIC", () => {
    expect(sortClosures(SUGGESTIONS).map(closureKey)).toEqual([
      "XNYS:2026-07-03",
      "XNYS:2026-11-26",
      "XETR:2026-12-24",
    ]);
  });

  it("builds the confirm body from the selected suggestions only", () => {
    expect(selectedClosures(SUGGESTIONS, new Set())).toBeNull();
    expect(selectedClosures(SUGGESTIONS, new Set(["XETR:2026-12-24", "XNYS:2026-07-03", "XLON:2026-01-01"]))).toEqual({
      closures: [
        { mic: "XNYS", date: "2026-07-03", name: "Independence Day" },
        { mic: "XETR", date: "2026-12-24", name: "Christmas Eve" },
      ],
    });
  });

  it("keeps names within the config's 100-character limit", () => {
    const body = selectedClosures([{ mic: "XPRA", date: "2026-05-01", name: `  ${"x".repeat(120)}` }], new Set(["XPRA:2026-05-01"]));
    expect(body?.closures[0].name).toHaveLength(100);
  });

  it("tells past days from coming ones", () => {
    expect(isoToday(new Date(2026, 8, 5))).toBe("2026-09-05");
    expect(isPast("2026-09-04", "2026-09-05")).toBe(true);
    expect(isPast("2026-09-05", "2026-09-05")).toBe(false);
  });
});
