import { describe, expect, it } from "vitest";

import { byLead, EMPTY_FILTERS, feedScope, filtersToSearch, groupByStory, isFiltered, parseFilters, showAll, splitLinks, toggle } from "./feed-filters";

describe("feed filters", () => {
  it("round-trips through the URL", () => {
    const filters = {
      ticker: "AAPL_US_EQ",
      severities: [2, 3],
      confidences: ["verified" as const, "disputed" as const],
      tiers: [1],
      story: null,
      eventType: "m_and_a",
      related: false,
      ignored: false,
    };
    const search = filtersToSearch(filters);
    expect(search).toBe("?ticker=AAPL_US_EQ&severity=2&severity=3&confidence=verified&confidence=disputed&tier=1&event_type=m_and_a");
    expect(parseFilters(new URLSearchParams(search.slice(1)))).toEqual(filters);
  });

  it("drops values the API would reject", () => {
    const parsed = parseFilters(
      new URLSearchParams("ticker=bad ticker!&severity=7&severity=2&severity=2&confidence=maybe&tier=0&story=-1"),
    );
    expect(parsed).toEqual({ ...EMPTY_FILTERS, severities: [2] });
    expect(filtersToSearch(EMPTY_FILTERS)).toBe("");
    expect(isFiltered(EMPTY_FILTERS)).toBe(false);
    expect(isFiltered(parsed)).toBe(true);
  });

  it("scopes the feed to stories about holdings by default", () => {
    expect(feedScope(EMPTY_FILTERS)).toEqual({ direct: true, severity: undefined, minSeverity: 1 });
    expect(feedScope({ ...EMPTY_FILTERS, related: true, ignored: true })).toEqual({ direct: false, severity: undefined, minSeverity: undefined });
    expect(feedScope({ ...EMPTY_FILTERS, severities: [3] })).toEqual({ direct: true, severity: [3], minSeverity: undefined });
    expect(feedScope({ ...EMPTY_FILTERS, story: 7 })).toEqual({ direct: false, severity: undefined, minSeverity: undefined });
    const search = filtersToSearch({ ...EMPTY_FILTERS, related: true });
    expect(search).toBe("?related=1");
    expect(parseFilters(new URLSearchParams(search.slice(1))).related).toBe(true);
  });

  it("shows everything the scope hides, keeping the other filters", () => {
    const all = showAll({ ...EMPTY_FILTERS, ticker: "AAPL_US_EQ", story: 4 });
    expect(all).toMatchObject({ ticker: "AAPL_US_EQ", story: null, related: true, ignored: true });
    expect(feedScope(all)).toEqual({ direct: false, severity: undefined, minSeverity: undefined });
  });

  it("keeps the event type in the URL and counts it as a filter", () => {
    expect(parseFilters(new URLSearchParams("event_type=unclassified")).eventType).toBe("unclassified");
    expect(parseFilters(new URLSearchParams("event_type=Bad Type!")).eventType).toBeNull();
    expect(isFiltered({ ...EMPTY_FILTERS, eventType: "m_and_a" })).toBe(true);
  });

  it("splits a story's holdings into those it is about and those it reaches", () => {
    const { about, related } = splitLinks(
      [
        { ticker: "B", via: "customer" },
        { ticker: "A", via: "name" },
        { ticker: "C", via: "competitor" },
        { ticker: "A", via: "source" },
        { ticker: "C", via: "topic" },
        { ticker: "D", via: "alias" },
      ],
      "D",
    );
    expect(about.map((l) => l.ticker)).toEqual(["D", "A"]);
    expect(related.map((l) => l.ticker)).toEqual(["B", "C"]);
  });

  it("toggles values", () => {
    expect(toggle([1, 2], 2)).toEqual([1]);
    expect(toggle([1], 3)).toEqual([1, 3]);
  });

  it("groups a story's items together, in feed order", () => {
    const items = [
      { id: 5, story: { id: 1 } },
      { id: 4, story: { id: 2 } },
      { id: 3, story: { id: 1 } },
    ];
    expect(groupByStory(items).map((g) => g.map((i) => i.id))).toEqual([[5, 3], [4]]);
  });

  it("leads a story with its most trusted, then earliest, item", () => {
    const items = [
      { id: 3, tier: 2, published_at: "2026-09-25T10:00:00Z" },
      { id: 2, tier: 2, published_at: "2026-09-25T08:00:00Z" },
      { id: 1, tier: 1, published_at: "2026-09-25T09:00:00Z" },
    ];
    expect(byLead(items).map((i) => i.id)).toEqual([1, 2, 3]);
  });
});
