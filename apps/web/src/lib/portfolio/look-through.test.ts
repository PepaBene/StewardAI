import { describe, expect, it } from "vitest";

import {
  countryLabel,
  exposureSegments,
  fundProvenance,
  fundsByTicker,
  hasLookThrough,
  lookThroughConcentrationPosition,
  lookThroughRisk,
  type ExposureBucket,
  type ExposureDimension,
  type FundLookThrough,
  type LookThroughReport,
} from "./look-through";

function bucket(key: string, weight: number, kind: ExposureBucket["kind"] = "classified"): ExposureBucket {
  return { key, label: key, kind, weight, value_base: weight * 1000, direct_weight: 0, fund_weight: weight, benchmark_weight: null };
}

function fund(over: Partial<FundLookThrough>): FundLookThrough {
  return {
    isin: "IE00B5BMR087",
    label: "CSPX",
    ticker: "CSPXl_EQ",
    name: null,
    provider: "ishares",
    portfolio_id: "253743",
    held: true,
    benchmark_proxy_for: [],
    value_base: 100,
    weight: 0.1,
    status: "ok",
    as_of: "2026-09-25",
    fetched_at: "2026-09-27T06:40:00Z",
    source: "ishares_json",
    source_label: "iShares product-data API (undocumented)",
    tier: 1,
    tier_label: "Tier 1",
    fallback: null,
    last_attempt: null,
    country_basis: null,
    currency_basis: null,
    equity_share: 0.99,
    holdings_count: 504,
    warnings: [],
    top_holdings: [],
    ...over,
  };
}

function report(over: Partial<LookThroughReport>): LookThroughReport {
  return {
    snapshot_date: "2026-09-27",
    base_currency: "CZK",
    available: true,
    method: "",
    coverage: null,
    exposures: [{ dimension: "country", basis: "", buckets: [bucket("US", 0.6)] }],
    top_holdings: [],
    concentration: { hhi: 0.06, effective_n: 16.7, top10_share: 0.74, holdings: 3817, basis: "" },
    benchmark: null,
    benchmarks: [],
    fx: { base_currency: "CZK", foreign_share: 0.85, known_share: 1, benchmark_foreign_share: 1, benchmark_label: "S&P 500", rows: [] },
    funds: [],
    notes: [],
    ...over,
  };
}

describe("look-through display rules", () => {
  it("keeps special buckets as their own segments and folds the classified tail", () => {
    const dim: ExposureDimension = {
      dimension: "country",
      basis: "",
      buckets: [
        ...["US", "JP", "GB", "CA", "FR", "DE", "CH", "TW"].map((k, i) => bucket(k, 0.1 - i * 0.01)),
        bucket("__cash__", 0.04, "cash"),
        bucket("__fund_cash__", 0.002, "fund_cash"),
      ],
    };
    const segments = exposureSegments(dim);
    expect(segments).toHaveLength(8); // the chart's slot count
    expect(segments.map((s) => s.label)).toEqual(["US", "JP", "GB", "CA", "FR", "Other 3", "__cash__", "__fund_cash__"]);
    expect(segments[5].value).toBeCloseTo(0.05 + 0.04 + 0.03);
  });

  it("labels each fund's provenance honestly", () => {
    expect(fundProvenance(fund({}))).toBe("iShares, as of 25 Sep 2026");
    expect(fundProvenance(fund({ fallback: "cached", status: "stale" }))).toBe(
      "iShares, as of 25 Sep 2026 · latest fetch failed, stored snapshot shown · stale",
    );
    expect(fundProvenance(fund({ provider: "vanguard", as_of: null, status: "missing" }))).toBe("Vanguard: no snapshot yet");
    expect(fundProvenance(fund({ status: "not_configured", provider: null }))).toBe("No look-through source configured");
  });

  it("maps held funds by ticker and gives the radar its inputs only with data", () => {
    const r = report({ funds: [fund({}), fund({ ticker: null, held: false, label: "VWCE" })] });
    expect([...fundsByTicker(r).keys()]).toEqual(["CSPXl_EQ"]);
    expect(hasLookThrough(r)).toBe(true);
    expect(lookThroughRisk(r)).toEqual({ hhi: 0.06, benchmarkHhi: null, fxShare: 0.85, benchmarkFxShare: 1 });
    expect(lookThroughRisk(report({ available: false }))).toBeNull();
    expect(hasLookThrough(null)).toBe(false);
  });

  it("puts the look-through HHI on a fixed 0.10 rim and names countries", () => {
    expect(lookThroughConcentrationPosition(0.05)).toBeCloseTo(0.5);
    expect(lookThroughConcentrationPosition(0.4)).toBe(1);
    expect(countryLabel("US")).toBe("United States");
    expect(countryLabel(null)).toBe("—");
  });
});
