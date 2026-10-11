import { describe, expect, it } from "vitest";

import { changeText, isWorse, pointsChange, sharesText, stageText } from "./sizing";

describe("limit changes", () => {
  it("names each way a limit can move and flags only the worse ones", () => {
    expect(changeText("new")).toBe("New breach");
    expect(changeText("cleared")).toBe("Cleared");
    expect(changeText("something_else")).toBe("something_else");
    expect(isWorse("new")).toBe(true);
    expect(isWorse("deeper")).toBe(true);
    expect(isWorse("eased")).toBe(false);
    expect(isWorse("still_open")).toBe(false);
  });
});

describe("pointsChange", () => {
  it("shows the move in percentage points with a real minus sign", () => {
    expect(pointsChange(0.158, 0.141)).toBe("−1.7 pts");
    expect(pointsChange(0.1, 0.112)).toBe("+1.2 pts");
  });
  it("says no change for a negligible move and a dash for a missing side", () => {
    expect(pointsChange(0.15, 0.15001)).toBe("no change");
    expect(pointsChange(null, 0.1)).toBe("—");
    expect(pointsChange(0.1, undefined)).toBe("—");
  });
});

describe("labels", () => {
  it("labels the allocation stages", () => {
    expect(stageText("core_below_target")).toBe("Core below target");
    expect(stageText("core_fallback")).toBe("Core, nothing else ready");
  });
  it("counts whole shares", () => {
    expect(sharesText(1)).toBe("1 share");
    expect(sharesText(12)).toBe("12 shares");
    expect(sharesText(null)).toBe("—");
  });
});
