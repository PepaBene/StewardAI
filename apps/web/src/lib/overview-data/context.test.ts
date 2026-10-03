import { describe, expect, it } from "vitest";

import {
  concentrationRows,
  contextState,
  dec,
  overlapSummary,
  pctText,
  shareText,
  shareView,
  type PortfolioConcentration,
  type PortfolioDataContext,
} from "./context";

const share = (n: number | string, d: number | string, ratio: number | string | null, basis = "total portfolio value") => ({
  numerator: n,
  denominator: d,
  ratio,
  denominator_basis: basis,
});

function ctx(over: Partial<PortfolioDataContext> = {}): PortfolioDataContext {
  return {
    snapshot_date: "2026-10-01",
    known_as_of: "2026-10-03T00:00:00Z",
    base_currency: "EUR",
    total_value: "1000",
    position_count: 3,
    identity_coverage: share(1000, 1000, 1),
    country_concentration: null,
    product_concentration: null,
    macro_sensitivities: [],
    adjusted_return_coverage: null,
    fund_overlap: null,
    fund_unknown: null,
    fund_total_value: null,
    gaps: [],
    ...over,
  };
}

describe("dec", () => {
  it("parses numbers and decimal strings, rejects the rest", () => {
    expect(dec("12.5")).toBe(12.5);
    expect(dec(3)).toBe(3);
    expect(dec("abc")).toBeNull();
    expect(dec(null)).toBeNull();
    expect(dec("")).toBeNull();
  });
});

describe("shareView / shareText", () => {
  it("never reports a ratio over a zero denominator", () => {
    expect(shareView(share(5, 0, null))?.ratio).toBeNull();
    expect(shareView(share(5, 0, 1))?.ratio).toBeNull();
    expect(pctText(null)).toBe("n/a");
    expect(shareText(share(0, 0, null), (v) => String(v))).toContain("zero or unknown");
  });
  it("states the denominator beside the percentage", () => {
    expect(shareText(share("250", "1000", "0.25"), (v) => `€${v}`)).toBe("25.0% of €1000 total portfolio value");
  });
  it("derives the ratio when the backend omitted it", () => {
    expect(shareView(share(1, 4, null))?.ratio).toBe(0.25);
  });
});

describe("contextState", () => {
  it("is not_covered without a snapshot", () => {
    expect(contextState(ctx({ snapshot_date: null, position_count: 0, gaps: [{ domain: "portfolio", state: "missing", reason: "none" }] })).state).toBe("not_covered");
  });
  it("is partial with gaps or incomplete identity, verified otherwise", () => {
    expect(contextState(ctx({ gaps: [{ domain: "identity", state: "unresolved", reason: "x" }] })).state).toBe("partial");
    expect(contextState(ctx({ identity_coverage: share(500, 1000, 0.5) })).state).toBe("partial");
    expect(contextState(ctx()).state).toBe("verified");
  });
});

describe("concentrationRows", () => {
  const conc = (rows: PortfolioConcentration["rows"]): PortfolioConcentration => ({
    dimension: "country",
    mapped: share(500, 1000, 0.5),
    unmapped_value: 500,
    unmapped_tickers: ["X"],
    rows,
    hhi: null,
    hhi_denominator: 500,
    note: "",
  });
  it("sorts by exposed value, ties by key, and separates reported from inferred", () => {
    const row = (key: string, v: number, by: Record<string, number | string>) => ({
      key,
      exposed_value: v,
      share_of_portfolio: share(v, 1000, v / 1000),
      share_of_mapped: null,
      by_basis: by,
      holdings: 1,
    });
    const out = concentrationRows(conc([row("US", 100, { reported: 60, estimated: "40" }), row("DE", 100, { reported: 100 }), row("FR", 300, { user: 300 })]));
    expect(out.map((r) => r.key)).toEqual(["FR", "DE", "US"]);
    expect(out[2]).toMatchObject({ reported: 60, inferred: 40, ofMapped: null });
  });
  it("marks an unparsable basis amount null instead of 0", () => {
    const r = concentrationRows(conc([{ key: "US", exposed_value: 1, share_of_portfolio: share(1, 10, 0.1), share_of_mapped: null, by_basis: { reported: "bad" }, holdings: 1 }]));
    expect(r[0].reported).toBeNull();
  });
  it("is empty for null", () => expect(concentrationRows(null)).toEqual([]));
});

describe("overlapSummary", () => {
  it("returns null without a report and orders pairs by overlap", () => {
    expect(overlapSummary(null, null)).toBeNull();
    const s = overlapSummary(
      {
        as_of: "2026-10-01",
        notes: [],
        funds: [
          { isin: "A", label: "Fund A", stale: true },
          { isin: "B", label: "Fund B", stale: false },
        ] as never,
        pairs: [
          { fund_a: "A", fund_b: "B", overlap: 0.2, shared_issuers: 3, as_of_a: "", as_of_b: "", same_date: true },
          { fund_a: "B", fund_b: "A", overlap: 0.4, shared_issuers: 5, as_of_a: "", as_of_b: "", same_date: false },
        ],
      },
      0.1,
    );
    expect(s?.pairs[0]).toMatchObject({ overlap: 0.4, a: "Fund B" });
    expect(s?.staleFunds).toBe(1);
  });
});
