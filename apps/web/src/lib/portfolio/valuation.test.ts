import { describe, expect, it } from "vitest";

import type { PerShareFigure, PortfolioValuation } from "@/lib/api/client";

import {
  figureFlags,
  formatMultiple,
  formatPerShare,
  formatYield,
  periodLabel,
  priceGap,
  priceGapLabel,
  ratioText,
  relativeTo,
  VALUATION_MIN_COVERAGE,
  VALUATION_SCALE,
  valuationAxis,
} from "./valuation";

function portfolio(pe: number | null, coverage: number, benchPe: number | null): PortfolioValuation {
  return {
    as_of: "2026-09-27",
    base_currency: "CZK",
    portfolio: { pe, pb: null, dividend_yield: null, coverage: { pe: coverage, pb: 0, dividend_yield: 0 } },
    benchmark:
      benchPe === null
        ? null
        : {
            label: "S&P 500",
            symbol: "^spx",
            via: "CSPX fund figures (iShares)",
            fund_key: "CSPX",
            pe: benchPe,
            pb: null,
            dividend_yield: null,
            dividend_yield_kind: null,
            as_of: "2026-09-25",
            source_url: "https://example.org",
            methodology: null,
            stale: false,
          },
    comparisons: [],
    holdings: [],
    method: "",
  };
}

const figure = (over: Partial<PerShareFigure> = {}): PerShareFigure => ({
  value: 2.09,
  currency: "USD",
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  method: "full year (FY to 2025-12-31)",
  annual_only: true,
  stale: true,
  inputs: [],
  ...over,
});

describe("valuationAxis", () => {
  it("maps P/E onto the stated scale", () => {
    const axis = valuationAxis(portfolio(20, 0.9, 30));
    expect(axis.portfolio).toBeCloseTo(20 / VALUATION_SCALE.pe);
    expect(axis.benchmark).toBeCloseTo(0.75);
    expect(axis.portfolioPe).toBe(20);
  });

  it("clamps at the rim", () => {
    expect(valuationAxis(portfolio(55, 1, 60)).portfolio).toBe(1);
  });

  it("drops the portfolio value when too little is covered", () => {
    const axis = valuationAxis(portfolio(20, VALUATION_MIN_COVERAGE - 0.01, 30));
    expect(axis.portfolio).toBeNull();
    expect(axis.benchmark).toBeCloseTo(0.75);
  });

  it("is empty without data", () => {
    expect(valuationAxis(null)).toMatchObject({ portfolio: null, benchmark: null });
    expect(valuationAxis(portfolio(null, 1, null))).toMatchObject({ portfolio: null, benchmark: null });
  });
});

describe("formatting", () => {
  it("formats multiples and yields", () => {
    expect(formatMultiple(23.167)).toBe("23.2×");
    expect(formatMultiple(null)).toBe("—");
    expect(formatYield(0.0152721)).toBe("1.53%");
  });

  it("shows n/m only for not-meaningful ratios", () => {
    expect(ratioText({ value: null, reason: "n/m: negative earnings" })).toBe("n/m");
    expect(ratioText({ value: null, reason: "no stored price" })).toBe("—");
    expect(ratioText({ value: 0.04, reason: null }, "yield")).toBe("4.00%");
  });

  it("labels periods and flags", () => {
    expect(periodLabel(figure())).toBe("FY to Dec 2025");
    expect(periodLabel(figure({ annual_only: false, method: "FY to 2025-12-31 + 6M…", period_start: "2025-07-01", period_end: "2026-06-30" }))).toBe(
      "TTM to Jun 2026",
    );
    expect(periodLabel(figure({ period_start: null, period_end: "2026-06-30" }))).toBe("as of 30 Jun 2026");
    expect(figureFlags(figure())).toEqual(["annual", "stale"]);
    expect(formatPerShare(figure())).toBe("USD 2.09");
    expect(formatPerShare(figure({ value: 449.9, currency: "CZK" }))).toBe("CZK 449.9");
  });

  it("compares with the benchmark", () => {
    expect(relativeTo(20, 25)).toBeCloseTo(-0.2);
    expect(relativeTo(null, 25)).toBeNull();
  });
});

describe("price against fair value", () => {
  // Production, 17 Oct 2026: ASML at USD 1,640 against a USD 808.65 fair value read "51% above"
  // in the tables while the chart beside them said +103%.
  const pct = (f: number) => `${Math.round(f * 100)}%`;
  it("measures the price's gap on fair value, not the upside on price", () => {
    const asml = 808.65 / 1640.41 - 1;
    expect(priceGapLabel(asml, pct)).toBe("103% above");
    const nu = 21.96 / 14.57 - 1;
    expect(priceGapLabel(nu, pct)).toBe("34% below");
    expect(priceGap(0)).toBe(0);
    expect(priceGapLabel(-1, pct)).toBeNull();
  });
});
