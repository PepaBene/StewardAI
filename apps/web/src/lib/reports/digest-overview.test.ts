import { describe, expect, it } from "vitest";

import type { DigestBody } from "@/lib/api/client";

import { digestChecks, digestDays, historyUpTo, localDay, telegramTimes, whatHappened, windowReturn } from "./digest-overview";

const points = [
  { date: "2026-09-21", close: 100 },
  { date: "2026-09-22", close: 102 },
  { date: "2026-09-24", close: 101 },
  { date: "2026-09-23", close: 104 },
  { date: "2026-09-25", close: 106.08 },
];

describe("historyUpTo", () => {
  it("takes the last n values on or before the day, oldest first", () => {
    expect(historyUpTo(points, "2026-09-24", 3)).toEqual([102, 104, 101]);
    expect(historyUpTo(points, "2026-09-20")).toEqual([]);
  });
});

describe("windowReturn", () => {
  it("runs from the last value before the window to the last one inside it", () => {
    const r = windowReturn(points, "2026-09-24", "2026-09-26");
    expect(r?.from).toBe("2026-09-23");
    expect(r?.to).toBe("2026-09-25");
    expect(r?.pct).toBeCloseTo(0.02, 10);
  });

  it("is null without a value before the window or with nothing new in it", () => {
    expect(windowReturn(points, "2026-09-21", "2026-09-22")).toBeNull();
    expect(windowReturn(points.slice(0, 1), "2026-09-22", "2026-09-23")).toBeNull();
  });
});

describe("days", () => {
  it("reads calendar days in the zone", () => {
    expect(localDay("2026-09-26T23:30:00Z", "Europe/Prague")).toBe("2026-09-27");
    expect(digestDays({ window_start: "2026-09-25T05:01:00Z", window_end: "2026-09-27T18:30:00Z" }, "UTC")).toEqual({
      start: "2026-09-25",
      end: "2026-09-27",
    });
  });
});

const body: DigestBody = {
  kind: "daily",
  window_start: "2026-09-25T05:00:00Z",
  window_end: "2026-09-26T05:00:00Z",
  nothing_material: false,
  headline: "h",
  disclosure_alerts_more: 0,
  alerts_sent: 2,
  dropped_sentences: 0,
  overview: [
    { text: "Novo cut guidance.", news_item_ids: [1], fact_ids: [] },
    { text: "Markets calm.", news_item_ids: [], fact_ids: [] },
  ],
  sections: [
    {
      ticker: "NOVOb_EQ",
      name: "Novo",
      summary: [{ text: "Growth 8-11%.", news_item_ids: [1, 2], fact_ids: [] }],
      stories: [{ cluster_id: 5, headline: "x", event_type: null, severity: 3, status: "confirmed", confidence: "verified", news_item_ids: [1, 2] }],
    },
    { ticker: null, name: "Markets and macro", summary: [{ text: "ČNB held.", news_item_ids: [3], fact_ids: [] }], stories: [] },
  ],
  sources: [
    { news_item_id: 1, url: "https://a", outlet: null, tier: 1, headline: "a", published_at: "2026-09-25T06:00:00Z" },
    { news_item_id: 2, url: "https://b", outlet: null, tier: 2, headline: "b", published_at: "2026-09-25T06:00:00Z" },
    { news_item_id: 3, url: "https://c", outlet: null, tier: 1, headline: "c", published_at: "2026-09-25T06:00:00Z" },
  ],
  claims: { mode: "strip", supported: 3, unsupported: 0, unchecked: 1, removed: 0 },
};

describe("whatHappened", () => {
  it("lists overview then section sentences with their holdings and severity", () => {
    const items = whatHappened(body);
    expect(items.map((i) => i.path)).toEqual(["overview/0", "overview/1", "sections/0/summary/0", "sections/1/summary/0"]);
    expect(items[0]).toMatchObject({ tickers: ["NOVOb_EQ"], severity: 3, context: "Overview" });
    expect(items[1]).toMatchObject({ tickers: [], severity: null });
    expect(items[3]).toMatchObject({ tickers: [], severity: null, context: "Markets and macro" });
  });
});

describe("digestChecks", () => {
  it("counts supported claims, tier-1 sources and holdings with a section", () => {
    expect(digestChecks(body, ["NOVOb_EQ", "NVDA_US_EQ"])).toEqual({
      claims: { value: 3, total: 4 },
      tier1: { value: 2, total: 3 },
      holdings: { value: 1, total: 2 },
    });
    expect(digestChecks({ ...body, claims: null }, null)).toMatchObject({ claims: null, holdings: null });
  });
});

describe("telegramTimes", () => {
  it("keeps sent Telegram alerts inside the window", () => {
    const a = (channel: "telegram" | "email", status: "sent" | "failed", sent_at: string | null) => ({ channel, status, sent_at });
    expect(
      telegramTimes(
        [
          a("telegram", "sent", "2026-09-25T18:05:00Z"),
          a("telegram", "sent", "2026-09-25T17:48:00Z"),
          a("email", "sent", "2026-09-25T17:00:00Z"),
          a("telegram", "failed", "2026-09-25T17:00:00Z"),
          a("telegram", "sent", "2026-09-27T17:00:00Z"),
        ],
        body.window_start,
        body.window_end,
      ),
    ).toEqual(["2026-09-25T17:48:00Z", "2026-09-25T18:05:00Z"]);
  });
});
