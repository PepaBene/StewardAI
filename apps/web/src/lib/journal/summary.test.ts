import { describe, expect, it } from "vitest";

import type { JournalEntry } from "./journal";
import { journalSummary } from "./summary";

const entry = (over: Partial<JournalEntry>): JournalEntry =>
  ({
    id: 1,
    scope: "security",
    ticker: "AAPL_US_EQ",
    decision: "buy",
    parent_id: null,
    decided_on: "2026-10-01",
    review_date: null,
    conviction: null,
    ...over,
  }) as JournalEntry;

describe("journalSummary", () => {
  it("counts decisions, due reviews and conviction share", () => {
    const s = journalSummary(
      [
        entry({ id: 1, review_date: "2026-10-10", conviction: 4 }),
        entry({ id: 2, review_date: "2026-10-10" }),
        entry({ id: 3, decision: "review", parent_id: 2, decided_on: "2026-10-12" }),
        entry({ id: 4, decided_on: "2025-01-01", review_date: "2026-12-01" }),
      ],
      "2026-10-16",
    );
    expect(s).toEqual({ decisions: 3, reviewsDue: 1, recent: 2, recentWithConviction: 0.5 });
  });

  it("has no conviction share without recent decisions", () => {
    expect(journalSummary([], "2026-10-16").recentWithConviction).toBeNull();
  });
});
