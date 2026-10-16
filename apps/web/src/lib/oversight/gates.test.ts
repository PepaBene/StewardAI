import { describe, expect, it } from "vitest";

import { gateRule, kindName, precisionRows } from "./gates";

const gates = { min_n: 30, min_reviewed: 10, max_wrong_rate_increase: -0.01 };
const kind = (over: Partial<Parameters<typeof precisionRows>[0][number]>) => ({
  kind: "earnings_check",
  reviewed: 20,
  right: 19,
  precision: 0.95,
  enough_evidence: true,
  met: true,
  ...over,
});

describe("gateRule", () => {
  it("says the bar in words", () => {
    expect(gateRule(gates)).toBe(
      "Before the engine raises a confidence floor, it replays at least 30 past decisions, 10 of them checked by you or a second model, and the stricter floor must make wrong decisions at least 1 point rarer.",
    );
    expect(gateRule({ ...gates, max_wrong_rate_increase: 0 })).toContain("not make wrong decisions more common");
    expect(gateRule({ ...gates, max_wrong_rate_increase: 0.05 })).toContain("by more than 5 points");
  });
});

describe("precisionRows", () => {
  it("passes or fails a kind against the target once enough are reviewed", () => {
    const [pass, fail] = precisionRows([kind({}), kind({ kind: "news", precision: 0.8, right: 16, met: false })], 0.9, gates);
    expect(pass).toMatchObject({ state: "pass", lead: "Earnings check", sentence: "95% right (19 of 20 reviewed), target 90%." });
    expect(fail.state).toBe("fail");
  });

  it("leaves a kind unmeasured below the gate's reviewed minimum", () => {
    const [row] = precisionRows([kind({ reviewed: 4, right: 4, precision: 1 })], 0.9, gates);
    expect(row).toMatchObject({ state: "unknown", sentence: "4 reviewed so far; 6 more before it counts." });
  });

  it("names kinds in sentence case", () => {
    expect(kindName("engine_change")).toBe("Engine change");
  });
});
