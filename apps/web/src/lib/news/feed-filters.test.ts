import { describe, expect, it } from "vitest";

import { byLead, EMPTY_FILTERS, filtersToSearch, groupByStory, isFiltered, parseFilters, toggle } from "./feed-filters";

describe("feed filters", () => {
  it("round-trips through the URL", () => {
    const filters = {
      ticker: "AAPL_US_EQ",
      severities: [2, 3],
      confidences: ["verified" as const, "disputed" as const],
      tiers: [1],
      story: null,
    };
    const search = filtersToSearch(filters);
    expect(search).toBe("?ticker=AAPL_US_EQ&severity=2&severity=3&confidence=verified&confidence=disputed&tier=1");
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
