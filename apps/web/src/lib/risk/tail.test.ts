import { describe, expect, it } from "vitest";

import { badMonthSentence, horizonText, worstLine, yearsText, type TailLine } from "./tail";

const line = (over: Partial<TailLine>): TailLine => ({
  horizon_weeks: 4,
  level: 0.95,
  method: "historical",
  var: 0.05,
  es: 0.07,
  var_value: 50_000,
  es_value: 70_000,
  ...over,
});

describe("tail helpers", () => {
  it("names the horizons", () => {
    expect(horizonText(1)).toBe("one week");
    expect(horizonText(4)).toBe("one month");
    expect(horizonText(8)).toBe("8 weeks");
    expect(yearsText(1)).toBe("1 year");
    expect(yearsText(3)).toBe("3 years");
  });

  it("picks the method with the largest loss at a horizon and level", () => {
    const lines = [line({}), line({ method: "Cornish-Fisher", es: 0.09 }), line({ level: 0.99, es: 0.2 }), line({ horizon_weeks: 1, es: 0.5 })];
    expect(worstLine(lines, 4, 0.95)?.method).toBe("Cornish-Fisher");
    expect(worstLine(lines, 2, 0.95)).toBeNull();
  });

  it("writes the bad-month sentence with and without money", () => {
    expect(badMonthSentence([line({})], "CZK")).toBe(
      "In the worst one month in twenty, the book has lost about 7.0 % on average: 70,000 CZK at today's value.",
    );
    expect(badMonthSentence([line({ es_value: null })], "CZK")).toBe(
      "In the worst one month in twenty, the book has lost about 7.0 % on average.",
    );
    expect(badMonthSentence([], "CZK")).toBeNull();
    // Cornish-Fisher never sets the headline figure.
    expect(
      badMonthSentence([line({ es: 0.07 }), line({ method: "Cornish-Fisher", es: 0.2 })], null),
    ).toBe("In the worst one month in twenty, the book has lost about 7.0 % on average: 70,000 at today's value.");
  });
});
