import { describe, expect, it } from "vitest";

import { moversSentence, splitMovers, type Mover } from "./movers";

const rows: Mover[] = [
  { ticker: "NVDA_US_EQ", name: "Nvidia", pct: -0.0012 },
  { ticker: "AMD_US_EQ", name: "AMD", pct: -0.0008 },
  { ticker: "PM_US_EQ", name: "Philip Morris", pct: 0.0003 },
  { ticker: "KO_US_EQ", name: "Coca-Cola", pct: 0.00001 },
  { ticker: "BA_US_EQ", name: "Boeing", pct: 0 },
];

describe("what moved the portfolio", () => {
  it("names the holdings that drove a fall and the one that helped", () => {
    expect(moversSentence(-0.0032, { pct: -0.0077, name: "S&P 500" }, rows)).toBe(
      "Down 0.32%, against −0.77% for the S&P 500. Nvidia (−0.12 pp) and AMD (−0.08 pp) pulled it down most; Philip Morris (+0.03 pp) helped.",
    );
  });

  it("names the holdings that drove a rise without a benchmark", () => {
    const up = rows.map((r) => ({ ...r, pct: -r.pct }));
    expect(moversSentence(0.0032, null, up)).toBe("Up 0.32%. Nvidia (+0.12 pp) and AMD (+0.08 pp) lifted it most; Philip Morris (−0.03 pp) held it back.");
  });

  it("says so when no holding moved it, and nothing without a portfolio move", () => {
    expect(moversSentence(0, null, rows.slice(3))).toBe("Flat. No holding moved it by 0.01 points or more.");
    expect(moversSentence(null, null, rows)).toBeNull();
  });

  it("splits movers up and down and counts the ones that barely moved", () => {
    const { up, down, still } = splitMovers(rows, 5);
    expect(up.map((m) => m.name)).toEqual(["Philip Morris"]);
    expect(down.map((m) => m.name)).toEqual(["Nvidia", "AMD"]);
    expect(still).toBe(2);
  });
});
