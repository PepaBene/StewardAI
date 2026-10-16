import { describe, expect, it } from "vitest";

import { breadthSentence, changeWords, concentrationTier, issuerKey, moveTone, moveWords, netHolders, orderMoves, positionMoves, overlapWithHoldings, shortManager, signedPercent, valueChange } from "./investors";

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

  it("writes a tracked manager's move in a holding", () => {
    expect(shortManager("Pershing Square Capital Management, L.P.")).toBe("Pershing Square Capital");
    expect(shortManager("Bridgewater Associates, LP")).toBe("Bridgewater");
    expect(shortManager("Berkshire Hathaway Inc")).toBe("Berkshire Hathaway");
    expect(moveWords("added", 0.452)).toBe("added 45%");
    expect(moveWords("trimmed", -0.338)).toBe("trimmed 34%");
    expect(moveWords("added", 2757)).toBe("added more than 10×");
    expect(moveWords("sold", null)).toBe("sold out");
    expect(moveTone("trimmed")).toBe("loss");
    const ordered = orderMoves([
      { change: "unchanged" as const, share_change_pct: 0 },
      { change: "added" as const, share_change_pct: 0.05 },
      { change: "trimmed" as const, share_change_pct: -0.4 },
      { change: "sold" as const, share_change_pct: null },
    ]);
    expect(ordered.map((m) => m.change)).toEqual(["sold", "trimmed", "added", "unchanged"]);
  });

  it("counts holders who came and went", () => {
    expect(netHolders({ holders: 10, new_holders: 4, exited_holders: 1 })).toBe(3);
    expect(netHolders({ holders: 10 })).toBeNull();
    expect(
      breadthSentence([
        { holders: 1, new_holders: 3, exited_holders: 1 },
        { holders: 1, new_holders: 1, exited_holders: 5 },
        { holders: 1 },
      ]),
    ).toBe("More funds opened a position than closed one in 1 of your 2 US-listed holdings; more closed than opened in 1.");
  });
});
