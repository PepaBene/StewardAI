import { describe, expect, it } from "vitest";

import { ANSWER_FIXTURE, REPLAY_FIXTURE } from "@/lib/disclosure/fixtures-replay";
import { answerState, asOfFromDay, datasetCounts, leaksFuture, replayTotals } from "@/lib/disclosure/replay";

describe("replay", () => {
  it("turns a chosen day into the end of that day and rejects junk", () => {
    expect(asOfFromDay("2026-03-10")).toBe("2026-03-10T23:59:59Z");
    expect(asOfFromDay("10/03/2026")).toBeNull();
    expect(asOfFromDay("2026-13-40")).toBeNull();
  });

  it("keeps later changes out of the known half", () => {
    for (const d of Object.values(REPLAY_FIXTURE.holdings[0].datasets)) expect(leaksFuture(d, REPLAY_FIXTURE.as_of)).toBe(false);
    expect(REPLAY_FIXTURE.holdings[0].datasets.filings.since).toHaveLength(1);
  });

  it("flags a known item stamped after the date", () => {
    const d = { known: [{ label: "x", known_at: "2026-04-01T00:00:00Z", detail: "", ref: "1" }], since: [], leaked_dropped: 0, error: null };
    expect(leaksFuture(d, "2026-03-10T23:59:59Z")).toBe(true);
  });

  it("counts unreadable datasets instead of treating them as empty", () => {
    expect(replayTotals(REPLAY_FIXTURE)).toEqual({ known: 2, since: 2, unreadable: 1 });
    expect(datasetCounts(REPLAY_FIXTURE.holdings[0]).find((c) => c.key === "deals")?.error).toMatch(/Could not read/);
  });

  it("states an answer's grounding", () => {
    expect(answerState(ANSWER_FIXTURE)).toBe("grounded");
    expect(answerState({ ...ANSWER_FIXTURE, evidence: [] })).toBe("no_evidence");
    expect(answerState({ ...ANSWER_FIXTURE, grounded: false, text: null })).toBe("withheld");
  });
});
