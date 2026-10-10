import { describe, expect, it } from "vitest";

import { delayLabel, fetchMethods, responseTimeLabel } from "./stats";

describe("source fetch columns", () => {
  it("lists the fetch methods a source used, in rung order", () => {
    expect(fetchMethods({ "2": 3, "1": 12 })).toBe("12 feed text, 3 page fetch");
    expect(fetchMethods({ "3": 1, "4": 2, "1": 0 })).toBe("1 browser, 2 official API");
    expect(fetchMethods({})).toBe("none");
    expect(fetchMethods(undefined)).toBe("none");
  });

  it("says how long after publication an article was seen", () => {
    expect(delayLabel(5, 40)).toBe("5 min (95%: 40 min)");
    expect(delayLabel(120, 3000)).toBe("2.0 h (95%: 2.1 d)");
    expect(delayLabel(7, null)).toBe("7 min");
    expect(delayLabel(null, null)).toBe("no data");
  });

  it("formats the answer time", () => {
    expect(responseTimeLabel(820)).toBe("820 ms");
    expect(responseTimeLabel(1400)).toBe("1.4 s");
    expect(responseTimeLabel(null)).toBe("no data");
  });
});
