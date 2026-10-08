import { describe, expect, it } from "vitest";

import { GRAPH, layoutGraph, parseConstituents, parseRegionLines } from "./owner-input";

describe("parseRegionLines", () => {
  it("reads country and percent lines into fractions", () => {
    const r = parseRegionLines("Germany 40\nFR: 35%\nItaly = 25");
    expect(r).toEqual({ ok: true, value: { Germany: 0.4, FR: 0.35, Italy: 0.25 } });
  });
  it("adds a country written twice", () => {
    const r = parseRegionLines("Germany 30\nGermany 30\nFrance 40");
    expect(r.ok && r.value.Germany).toBeCloseTo(0.6);
  });
  it("refuses a total that is not 100 and lines it cannot read", () => {
    expect(parseRegionLines("Germany 40\nFrance 40")).toMatchObject({ ok: false });
    expect(parseRegionLines("Germany")).toMatchObject({ ok: false });
    expect(parseRegionLines("")).toMatchObject({ ok: false });
  });
});

describe("parseConstituents", () => {
  it("reads semicolon and tab separated rows, with optional ISIN and ticker", () => {
    const r = parseConstituents("Apple Inc. ; us0378331005 ; AAPL ; 6.5%\nAlphabet, Inc.\t\tGOOGL\t4,2\nNo ids ;;; 1");
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.value[0]).toEqual({ name: "Apple Inc.", isin: "US0378331005", ticker: "AAPL", weight: 0.065 });
      expect(r.value[1]).toMatchObject({ name: "Alphabet, Inc.", isin: null, ticker: "GOOGL" });
      expect(r.value[1].weight).toBeCloseTo(0.042);
      expect(r.value[2]).toMatchObject({ isin: null, ticker: null, weight: 0.01 });
    }
  });
  it("refuses a bad ISIN, a missing weight and a total above 100", () => {
    expect(parseConstituents("A ; 123 ; ; 5")).toMatchObject({ ok: false });
    expect(parseConstituents("A ; ; ; x")).toMatchObject({ ok: false });
    expect(parseConstituents("A ; ; ; 70\nB ; ; ; 40")).toMatchObject({ ok: false });
    expect(parseConstituents("just a name")).toMatchObject({ ok: false });
  });
});

describe("layoutGraph", () => {
  it("puts suppliers on the left and each level one column to the right", () => {
    const { positions, width, height } = layoutGraph([
      { symbol: "TSM", name: null, weight: 0, held: false, level: 0 },
      { symbol: "ASML", name: null, weight: 0, held: false, level: 0 },
      { symbol: "AAPL", name: null, weight: 0.1, held: true, level: 1 },
    ]);
    expect(positions.get("TSM")!.x).toBe(GRAPH.pad);
    expect(positions.get("AAPL")!.x).toBe(GRAPH.pad + GRAPH.colWidth);
    expect(positions.get("ASML")!.y).toBe(GRAPH.pad + GRAPH.rowHeight);
    expect(width).toBe(GRAPH.pad * 2 + GRAPH.colWidth + GRAPH.nodeW);
    expect(height).toBe(GRAPH.pad * 2 + GRAPH.rowHeight + GRAPH.nodeH);
  });
  it("is empty for no nodes", () => {
    expect(layoutGraph([]).positions.size).toBe(0);
  });
});
