import { describe, expect, it } from "vitest";

import { ageInDays, ageTone, BATCH_MIN, countByKind, groupQueue, isInboxKind, toQueueRows, type QueueRow } from "./inbox-queue";

const NOW = Date.parse("2026-10-16T12:00:00Z");
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString();

function row(over: Partial<QueueRow>): QueueRow {
  return {
    id: "x:1",
    kind: "proposals",
    ticker: "AAPL_US_EQ",
    name: null,
    headline: "h",
    why: null,
    createdAt: null,
    ageDays: 0,
    href: "/inbox",
    batchKey: null,
    ...over,
  };
}

describe("ageInDays / ageTone", () => {
  it("floors whole days and ignores bad dates", () => {
    expect(ageInDays(daysAgo(3.5), NOW)).toBe(3);
    expect(ageInDays("nope", NOW)).toBeNull();
    expect(ageInDays(null, NOW)).toBeNull();
  });
  it("warns at a week and flags a loss at two", () => {
    expect(ageTone(6)).toBe("none");
    expect(ageTone(7)).toBe("warning");
    expect(ageTone(14)).toBe("loss");
    expect(ageTone(null)).toBe("none");
  });
});

describe("toQueueRows", () => {
  it("normalises every source and skips acknowledged alerts", () => {
    const rows = toQueueRows(
      {
        escalations: [{ id: 4, ticker: "MSFT_US_EQ", title: "Guidance cut", created_at: daysAgo(9) } as never],
        alerts: [
          { id: 1, kind: "insider_trade", ticker: "AAPL_US_EQ", headline: "CEO sold", created_at: daysAgo(1), acknowledged_at: null } as never,
          { id: 2, kind: "insider_trade", ticker: "AAPL_US_EQ", headline: "old", created_at: daysAgo(1), acknowledged_at: daysAgo(0) } as never,
        ],
        needsYou: { count: 1, items: [{ id: 7, kind: "journal", label: "Write down why", href: "/journal", ticker: null, age_days: 2, recommendation: "", reasons: "" } as never] },
      },
      NOW,
    );
    expect(rows.map((r) => r.id)).toEqual(["escalations:4", "needs-you:journal:7", "disclosure-alerts:1"]);
    expect(rows[0]).toMatchObject({ kind: "escalations", ageDays: 9, href: "/inbox?kind=escalations&item=4" });
    expect(rows[1].why).toBeNull();
  });
});

describe("toQueueRows: AI decisions and stopped jobs", () => {
  it("lists every AI decision and only failed jobs from the last week", () => {
    const rows = toQueueRows(
      {
        aiDecisions: {
          days: 7,
          listings: [],
          peers: [{ id: 3, ticker: "AAPL_US_EQ", peer: "MSFT", status: "approved", reason: "Same market", at: daysAgo(2) }],
          pages: [],
          facts: [],
          disclosures: [],
          exposures: [],
        },
        failedJobs: [
          { id: 9, job_type: "research", error: "Timed out", created_at: daysAgo(1), finished_at: daysAgo(1), params: { ticker: "AAPL_US_EQ" } } as never,
          { id: 8, job_type: "research", error: "old", created_at: daysAgo(30), finished_at: daysAgo(30), params: {} } as never,
        ],
      },
      NOW,
    );
    expect(rows.map((r) => r.id)).toEqual(["ai-decisions:peer:3", "stopped-jobs:9"]);
    expect(rows[0]).toMatchObject({ headline: "Peer MSFT approved", why: "Same market" });
    expect(rows[1]).toMatchObject({ ticker: "AAPL_US_EQ", href: "/jobs/9", why: "Timed out" });
  });
});

describe("groupQueue", () => {
  it("folds repeated prompts into one batch across companies", () => {
    const prompts = Array.from({ length: BATCH_MIN }, (_, i) =>
      row({ id: `needs-you:j:${i}`, kind: "needs-you", ticker: `T${i}`, batchKey: "needs-you:journal" }),
    );
    const { batches, companies } = groupQueue([...prompts, row({ id: "escalations:1", kind: "escalations", ticker: "T0" })]);
    expect(batches).toHaveLength(1);
    expect(batches[0]).toMatchObject({ type: "batch", rows: prompts });
    expect(companies.map((c) => c.ticker)).toEqual(["T0"]);
  });

  it("keeps small groups as rows, oldest company first, company-less last", () => {
    const { batches, companies } = groupQueue([
      row({ id: "a", ticker: "A", ageDays: 1, batchKey: "k" }),
      row({ id: "b", ticker: "B", ageDays: 20, batchKey: "k" }),
      row({ id: "c", ticker: null, ageDays: 50, kind: "deal-parties" }),
    ]);
    expect(batches).toHaveLength(0);
    expect(companies.map((c) => c.ticker)).toEqual(["B", "A", null]);
  });

  it("orders rows inside a company by kind", () => {
    const { companies } = groupQueue([
      row({ id: "1", kind: "disputed" }),
      row({ id: "2", kind: "proposals" }),
    ]);
    expect(companies[0].entries.map((e) => (e.type === "row" ? e.row.kind : e.kind))).toEqual(["proposals", "disputed"]);
  });
});

describe("countByKind / isInboxKind", () => {
  it("counts every kind, zero included", () => {
    const c = countByKind([row({ kind: "figures" }), row({ kind: "figures" })]);
    expect(c.figures).toBe(2);
    expect(c.proposals).toBe(0);
  });
  it("accepts only known kinds", () => {
    expect(isInboxKind("figures")).toBe(true);
    expect(isInboxKind("bogus")).toBe(false);
    expect(isInboxKind(null)).toBe(false);
  });
});
