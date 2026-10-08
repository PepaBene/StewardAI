import { describe, expect, it } from "vitest";

import { groupByArea, spreadLabel, type ChangeItem, type Spread } from "./insights";

const pct = (n: number) => `${Math.round(n * 100)}%`;

describe("spreadLabel", () => {
  it("shows no value when nobody has one", () => {
    expect(spreadLabel({ n: 0, of: 4, median: null, low: null, high: null } as Spread, pct)).toBe("none of 4 have a value");
  });
  it("gives the range and no median for a thin sample", () => {
    expect(spreadLabel({ n: 2, of: 3, median: null, low: 0.1, high: 0.3 } as Spread, pct)).toBe("too few for a median (2 of 3): 10% to 30%");
  });
  it("gives the median with its sample when it is large enough", () => {
    expect(spreadLabel({ n: 3, of: 4, median: 0.2, low: 0.1, high: 0.6 } as Spread, pct)).toBe("median 20% (3 of 4), range 10% to 60%");
  });
});

describe("groupByArea", () => {
  it("keeps the feed order inside each area and the order areas first appear", () => {
    const item = (area: string, what: string) => ({ area, what }) as unknown as ChangeItem;
    const out = groupByArea([item("price", "a"), item("estimates", "b"), item("price", "c")]);
    expect(out.map(([a, rows]) => [a, rows.map((r) => r.what)])).toEqual([
      ["price", ["a", "c"]],
      ["estimates", ["b"]],
    ]);
  });
});
