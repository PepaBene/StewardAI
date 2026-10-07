import { describe, expect, it } from "vitest";

import {
  activeBar,
  applyScenario,
  basisShares,
  buildProposal,
  cashRemainder,
  compositionSegments,
  groupViews,
  pointsLabel,
  segmentTone,
  weightToFraction,
  type XrayBucket,
  type XrayView,
} from "./xray";

function bucket(key: string, weight: number, kind: XrayBucket["kind"] = "classified"): XrayBucket {
  return {
    key,
    label: key,
    kind,
    weight,
    value_base: weight * 1000,
    direct_weight: 0,
    fund_weight: weight,
    contributors: [],
    basis: {},
  };
}

describe("compositionSegments", () => {
  it("merges small real buckets but never absorbs cash or unknown weight", () => {
    const segs = compositionSegments([
      bucket("IT", 0.4),
      bucket("Health", 0.2),
      bucket("Tiny A", 0.004),
      bucket("Tiny B", 0.003),
      bucket("__cash__", 0.1, "cash"),
      bucket("__unknown__", 0.005, "unknown_fund"),
    ]);
    expect(segs.map((s) => s.key)).toEqual(["IT", "Health", "__smaller__", "__cash__", "__unknown__"]);
    expect(segs.find((s) => s.key === "__smaller__")?.weight).toBeCloseTo(0.007);
    // a 0.5% unknown bucket keeps its own segment
    expect(segs.at(-1)?.kind).toBe("unknown_fund");
    expect(segs.reduce((a, s) => a + s.weight, 0)).toBeCloseTo(0.4 + 0.2 + 0.007 + 0.1 + 0.005);
  });

  it("ranks real segments by order and skips non-positive weights", () => {
    const segs = compositionSegments([bucket("A", 0.5), bucket("B", 0.3), bucket("Short", -0.02, "fund_cash")]);
    expect(segs.map((s) => [s.key, s.rank])).toEqual([
      ["A", 0],
      ["B", 1],
    ]);
  });
});

describe("segmentTone", () => {
  it("hatches unknown weight and flattens cash, so unknown never looks like a holding", () => {
    expect(segmentTone({ kind: "not_looked_through", rank: -1 }).hatched).toBe(true);
    expect(segmentTone({ kind: "unknown_fund", rank: -1 }).hatched).toBe(true);
    expect(segmentTone({ kind: "cash", rank: -1 })).toEqual({ className: "bg-border", hatched: false });
    expect(segmentTone({ kind: "classified", rank: 0 }).className).toBe("bg-accent");
    expect(segmentTone({ kind: "classified", rank: 99 }).className).toBe("bg-accent/32");
  });
});

describe("basisShares", () => {
  it("states how a bucket was classified, largest first", () => {
    expect(basisShares({ reported: 0.3, inferred: 0.1, owner: 0.1 }).map((b) => [b.basis, Math.round(b.share * 100)])).toEqual([
      ["reported", 60],
      ["inferred", 20],
      ["owner", 20],
    ]);
    expect(basisShares({})).toEqual([]);
  });
});

describe("groupViews", () => {
  it("orders the lens groups and drops empty ones", () => {
    const view = (dimension: string, group: string) => ({ dimension, group }) as unknown as XrayView;
    const groups = groupViews([view("size", "derived"), view("sector", "classification"), view("fund", "structure")]);
    expect(groups.map((g) => g.group)).toEqual(["classification", "structure", "derived"]);
  });
});

describe("pointsLabel and activeBar", () => {
  it("signs a weight difference in percentage points", () => {
    expect(pointsLabel(0.031)).toBe("+3.1 pp");
    expect(pointsLabel(-0.0204)).toBe("−2.0 pp");
    expect(pointsLabel(0.00001)).toBe("0.0 pp");
    expect(pointsLabel(null)).toBe("—");
  });
  it("draws over and underweights on opposite sides", () => {
    expect(activeBar(0.1, 0.2)).toEqual({ side: "over", width: 50 });
    expect(activeBar(-0.2, 0.2)).toEqual({ side: "under", width: 100 });
    expect(activeBar(0, 0.2)).toEqual({ side: "none", width: 0 });
  });
});

describe("weightToFraction", () => {
  it("reads percent with a dot or a comma and rejects the rest", () => {
    expect(weightToFraction("12.5")).toBeCloseTo(0.125);
    expect(weightToFraction("12,5")).toBeCloseTo(0.125);
    expect(weightToFraction(" 0 ")).toBe(0);
    expect(weightToFraction("")).toBeNull();
    expect(weightToFraction("-1")).toBeNull();
    expect(weightToFraction("101")).toBeNull();
    expect(weightToFraction("abc")).toBeNull();
  });
});

describe("buildProposal", () => {
  const rows = [
    { ticker: "AAPL", current: 0.3, text: "30" },
    { ticker: "CSPX", current: 0.6, text: "60" },
  ];

  it("sends only the weights you changed", () => {
    const built = buildProposal([{ ...rows[0], text: "10" }, rows[1]], [{ ticker: "NESN", text: "20" }]);
    expect(built).toEqual({
      ok: true,
      request: { weights: [{ ticker: "AAPL", weight: 0.1 }], additions: [{ ticker: "NESN", weight: 0.2 }] },
      changed: 2,
      invested: expect.closeTo(0.9),
    });
  });

  it("names the holding with an unreadable weight", () => {
    const built = buildProposal([{ ...rows[0], text: "x" }, rows[1]], []);
    expect(built).toEqual({ ok: false, error: "AAPL: enter a weight between 0 and 100." });
  });

  it("refuses more than 100% and an unchanged proposal", () => {
    const over = buildProposal([{ ...rows[0], text: "50" }, rows[1]], []);
    expect(over.ok).toBe(false);
    if (!over.ok) expect(over.error).toContain("cannot exceed 100%");
    expect(buildProposal(rows, [])).toEqual({ ok: false, error: "Change a weight or add a security first." });
  });

  it("refuses an addition with no weight", () => {
    const built = buildProposal(rows, [{ ticker: "NESN", text: "0" }]);
    expect(built.ok).toBe(false);
  });
});

describe("applyScenario", () => {
  const holdings = [
    { ticker: "AAA", weight: 0.3 },
    { ticker: "BBB", weight: 0.2 },
  ];

  it("puts saved weights over the current holdings and keeps the others as they are", () => {
    const out = applyScenario(holdings, { weights: [{ ticker: "AAA", weight: 0.15 }], additions: [{ ticker: "NEW", weight: 0.05 }] });
    expect(out.texts).toEqual({ AAA: "15.000", BBB: "20.000" });
    expect(out.added).toEqual([{ ticker: "NEW", text: "5" }]);
    expect(out.missing).toEqual([]);
  });

  it("names a saved holding that is no longer held instead of dropping it silently", () => {
    const out = applyScenario(holdings, { weights: [{ ticker: "GONE", weight: 0.1 }], additions: [] });
    expect(out.missing).toEqual(["GONE"]);
    expect(out.texts).toEqual({ AAA: "30.000", BBB: "20.000" });
  });
});

describe("cashRemainder", () => {
  it("is what is left of the whole after the typed weights", () => {
    expect(cashRemainder([{ ticker: "A", current: 0.3, text: "20" }], [{ ticker: "B", text: "15" }])).toBeCloseTo(0.65);
    expect(cashRemainder([{ ticker: "A", current: 0.3, text: "oops" }], [])).toBeNull();
  });
});
