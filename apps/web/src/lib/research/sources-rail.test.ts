import { describe, expect, it } from "vitest";

import { domainOf, groupByDomain } from "./sources-rail";

describe("sources rail", () => {
  it("strips www and path", () => {
    expect(domainOf("https://www.reuters.com/markets/x")).toBe("reuters.com");
    expect(domainOf("not a url")).toBe("not a url");
  });

  it("groups by domain, most cited first, keeping original numbers", () => {
    const urls = ["https://a.com/1", "https://b.com/1", "https://www.b.com/2", "https://c.com/1"];
    const groups = groupByDomain(urls, (u) => u);
    expect(groups.map((g) => [g.domain, g.items.length])).toEqual([
      ["b.com", 2],
      ["a.com", 1],
      ["c.com", 1],
    ]);
    expect(groups[0].items.map((i) => i.index)).toEqual([1, 2]);
  });
});
