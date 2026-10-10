import { describe, expect, it } from "vitest";

import { basisPointsText, confidenceText, pValueText, signedPercent, testOutcome } from "./validation";

describe("validation text", () => {
  it("names the three confidence states and passes unknown ones through", () => {
    expect(confidenceText("ok")).toBe("Holds up");
    expect(confidenceText("reduced")).toBe("Trust it less");
    expect(confidenceText("untested")).toBe("Not enough history to test");
    expect(confidenceText("other")).toBe("other");
  });

  it("shows tiny p-values as <0.01 and missing ones as a dash", () => {
    expect(pValueText(0.0003)).toBe("<0.01");
    expect(pValueText(0.2345)).toBe("0.23");
    expect(pValueText(null)).toBe("—");
    expect(pValueText(Number.NaN)).toBe("—");
  });

  it("states a reconciliation error and signed shares with a true minus", () => {
    expect(basisPointsText(1e-9)).toBe("0.0 bp");
    expect(basisPointsText(-0.8)).toBe("-0.8 bp");
    expect(signedPercent(0.0123)).toBe("+1.2 %");
    expect(signedPercent(-0.05)).toBe("−5.0 %");
    expect(signedPercent(0.00001)).toBe("0.0 %");
    expect(signedPercent(undefined)).toBe("—");
    expect(testOutcome(true)).toBe("Passed");
    expect(testOutcome(false)).toBe("Failed");
  });
});
