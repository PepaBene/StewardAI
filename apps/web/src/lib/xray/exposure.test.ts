import { describe, expect, it } from "vitest";

import {
  aggregateProvenance,
  buildExposureTreemap,
  buildLookThroughSankey,
  fundDataState,
  KEY_CASH,
  KEY_NOT_LOOKED,
  KEY_UNKNOWN,
} from "./exposure";
import type { AggregateReport, ExposureBucket, FundAsOf, FundOverlapReport, IssuerExposure } from "./types";

function bucket(key: string, kind: ExposureBucket["kind"], weight: number, direct = 0): ExposureBucket {
  return { key, label: key, kind, weight, value_base: weight * 1000, direct_weight: direct, fund_weight: weight - direct };
}

function issuer(key: string, weight: number, over: Partial<IssuerExposure> = {}): IssuerExposure {
  return {
    key,
    label: key,
    merged_securities: 1,
    securities: [],
    weight,
    value_base: weight * 1000,
    direct_weight: 0,
    fund_weight: weight,
    overlap_weight: 0,
    channels: [],
    sector: "Tech",
    country: "US",
    region: "North America",
    currency: "USD",
    ...over,
  };
}

function fund(isin: string, weight: number, over: Partial<FundAsOf> = {}): FundAsOf {
  return {
    isin,
    label: isin,
    ticker: isin.slice(0, 4),
    weight,
    value_base: weight * 1000,
    status: "ok",
    holdings_as_of: "2026-09-01",
    lag_days: 10,
    stale: false,
    source: "ishares_json",
    rows_total_weight: 1,
    residual_weight: 0,
    unknown_weight: 0,
    reconciles: true,
    expense_ratio: 0.0007,
    fee_drag: weight * 0.0007,
    warnings: [],
    ...over,
  };
}

function report(over: Partial<AggregateReport> = {}): AggregateReport {
  return {
    as_of: "2026-10-03",
    portfolio_snapshot_date: "2026-10-02",
    base_currency: "EUR",
    total_value: 1000,
    available: true,
    method: "m",
    funds: [],
    stale_funds: [],
    issuers: [],
    overlap: [],
    exposures: [],
    fees: null,
    unknown: null,
    notes: [],
    ...over,
  };
}

describe("aggregateProvenance", () => {
  it("is not_covered without a portfolio snapshot", () => {
    const p = aggregateProvenance(report({ available: false, notes: ["No portfolio snapshot known at this date."] }));
    expect(p.state).toBe("not_covered");
    expect(p.reason).toContain("No portfolio snapshot");
  });
  it("is stale when a fund is stale, partial when weight is unknown, verified otherwise", () => {
    const funds = [fund("IE00AAAAAAA1", 0.5)];
    expect(aggregateProvenance(report({ funds })).state).toBe("verified");
    expect(aggregateProvenance(report({ funds, stale_funds: ["X"] })).state).toBe("stale");
    const unknown = { unknown_fund_weight: 0.1, unknown_fund_value: 100, not_looked_through_weight: 0, residual_weight: 0.1, over_reported_weight: 0, by_fund: {} };
    expect(aggregateProvenance(report({ funds, unknown })).state).toBe("partial");
  });
  it("counts coverage as looked-through funds over held funds", () => {
    const funds = [fund("IE00AAAAAAA1", 0.4), fund("IE00BBBBBBB2", 0.2, { status: "missing" }), fund("IE00CCCCCCC3", 0, { status: "ok" })];
    const p = aggregateProvenance(report({ funds }));
    expect(p.coverage).toEqual({ covered: 1, total: 2, unit: "funds looked through" });
    expect(p.state).toBe("partial");
  });
});

describe("buildExposureTreemap", () => {
  const rep = report({
    issuers: [issuer("a", 0.2), issuer("b", 0.1, { sector: "Health" }), issuer("c", 0.05, { sector: null })],
    exposures: [
      {
        dimension: "sector",
        reconciles: true,
        buckets: [
          bucket("Tech", "classified", 0.3),
          bucket("Health", "classified", 0.1),
          bucket("__unclassified__", "unclassified", 0.05),
          bucket(KEY_CASH, "cash", 0.1, 0.1),
          bucket(KEY_UNKNOWN, "unknown_fund", 0.05),
          bucket("Energy", "classified", 0),
        ],
      },
    ],
  });
  it("nests named issuers under their bucket and keeps the remainder explicit", () => {
    const t = buildExposureTreemap(rep, "sector")!;
    const tech = t.groups.find((g) => g.key === "Tech")!;
    expect(tech.children.map((c) => c.kind)).toEqual(["issuer", "remainder"]);
    expect(tech.children[1].weight).toBeCloseTo(0.1);
    expect(t.groups.find((g) => g.key === "Health")!.children).toHaveLength(1);
  });
  it("puts special buckets last, never gives them a remainder tile and drops zero buckets", () => {
    const t = buildExposureTreemap(rep, "sector")!;
    expect(t.groups.map((g) => g.kind)).toEqual(["classified", "classified", "unclassified", "cash", "unknown_fund"]);
    expect(t.groups.find((g) => g.kind === "cash")!.children).toEqual([]);
    expect(t.dropped).toBe(1);
  });
  it("assigns issuers without a classification to the unclassified bucket", () => {
    const t = buildExposureTreemap(rep, "sector")!;
    expect(t.groups.find((g) => g.kind === "unclassified")!.children.map((c) => c.label)).toEqual(["c"]);
  });
  it("flags named issuers that exceed their bucket", () => {
    const t = buildExposureTreemap(
      report({ issuers: [issuer("a", 0.5)], exposures: [{ dimension: "sector", reconciles: false, buckets: [bucket("Tech", "classified", 0.2)] }] }),
      "sector",
    )!;
    expect(t.groups[0].overflow).toBe(true);
    expect(t.reconciles).toBe(false);
  });
  it("returns null when the dimension is absent", () => {
    expect(buildExposureTreemap(rep, "country")).toBeNull();
  });
});

describe("buildLookThroughSankey", () => {
  const FUND = "IE00AAAAAAA1";
  const rep = report({
    funds: [fund(FUND, 0.6, { unknown_weight: 0.03 }), fund("IE00BBBBBBB2", 0.1, { status: "missing", holdings_as_of: null, unknown_weight: 0.1, rows_total_weight: null, residual_weight: null })],
    issuers: [
      issuer("apple", 0.15, {
        direct_weight: 0.05,
        fund_weight: 0.1,
        channels: [
          { kind: "direct", ticker: "AAPL", isin: null, label: "Direct", weight: 0.05, value_base: 50 },
          { kind: "fund", ticker: "AAAA", isin: FUND, label: "AAAA", weight: 0.1, value_base: 100 },
        ],
      }),
    ],
    exposures: [
      {
        dimension: "sector",
        reconciles: true,
        buckets: [bucket("Tech", "classified", 0.2, 0.1), bucket(KEY_CASH, "cash", 0.2, 0.2), bucket(KEY_NOT_LOOKED, "not_looked_through", 0.1)],
      },
    ],
  });
  const overlap: FundOverlapReport = {
    as_of: "2026-10-03",
    pairs: [],
    notes: [],
    funds: [{ isin: FUND, label: "AAAA", as_of: "2026-09-01", lag_days: 10, stale: false, equity_weight: 0.9, cash_derivative_other_weight: 0.07, unknown_weight: 0.03, unidentified_equity_weight: 0, hhi: null, effective_n: null, top10_share: null, issuers: 1 }],
  };

  it("preserves cash, the not-looked-through fund and the unknown remainder as own nodes", () => {
    const s = buildLookThroughSankey(rep, overlap)!;
    expect(s.kinds["out:cash"]).toBe("cash");
    expect(s.kinds["out:not-looked"]).toBe("not_looked_through");
    expect(s.kinds["out:unknown"]).toBe("unknown");
    expect(s.kinds["out:fund-cash"]).toBe("fund_cash");
    const unknown = s.links.find((l) => l.target === "out:unknown")!;
    expect(unknown.value).toBeCloseTo(0.03);
  });
  it("conserves portfolio weight: position column adds up to cash + direct + funds", () => {
    const s = buildLookThroughSankey(rep, overlap)!;
    expect(s.drawnTotal).toBeCloseTo(0.2 + 0.1 + 0.6 + 0.1);
    const outflow = (id: string) => s.links.filter((l) => l.source === id).reduce((a, l) => a + l.value, 0);
    const inflow = (id: string) => s.links.filter((l) => l.target === id).reduce((a, l) => a + l.value, 0);
    for (const id of [`src:${FUND}`, "src:direct", "src:cash"]) expect(outflow(id)).toBeCloseTo(inflow(id));
  });
  it("sends a fund's unexplained rest to Other issuers, not to named ones", () => {
    const s = buildLookThroughSankey(rep, overlap)!;
    // 0.6 - 0.03 unknown - 0.6*0.07 cash/derivatives - 0.1 apple = 0.428
    const other = s.links.find((l) => l.source === `src:${FUND}` && l.target === "out:other")!;
    expect(other.value).toBeCloseTo(0.6 - 0.03 - 0.042 - 0.1);
  });
  it("without the overlap report it does not split cash from other issuers", () => {
    const s = buildLookThroughSankey(rep, null)!;
    expect(s.kinds["out:rest"]).toBe("rest_of_fund");
    expect(s.kinds["out:fund-cash"]).toBeUndefined();
  });
  it("reports a gap instead of hiding it", () => {
    const s = buildLookThroughSankey(report({ ...rep, funds: [fund(FUND, 0.3)] }), overlap)!;
    expect(s.gap).toBeCloseTo(1 - (0.2 + 0.1 + 0.3));
    expect(s.notes.join(" ")).toContain("not drawn");
  });
  it("is null when the report is unavailable or has no exposures", () => {
    expect(buildLookThroughSankey(report({ available: false }), null)).toBeNull();
    expect(buildLookThroughSankey(report(), null)).toBeNull();
  });
});

describe("fundDataState", () => {
  it("maps statuses without inventing verification", () => {
    expect(fundDataState({ status: "missing", reconciles: null })).toBe("unavailable");
    expect(fundDataState({ status: "not_configured", reconciles: null })).toBe("not_covered");
    expect(fundDataState({ status: "stale", reconciles: true })).toBe("stale");
    expect(fundDataState({ status: "ok", reconciles: false })).toBe("partial");
    expect(fundDataState({ status: "ok", reconciles: true })).toBe("verified");
  });
});
