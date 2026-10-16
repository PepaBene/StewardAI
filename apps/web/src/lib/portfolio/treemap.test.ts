import { describe, expect, it } from "vitest";

import { TREEMAP_MODES, treemapItems } from "./treemap";

const holdings = [
  { ticker: "VWCEd_EQ", value_base: 300, weight: 0.3, ppl_pct: 0.12 },
  { ticker: "ASMLa_EQ", value_base: 100, weight: 0.1, ppl_pct: -0.05 },
  { ticker: "GONE_EQ", value_base: 0, weight: 0, ppl_pct: null },
];
const moves: Record<string, number | null> = { VWCEd_EQ: 0.004, ASMLa_EQ: null };

describe("holdings treemap tiles", () => {
  it("sizes by weight and colours by today's move, leaving positions without value out", () => {
    const items = treemapItems(holdings, "day", (t) => moves[t]);
    expect(items.map((i) => i.id)).toEqual(["VWCEd_EQ", "ASMLa_EQ"]);
    expect(items[0]).toMatchObject({ label: "VWCE", weight: 0.3, change: 0.004, href: "/companies/VWCEd_EQ" });
    // No close-to-close move: a neutral tile, never an invented 0.
    expect(items[1].change).toBeNull();
  });

  it("colours by unrealised P/L in the P/L mode", () => {
    const items = treemapItems(holdings, "ppl", () => null);
    expect(items.map((i) => i.change)).toEqual([0.12, -0.05]);
  });

  it("falls back to the share of total value when a position has no weight", () => {
    const items = treemapItems(
      [
        { ticker: "A", value_base: 75, weight: null, ppl_pct: 0 },
        { ticker: "B", value_base: 25, weight: null, ppl_pct: 0 },
      ],
      "ppl",
      () => null,
    );
    expect(items.map((i) => i.weight)).toEqual([0.75, 0.25]);
  });

  it("saturates the day map at ±3% and the P/L map at ±20%", () => {
    expect(TREEMAP_MODES.day.cap).toBe(0.03);
    expect(TREEMAP_MODES.ppl.cap).toBe(0.2);
  });
});
