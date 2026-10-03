import { describe, expect, it } from "vitest";

import { coverageShare, describeCoverage, stateWithCoverage } from "./provenance";
import { DATA_STATE_META, DATA_STATES, displayDecision, isDataState, parseDataState, toDataState } from "./states";

describe("data states", () => {
  it("defines every state the plan lists, each with a label and a description", () => {
    expect(DATA_STATES).toHaveLength(9);
    for (const s of DATA_STATES) {
      expect(DATA_STATE_META[s].label.length).toBeGreaterThan(2);
      expect(DATA_STATE_META[s].description.endsWith(".")).toBe(true);
    }
  });

  it("recognises state strings", () => {
    expect(isDataState("paid_only")).toBe(true);
    expect(isDataState("available")).toBe(false);
  });

  it("keeps the last good value when a refresh fails or quota runs out", () => {
    expect(displayDecision("unavailable", true)).toBe("last_good_with_notice");
    expect(displayDecision("quota_exhausted", true)).toBe("last_good_with_notice");
  });

  it("never draws a value for a structural denial", () => {
    for (const s of ["not_covered", "paid_only", "access_unverified"] as const) {
      expect(displayDecision(s, true)).toBe("placeholder");
    }
  });

  it("shows stale, partial and disputed values with a notice, and a placeholder without a value", () => {
    expect(displayDecision("stale", true)).toBe("value_with_notice");
    expect(displayDecision("disputed", true)).toBe("value_with_notice");
    expect(displayDecision("partial", false)).toBe("placeholder");
    expect(displayDecision("verified", true)).toBe("value");
    expect(displayDecision("verified", false)).toBe("placeholder");
  });
});

describe("coverage", () => {
  it("computes a share only with a usable denominator", () => {
    expect(coverageShare({ covered: 3, total: 4, unit: "holdings" })).toBe(0.75);
    expect(coverageShare({ covered: 3, total: 0, unit: "holdings" })).toBeNull();
    expect(coverageShare(null)).toBeNull();
    expect(coverageShare({ covered: 9, total: 4, unit: "x" })).toBe(1);
  });

  it("always names the denominator", () => {
    expect(describeCoverage({ covered: 18, total: 24, unit: "holdings" })).toBe("18 of 24 holdings (75%)");
    expect(describeCoverage(undefined)).toBe("No coverage denominator");
  });

  it("downgrades verified to partial below full coverage, and leaves other states alone", () => {
    expect(stateWithCoverage("verified", { covered: 1, total: 2, unit: "x" })).toBe("partial");
    expect(stateWithCoverage("verified", { covered: 2, total: 2, unit: "x" })).toBe("verified");
    expect(stateWithCoverage("paid_only", { covered: 0, total: 2, unit: "x" })).toBe("paid_only");
  });
});

describe("toDataState (backend vocabulary adapter)", () => {
  it("maps every backend coverage and access state", () => {
    expect(toDataState("available")).toBe("verified");
    expect(toDataState("temporarily_failed")).toBe("unavailable");
    expect(toDataState("vintage_unavailable")).toBe("not_covered");
    expect(toDataState("missing")).toBe("not_covered");
    expect(toDataState("never_succeeded")).toBe("access_unverified");
    expect(toDataState("recent")).toBe("verified");
    for (const s of ["paid_only", "quota_exhausted", "access_unverified", "disputed", "not_covered", "stale", "partial"] as const) {
      expect(toDataState(s)).toBe(s);
    }
  });

  it("never turns an unknown, empty or inherited key into verified", () => {
    for (const bad of ["", "OK", "constructor", "__proto__", "toString", null, undefined]) {
      expect(toDataState(bad)).toBe("unavailable");
      expect(parseDataState(bad)).toBeNull();
    }
  });
});
