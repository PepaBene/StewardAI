import { describe, expect, it } from "vitest";

import { parseAiDecisions, totalDecisions } from "./ai-decisions";

describe("parseAiDecisions", () => {
  it("reads every queue and skips malformed rows", () => {
    const d = parseAiDecisions({
      days: 14,
      listings: [{ ticker: "FB_US_EQ", status: "confirmed", decided_at: "2026-10-06T07:10:00Z", reason: "AI approval: ok" }, { status: "x" }],
      peers: [{ id: 4, ticker: "FB_US_EQ", peer_ticker: "PINS_US_EQ", status: "confirmed", reason: "AI approval", at: "2026-10-06" }, { id: "no" }],
      investor_pages: [{ ticker: "CSGa_EQ", attempted_at: "2026-10-06", applied: ["ir_url"] }, { ticker: "X", applied: { ir_url: "https://a" } }],
      web_facts: [{ id: 9, ticker: "AAA", slot: "next_report_date", value_text: "2027-02-18", tier: "T1", status: "approved", confidence: "0.9" }],
    });
    expect(d.days).toBe(14);
    expect(d.listings).toHaveLength(1);
    expect(d.peers).toEqual([{ id: 4, ticker: "FB_US_EQ", peer: "PINS_US_EQ", status: "confirmed", reason: "AI approval", at: "2026-10-06" }]);
    expect(d.pages[0]?.applied).toEqual(["ir_url"]);
    expect(d.pages[1]?.applied).toEqual(["ir_url: https://a"]);
    expect(d.facts[0]).toMatchObject({ id: 9, confidence: 0.9 });
    expect(totalDecisions(d)).toBe(5);
  });

  it("returns an empty summary for an unexpected response", () => {
    expect(totalDecisions(parseAiDecisions(null))).toBe(0);
    expect(parseAiDecisions({ listings: "no" }).listings).toEqual([]);
  });
});
