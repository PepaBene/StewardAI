import { describe, expect, it } from "vitest";

import { areaVerdict, managers, parseValue, readingOrder, relativeChange } from "./changes";

const item = (area: "estimates" | "insiders" | "ownership", subject: string, what: string, before: string | null, after: string | null) => ({
  area,
  kind: "revised" as const,
  subject,
  what,
  before,
  after,
});

describe("what-changed values", () => {
  it("reads the numbers the feed writes", () => {
    expect(parseValue("4.979e+11")).toBe(4.979e11);
    expect(parseValue("-274,036")).toBe(-274036);
    expect(parseValue("+178")).toBe(178);
    expect(parseValue("2.03%")).toBe(2.03);
    expect(parseValue(null)).toBeNull();
    expect(relativeChange({ before: "20", after: "21" })).toBeCloseTo(0.05);
    expect(managers("5,405 managers, 4,347,147,316 shares (2026-03-31)")).toBe(5405);
  });
});

describe("area verdicts", () => {
  it("calls small estimate moves routine and names the material ones", () => {
    const small = [item("estimates", "GOOGL", "fmp eps estimate for FY2026", "20.61", "20.59")];
    expect(areaVerdict("estimates", small).text).toContain("none by 2% or more: routine drift");
    const big = [...small, item("estimates", "AMD", "fmp revenue estimate for FY2027", "100", "105")];
    const v = areaVerdict("estimates", big);
    expect(v.text).toContain("1 of 2 estimate changes moved 2% or more (1 up, 0 down)");
    expect(v.text).toContain("AMD revenue estimate for FY2027 +5.0%");
    expect(readingOrder("estimates", big)[0].subject).toBe("AMD");
  });

  it("counts insider net selling and names net buying", () => {
    const v = areaVerdict("insiders", [
      { ...item("insiders", "AZN", "Net", "-11,893", "+20"), name: "AstraZeneca" },
      item("insiders", "GOOGL", "Net", "-274,036", "-493,347"),
    ]);
    expect(v.tone).toBe("good");
    expect(v.text).toContain("net sellers at 1 of 2 holdings and net buyers at 1 (AstraZeneca)");
  });

  it("reads 13F holder counts, not share totals", () => {
    const v = areaVerdict("ownership", [
      item("ownership", "INTU", "13F", "1,921 managers, 1 shares (2026-03-31)", "1,653 managers, 1 shares (2026-06-30)"),
      item("ownership", "AMD", "13F", "3,060 managers, 1 shares (2026-03-31)", "3,827 managers, 1 shares (2026-06-30)"),
      item("ownership", "UBER", "13F", "2,324 managers, 1 shares (2026-03-31)", "2,303 managers, 1 shares (2026-06-30)"),
    ]);
    expect(v.text).toContain("rose by 5% or more at 1 holdings (AMD +25%) and fell at 1 (INTU −14%)");
  });
});
