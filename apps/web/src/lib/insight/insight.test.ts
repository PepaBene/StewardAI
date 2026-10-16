import { describe, expect, it } from "vitest";

import {
  axisCoverage,
  barTotal,
  markMean,
  markPetals,
  monthBars,
  monthGrid,
  niceRound,
  petalsFromMap,
  scoreText,
  shiftMonth,
  sortChecks,
  stepValues,
  tickScale,
  yearBars,
} from "./insight";

const axis = (axisName: string, score: number | null, applicable = 6, na = 0, unknown = 0) => ({
  axis: axisName as "value",
  score,
  max: 6,
  applicable,
  na,
  unknown,
  passes: score ?? 0,
});

describe("score mark petals", () => {
  it("keeps the fixed axis order and never turns unknown into zero", () => {
    const petals = markPetals([axis("past", 3), axis("value", 6), axis("future", null, 0, 0, 6)]);
    expect(petals.map((p) => p.axis)).toEqual(["value", "future", "past", "health", "dividend"]);
    expect(petals[0].fraction).toBe(1);
    expect(petals[1].fraction).toBeNull();
    expect(petals[2].fraction).toBe(0.5);
    expect(petals[3].fraction).toBeNull();
  });

  it("flags an axis where nothing applies as not applicable", () => {
    const [, , , , dividend] = markPetals([axis("dividend", null, 0, 6)]);
    expect(dividend.notApplicable).toBe(true);
    expect(scoreText(axis("dividend", null, 0, 6))).toBe("n/a");
    expect(scoreText(axis("future", null, 0, 0, 6))).toBe("unknown");
    expect(scoreText(axis("past", 4.5))).toBe("4.5 / 6");
  });

  it("averages only known axes", () => {
    expect(markMean(petalsFromMap({ value: 6, past: 3 }))).toBeCloseTo(4.5);
    expect(markMean(petalsFromMap({}))).toBeNull();
  });

  it("names coverage in words", () => {
    expect(axisCoverage({ passes: 4, applicable: 5, unknown: 1, na: 0 })).toBe("4 of 5 applicable pass, 1 unknown");
  });
});

describe("checks", () => {
  it("sorts failed first and keeps order within a state", () => {
    const sorted = sortChecks([
      { state: "pass" as const, id: "a" },
      { state: "na" as const, id: "b" },
      { state: "fail" as const, id: "c" },
      { state: "unknown" as const, id: "d" },
      { state: "fail" as const, id: "e" },
    ]);
    expect(sorted.map((c) => c.id)).toEqual(["c", "e", "d", "a", "b"]);
  });
});

describe("forecast bars", () => {
  const months = [
    { month: "2026-08", phase: "history" as const, received: "10.5", declared: 0, estimated: 0, payments: [] },
    { month: "2026-11", phase: "forecast" as const, received: 0, declared: "4", estimated: "6.25", payments: [] },
  ];

  it("sums to the backend totals per phase", () => {
    const bars = monthBars(months);
    expect(barTotal(bars, "history")).toBeCloseTo(10.5);
    expect(barTotal(bars, "forecast")).toBeCloseTo(10.25);
    expect(bars[1]).toMatchObject({ known: 4, estimated: 6.25, label: "Nov 26" });
  });

  it("years carry the mixed phase", () => {
    const bars = yearBars([{ year: 2026, phase: "mixed", received: 5, declared: 1, estimated: 2 }]);
    expect(bars[0]).toMatchObject({ known: 6, estimated: 2, total: 8, phase: "mixed" });
  });
});

describe("month grid", () => {
  it("starts on Monday and covers the whole month", () => {
    const weeks = monthGrid("2026-10-01");
    expect(weeks[0][0].iso).toBe("2026-09-28");
    expect(weeks[0][3]).toMatchObject({ iso: "2026-10-01", inMonth: true });
    const days = weeks.flat().filter((d) => d.inMonth);
    expect(days).toHaveLength(31);
    expect(weeks.every((w) => w.length === 7)).toBe(true);
  });

  it("shifts months across years", () => {
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });
});

describe("step values", () => {
  it("holds the latest value and starts at the first record", () => {
    const v = stepValues(
      ["2026-01-01", "2026-01-05", "2026-01-10", "2026-01-20"],
      [
        { as_of: "2026-01-10", value: "120" },
        { as_of: "2026-01-05", value: 100 },
      ],
    );
    expect(v).toEqual([null, 100, 120, 120]);
  });
});

describe("tick scale", () => {
  const s = tickScale([0, 1, 10, 100]);
  it("gives each tick gap an equal share and round-trips", () => {
    expect(s.toPos(1)).toBeCloseTo(1 / 3);
    expect(s.toPos(10)).toBeCloseTo(2 / 3);
    expect(s.toValue(s.toPos(55))).toBeCloseTo(55);
    expect(s.toPos(-5)).toBe(0);
    expect(s.toPos(500)).toBe(1);
  });

  it("rounds to a step that fits the magnitude", () => {
    expect(niceRound(0.04321)).toBeCloseTo(0.045);
    expect(niceRound(1234.4)).toBe(1230);
  });
});
