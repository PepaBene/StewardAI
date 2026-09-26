import { describe, expect, it } from "vitest";

import { claimsNeedAttention, claimsSummaryText, sentenceCheck } from "./claims";

describe("claim checks", () => {
  it("shows unsupported and unchecked sentences prominently, with the verifier's reason", () => {
    expect(sentenceCheck({ check: "supported" })).toMatchObject({ kind: "supported", label: "Supported", prominent: false });
    expect(sentenceCheck({ check: "unsupported", check_reason: "number 12 % not in any snippet" })).toEqual({
      kind: "unsupported",
      label: "Unsupported",
      detail: "Unsupported: number 12 % not in any snippet",
      prominent: true,
    });
    expect(sentenceCheck({ check: "unchecked", check_reason: null })).toMatchObject({ kind: "unchecked", prominent: true });
    expect(sentenceCheck({ check: "unchecked" }).detail).toMatch(/could not check/);
    expect(sentenceCheck({})).toMatchObject({ kind: "none", prominent: false });
  });

  it("summarises the pass", () => {
    const claims = { mode: "strip" as const, supported: 4, unsupported: 0, unchecked: 0, removed: 2, note: null };
    expect(claimsSummaryText(claims)).toBe("4 supported · 0 unsupported · 0 unchecked · 2 removed");
    expect(claimsNeedAttention(claims)).toBe(true);
    expect(claimsSummaryText({ ...claims, removed: 0 })).toBe("4 supported · 0 unsupported · 0 unchecked");
    expect(claimsNeedAttention({ ...claims, removed: 0 })).toBe(false);
  });
});
