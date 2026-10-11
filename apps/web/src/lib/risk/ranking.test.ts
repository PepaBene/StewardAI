import { describe, expect, it } from "vitest";

import { actionText, gateText, improvesText, missingText, scoreText, stageLabel } from "./ranking";

describe("ranking text", () => {
  it("names the action and the share switched", () => {
    expect(actionText({ action: "hold", share: 0, sell: "A", buy: "B" })).toBe("Keep A");
    expect(actionText({ action: "full", share: 1, sell: "A", buy: "B" })).toBe("Switch all of A into B");
    expect(actionText({ action: "partial", share: 0.5, sell: "A", buy: "B" })).toBe("Switch 50 % of A into B");
  });

  it("says why a row has no score", () => {
    expect(missingText({ score: 0.3, reasons: [] })).toBe("");
    expect(missingText({ score: null, reasons: ["no current price", "no cash rate"] })).toBe("No score: no current price; no cash rate.");
    expect(missingText({ score: null, reasons: [] })).toBe("No score.");
  });

  it("formats a score or shows a dash", () => {
    expect(scoreText(0.2819)).toBe("0.28");
    expect(scoreText(null)).toBe("—");
    expect(scoreText(Number.NaN)).toBe("—");
  });

  it("reads the hurdle and the asymmetry gate", () => {
    const returns = { meets_hurdle: true, meets_asymmetry: false } as never;
    expect(gateText({ returns })).toBe("hurdle met, loss too large for upside");
    expect(gateText({ returns: null })).toBe("—");
    expect(gateText({ returns: { meets_hurdle: null, meets_asymmetry: null } as never })).toBe("—");
  });

  it("labels the portfolio test and the risk stage", () => {
    expect(improvesText(true)).toBe("Improves the portfolio");
    expect(improvesText(false)).toBe("Does not improve the portfolio");
    expect(improvesText(null)).toBe("—");
    expect(stageLabel("A")).toBe("approximate");
    expect(stageLabel("B")).toBe("model");
    expect(stageLabel(null)).toBe("");
  });
});
