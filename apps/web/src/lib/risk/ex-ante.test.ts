import { describe, expect, it } from "vitest";

import { barWidth, budgetStatus, coverageSentence, riskMultiple, sharedMax } from "./ex-ante";

describe("ex-ante helpers", () => {
  it("says how many times its size a holding's risk is", () => {
    expect(riskMultiple(0.06, 0.15)).toBeCloseTo(2.5);
    expect(riskMultiple(0, 0.2)).toBeNull();
  });

  it("keeps bar widths finite and inside the track", () => {
    expect(sharedMax([])).toBe(1);
    expect(sharedMax([0.1, 0.4])).toBe(0.4);
    expect(barWidth(0.2, 0.4)).toBe(50);
    expect(barWidth(2, 0.4)).toBe(100);
    expect(barWidth(-1, 0.4)).toBe(0);
    expect(barWidth(NaN, 0.4)).toBe(0);
  });

  it("describes coverage and leaves out parts that are zero", () => {
    const full = { invested_weight: 0.9, real_weight: 0.9, proxied_weight: 0, uncovered_weight: 0, covered_share: 1 };
    expect(coverageSentence(full)).toMatch(/^90\s?% of the portfolio has its own history\.$/);
    const mixed = { invested_weight: 0.9, real_weight: 0.6, proxied_weight: 0.2, uncovered_weight: 0.1, covered_share: 0.89 };
    const text = coverageSentence(mixed) ?? "";
    expect(text).toContain("backfilled");
    expect(text).toContain("left out");
    expect(coverageSentence(null)).toBeNull();
  });

  it("compares a risk share with its budget", () => {
    expect(budgetStatus(0.3, 0.25)).toBe("over");
    expect(budgetStatus(0.25, 0.25)).toBe("within");
    expect(budgetStatus(0.9, undefined)).toBe("unbudgeted");
  });
});
