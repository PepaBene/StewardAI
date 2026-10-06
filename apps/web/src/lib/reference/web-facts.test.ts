import { describe, expect, it } from "vitest";

import { decidedByOwner, parseWebFacts } from "./web-facts";

describe("parseWebFacts", () => {
  it("reads facts and ignores malformed rows", () => {
    const facts = parseWebFacts({
      facts: [
        {
          id: 3,
          slot: "next_report_date",
          value_text: "2027-02-18",
          tier: "T1",
          quote: "Results on 18 February 2027",
          source_url: "https://investors.example.com/calendar",
          host: "investors.example.com",
          retrieved_at: "2026-10-06T10:00:00Z",
          status: "approved",
          decided_by: "ai:claude-sonnet-5",
          confidence: "0.91",
          reasons: "T1 source",
        },
        { id: "x", slot: "bad", status: "approved" },
        { id: 4, slot: "shares_outstanding", status: "weird" },
        null,
      ],
    });
    expect(facts).toHaveLength(1);
    expect(facts[0]).toMatchObject({ id: 3, value: "2027-02-18", confidence: 0.91 });
    expect(decidedByOwner(facts[0]!)).toBe(false);
  });

  it("recognises an owner decision", () => {
    const [fact] = parseWebFacts({
      facts: [{ id: 1, slot: "next_report_date", status: "rejected", decided_by: "owner:abc" }],
    });
    expect(decidedByOwner(fact!)).toBe(true);
  });

  it("returns nothing for an unexpected response", () => {
    expect(parseWebFacts(null)).toEqual([]);
    expect(parseWebFacts({ facts: "no" })).toEqual([]);
  });
});
