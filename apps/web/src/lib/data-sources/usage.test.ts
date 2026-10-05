import { describe, expect, it } from "vitest";

import { dailySeries, endpointTotals, parseUsage, providersOf } from "./usage";

const rows = parseUsage([
  { provider: "fred", endpoint: "obs", window_kind: "day", window_start: "2026-10-02T00:00:00Z", credits: "3", requests: 3 },
  { provider: "fred", endpoint: "obs", window_kind: "day", window_start: "2026-10-01T00:00:00Z", credits: 2, requests: 2 },
  { provider: "ecb", endpoint: "sdw", window_kind: "day", window_start: "2026-10-02T00:00:00Z", credits: null, requests: 4 },
  { provider: "bad", window_start: "" },
]);

describe("usage", () => {
  it("drops rows without a window and lists providers", () => {
    expect(rows).toHaveLength(3);
    expect(providersOf(rows)).toEqual(["ecb", "fred"]);
  });
  it("sums per day, oldest first, skipping unknown credits", () => {
    expect(dailySeries(rows, null, "credits")).toEqual([{ date: "2026-10-01", value: 2 }, { date: "2026-10-02", value: 3 }]);
    expect(dailySeries(rows, "ecb", "credits")).toEqual([]);
    expect(dailySeries(rows, "ecb", "requests")).toEqual([{ date: "2026-10-02", value: 4 }]);
  });
  it("totals per endpoint", () => {
    expect(endpointTotals(rows, null, "requests")[0]).toEqual({ key: "fred obs", total: 5 });
  });
});
