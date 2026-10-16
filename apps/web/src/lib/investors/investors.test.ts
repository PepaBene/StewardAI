import { describe, expect, it } from "vitest";

import { changeWords, concentrationTier, issuerKey, positionMoves, overlapWithHoldings, signedPercent, valueChange } from "./investors";

describe("13F helpers", () => {
  it("tiers concentration by positions", () => {
    expect(concentrationTier(12)).toBe("concentrated");
    expect(concentrationTier(60)).toBe("focused");
    expect(concentrationTier(400)).toBe("broad");
  });

  it("computes the signed change against the previous quarter", () => {
    expect(valueChange("110", "100")).toBeCloseTo(0.1);
    expect(valueChange(90, 100)).toBeCloseTo(-0.1);
    expect(valueChange(90, 0)).toBeNull();
    expect(valueChange(null, 100)).toBeNull();
    expect(signedPercent(0.1)).toBe("+10.0%");
    expect(signedPercent(-0.052)).toBe("−5.2%");
    expect(signedPercent(null)).toBe("No prior quarter");
  });

  it("matches issuers to holdings by name ignoring legal suffixes", () => {
    expect(issuerKey("APPLE INC")).toBe("apple");
    expect(issuerKey("Microsoft Corp.")).toBe("microsoft");
    const rows = overlapWithHoldings(
      [
        { issuer_name: "APPLE INC", value_usd: 300 },
        { issuer_name: "TESLA INC", value_usd: 100 },
      ],
      new Map([["AAPL_US_EQ", "Apple"]]),
      400,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].ticker).toBe("AAPL_US_EQ");
    expect(rows[0].share).toBeCloseTo(0.75);
  });

  it("words and ranks quarter-on-quarter position moves", () => {
    expect(changeWords("new", null)).toBe("New");
    expect(changeWords("added", 0.12)).toBe("+12%");
    expect(changeWords("added", 0.125)).toBe("+12.5%");
    expect(changeWords("trimmed", -0.5)).toBe("−50%");
    expect(changeWords("unknown", null)).toBe("—");
    const m = positionMoves([
      { cusip: "a", issuer_name: "A", value_usd: 1, change: "added", share_change_pct: 0.1 },
      { cusip: "b", issuer_name: "B", value_usd: 1, change: "trimmed", share_change_pct: -0.6 },
      { cusip: "c", issuer_name: "C", value_usd: 1, change: "new", share_change_pct: null },
      { cusip: "d", issuer_name: "D", value_usd: 1, change: "unchanged", share_change_pct: 0 },
    ]);
    expect(m.bars.map((b) => b.label)).toEqual(["B", "A"]);
    expect(m.opened.map((p) => p.cusip)).toEqual(["c"]);
    expect(m.sentence).toBe("Among the largest 4 positions the manager added to 1, trimmed 1, opened 1, left 1 unchanged.");
  });
});
