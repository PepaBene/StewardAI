import { describe, expect, it } from "vitest";

import { concentrationView, fundRows, num, pct, ratioOf, returnCoverageRows, sensitivityRows, signed, unknownFund, type PortfolioDataContext } from "./data-context";

type Conc = NonNullable<PortfolioDataContext["country_concentration"]>;

const share = (n: number, d: number, basis = "total portfolio value") => ({ numerator: n, denominator: d, ratio: d ? n / d : null, denominator_basis: basis });

describe("concentration", () => {
  const c = {
    dimension: "country",
    mapped: share(60, 100),
    unmapped_value: "40",
    unmapped_tickers: ["X", "Y"],
    hhi: "0.52",
    note: "n",
    rows: [
      { key: "DE", exposed_value: "10", share_of_portfolio: share(10, 100), share_of_mapped: share(10, 60), by_basis: { reported: 6, estimated: 0, user_assigned: 4 }, holdings: 1 },
      { key: "US", exposed_value: "50", share_of_portfolio: share(50, 100), share_of_mapped: null, by_basis: {}, holdings: 3 },
    ],
  } as unknown as Conc;

  it("orders by value, keeps the unmapped remainder and drops empty bases", () => {
    const v = concentrationView(c)!;
    expect(v.rows.map((r) => r.key)).toEqual(["US", "DE"]);
    expect(v.rows[1].byBasis).toEqual([{ basis: "reported", value: 6 }, { basis: "user_assigned", value: 4 }]);
    expect(v.rows[0].ofMapped).toBeNull();
    expect(v.mapped).toMatchObject({ numerator: 60, denominator: 100, ratio: 0.6 });
    expect(v.unmappedValue).toBe(40);
    expect(v.unmappedTickers).toEqual(["X", "Y"]);
    expect(concentrationView(null)).toBeNull();
  });
});

describe("sensitivities and coverage", () => {
  it("keeps reported and inferred apart and a missing change null", () => {
    const rows = sensitivityRows([
      {
        series_id: "CPI",
        source: "oecd",
        unit: "percent",
        reported: { exposed_value_total: 100, exposed_value_covered: 0, coverage: share(0, 100), weighted_change: null, geographies: [] },
        inferred: { exposed_value_total: 100, exposed_value_covered: 50, coverage: share(50, 100), weighted_change: "0.4", geographies: ["DE"] },
        latest_period_min: null,
        latest_period_max: null,
        portfolio_value: 200,
      },
    ] as never);
    expect(rows[0].reported.weightedChange).toBeNull();
    expect(rows[0].inferred.weightedChange).toBe(0.4);
    expect(rows[0].inferred.coverage!.ratio).toBe(0.5);
    expect(signed(null)).toBe("No change computed");
    expect(signed(-0.4)).toBe("−0.40");
  });

  it("reads return coverage per basis", () => {
    const rows = returnCoverageRows({
      note: "n",
      bases: [{ adjustment_basis: "price", complete: share(80, 100), partial_value: 15, missing_value: 5, complete_tickers: [], partial_tickers: ["A"], missing_tickers: ["B"] }],
    } as never);
    expect(rows[0]).toMatchObject({ basis: "price", partialValue: 15, missingValue: 5, partialTickers: ["A"] });
    expect(returnCoverageRows(null)).toEqual([]);
  });
});

describe("funds", () => {
  it("names pairs by fund label, sorts by overlap and never redistributes unknown weight", () => {
    const r = fundRows({
      as_of: "2026-09-30",
      notes: [],
      funds: [
        { isin: "A", label: "CSPX", as_of: "2026-09-29", lag_days: 1, stale: false, equity_weight: 0.97, cash_derivative_other_weight: 0.01, unknown_weight: 0.02, unidentified_equity_weight: 0, hhi: null, effective_n: 90, top10_share: 0.3, issuers: 500 },
      ],
      pairs: [
        { fund_a: "A", fund_b: "B", overlap: 0.2, shared_issuers: 10, as_of_a: "x", as_of_b: "y", same_date: false },
        { fund_a: "A", fund_b: "C", overlap: 0.6, shared_issuers: 40, as_of_a: "x", as_of_b: "y", same_date: true },
      ],
    } as never);
    expect(r.pairs.map((p) => p.b)).toEqual(["C", "B"]);
    expect(r.pairs[0].a).toBe("CSPX");
    expect(r.funds[0]).toMatchObject({ unknownWeight: 0.02, stale: false });
    expect(unknownFund({ unknown_fund_weight: 0.1, unknown_fund_value: 500, not_looked_through_weight: 0.06, residual_weight: 0.04, over_reported_weight: 0, by_fund: {} })).toMatchObject({ unknown: 0.1, value: 500 });
    expect(fundRows(null)).toEqual({ funds: [], pairs: [], notes: [] });
  });

  it("formats and coerces safely", () => {
    expect(pct(null)).toBe("—");
    expect(pct(0.1234)).toBe("12.3%");
    expect(num("x")).toBeNull();
    expect(ratioOf(null)).toBeNull();
  });
});
