import { describe, expect, it } from "vitest";

import {
  CONFIDENCE,
  confidenceLabel,
  formatFactValue,
  formatObserved,
  formatTarget,
  humanizeMetric,
  tallyConfidence,
  tallyConfidenceLabel,
  VERDICT_STATUS,
} from "./confidence";

describe("confidence labels", () => {
  it("has exactly the four labels of the spec, none using gain or loss colours", () => {
    expect(Object.values(CONFIDENCE).map((c) => c.label)).toEqual([
      "Verified",
      "Corroborated",
      "Unconfirmed",
      "Disputed",
    ]);
    for (const c of Object.values(CONFIDENCE)) {
      expect(["gain", "loss"]).not.toContain(c.tone);
    }
    expect(confidenceLabel("unconfirmed")).toBe("Unconfirmed");
  });

  it("styles unknown apart from pass and breach", () => {
    expect(VERDICT_STATUS.pass.tone).toBe("gain");
    expect(VERDICT_STATUS.breach.tone).toBe("loss");
    expect(VERDICT_STATUS.unknown.tone).toBe("unknown");
  });

  it("tallies a list of confidence values and labels it in fixed order, omitting empty states", () => {
    const counts = tallyConfidence(["verified", "verified", "disputed", "verified"]);
    expect(counts).toEqual({ verified: 3, corroborated: 0, unconfirmed: 0, disputed: 1 });
    expect(tallyConfidenceLabel(counts)).toBe("3 verified · 1 disputed");
    expect(tallyConfidence([])).toEqual({ verified: 0, corroborated: 0, unconfirmed: 0, disputed: 0 });
    expect(tallyConfidenceLabel(tallyConfidence([]))).toBe("");
  });
});

describe("formatting", () => {
  it("formats targets and observed values", () => {
    expect(formatTarget(">=", 5, "%")).toBe(">= 5 %");
    expect(formatTarget(">", 0.1 + 0.2, "USD bn")).toBe("> 0.3 USD bn");
    expect(formatObserved(9.628455, "%")).toBe("9.63%");
    expect(formatObserved(23434, "USD m")).toBe("23434.00 USD m");
    expect(formatObserved(null, "%")).toBe("—");
    expect(humanizeMetric("revenue_growth_yoy")).toBe("Revenue growth YoY");
    expect(humanizeMetric("eps_diluted")).toBe("EPS diluted");
  });
});

describe("fact values", () => {
  it("shows the printed number with its scale and currency", () => {
    expect(formatFactValue({ value_as_written: "94,036", scale: "millions", unit: "currency", currency: "USD" })).toBe("USD 94,036 m");
    expect(formatFactValue({ value_as_written: "1.57", scale: "units", unit: "per_share", currency: "USD" })).toBe("USD 1.57 / share");
    expect(formatFactValue({ value_as_written: "46.5", scale: "units", unit: "percent", currency: null })).toBe("46.5%");
  });
});
