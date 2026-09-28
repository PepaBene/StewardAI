import { describe, expect, it } from "vitest";

import {
  compactMoney,
  dailyMoveGrid,
  dayExtremes,
  displayTicker,
  investedShare,
  periodChange,
  portfolioDayMove,
  positionMixLabel,
  securityClass,
  unrealisedTotal,
  weightScale,
} from "./holdings-view";

describe("displayTicker", () => {
  it("drops Trading212's venue suffix and instrument tag", () => {
    expect(displayTicker("VWCEd_EQ")).toBe("VWCE");
    expect(displayTicker("MSFT_US_EQ")).toBe("MSFT");
    expect(displayTicker("CEZ_EQ")).toBe("CEZ");
    expect(displayTicker("NOVOb_EQ")).toBe("NOVO");
  });

  it("leaves other tickers alone", () => {
    expect(displayTicker("AAPL")).toBe("AAPL");
  });
});

describe("security classes", () => {
  it("folds OpenFIGI types into ETF / Stock / Other", () => {
    expect(securityClass("ETP")).toBe("ETF");
    expect(securityClass("Open-End Fund")).toBe("ETF");
    expect(securityClass("Common Stock")).toBe("Stock");
    expect(securityClass("ADR")).toBe("Stock");
    expect(securityClass("Warrant")).toBe("Other");
    expect(securityClass(null)).toBeNull();
  });

  it("labels the mix, naming unresolved instruments", () => {
    expect(
      positionMixLabel([
        { security_type: "ETP" },
        { security_type: "ETP" },
        { security_type: "Common Stock" },
        { security_type: null },
      ]),
    ).toBe("2 ETFs · 1 stock · 1 unresolved");
  });
});

describe("portfolio day move", () => {
  it("weights each close-to-close move by the value before it", () => {
    // 110 after +10% was 100; 90 after −10% was 100: 200 before, 200 after.
    const move = portfolioDayMove(
      [
        { ticker: "A", value_base: 110 },
        { ticker: "B", value_base: 90 },
        { ticker: "C", value_base: 50 },
      ],
      (t) => ({ A: 0.1, B: -0.1 })[t] ?? null,
    );
    expect(move?.change).toBeCloseTo(0, 10);
    expect(move?.covered).toBe(2);
  });

  it("is null when no position has a move", () => {
    expect(portfolioDayMove([{ ticker: "A", value_base: 1 }], () => null)).toBeNull();
  });
});

describe("unrealised total", () => {
  it("sums P/L and relates it to cost", () => {
    const total = unrealisedTotal([
      { ppl: 20, cost_account: 100, account_currency: "CZK" },
      { ppl: -10, cost_account: 100, account_currency: "CZK" },
    ]);
    expect(total).toEqual({ ppl: 10, pct: 0.05, currency: "CZK" });
  });

  it("refuses to add P/L in different currencies", () => {
    expect(
      unrealisedTotal([
        { ppl: 1, cost_account: 1, account_currency: "CZK" },
        { ppl: 1, cost_account: 1, account_currency: "EUR" },
      ]),
    ).toBeNull();
  });
});

describe("day extremes", () => {
  it("picks the best and worst movers, skipping holdings without a move", () => {
    const moves: Record<string, number | null> = { A: 0.02, B: -0.03, C: null };
    const r = dayExtremes([{ ticker: "A" }, { ticker: "B" }, { ticker: "C" }], (t) => moves[t]);
    expect(r?.best.holding.ticker).toBe("A");
    expect(r?.worst.change).toBe(-0.03);
  });
});

describe("period change", () => {
  it("is last over first", () => {
    expect(periodChange([{ date: "2026-01-01", close: 100 }, { date: "2026-01-02", close: 110 }])).toBeCloseTo(0.1);
    expect(periodChange([{ date: "2026-01-01", close: 100 }])).toBeNull();
  });
});

describe("daily move grid", () => {
  it("lines holdings up on the latest shared dates, each cell its own close-to-close return", () => {
    const grid = dailyMoveGrid(
      [
        {
          ticker: "A_EQ",
          name: null,
          symbol: null,
          as_of: null,
          last_close: null,
          previous_close: null,
          day_change_pct: null,
          bars: [
            { date: "2026-09-23", close: 100 },
            { date: "2026-09-24", close: 110 },
            { date: "2026-09-25", close: 99 },
          ],
        },
        {
          ticker: "B_EQ",
          name: null,
          symbol: null,
          as_of: null,
          last_close: null,
          previous_close: null,
          day_change_pct: null,
          bars: [
            { date: "2026-09-24", close: 50 },
            { date: "2026-09-25", close: 55 },
          ],
        },
      ],
      2,
    );
    expect(grid.dates).toEqual(["2026-09-24", "2026-09-25"]);
    expect(grid.rows[0].label).toBe("A");
    expect(grid.rows[0].cells[0]).toBeCloseTo(0.1);
    expect(grid.rows[0].cells[1]).toBeCloseTo(-0.1);
    // B's first bar has nothing to compare against.
    expect(grid.rows[1].cells[0]).toBeNull();
    expect(grid.rows[1].cells[1]).toBeCloseTo(0.1);
  });
});

describe("weight meter scale", () => {
  it("rounds the largest weight or target plus a margin up to 5 pp", () => {
    expect(weightScale([{ weight: 0.18, target_weight: 0.22 }])).toBeCloseTo(0.25);
    expect(weightScale([{ weight: 0.01, target_weight: null }])).toBeCloseTo(0.05);
  });

  it("sums the invested share", () => {
    expect(investedShare([{ weight: 0.5 }, { weight: 0.3 }, { weight: null }])).toBeCloseTo(0.8);
    expect(investedShare([{ weight: null }])).toBeNull();
  });
});

describe("compact money", () => {
  it("keeps the currency code and signs gains when asked", () => {
    expect(compactMoney(2_381_884, "CZK")).toBe("2.38M CZK");
    expect(compactMoney(265_200, "CZK", true)).toBe("+265.2k CZK");
    expect(compactMoney(-12_818, "CZK", true)).toBe("−12.8k CZK");
  });
});
