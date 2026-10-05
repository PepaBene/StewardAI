import { describe, expect, it } from "vitest";

import {
  concentrationSides,
  controlStakeRows,
  fundPositionRows,
  insiderMonthly,
  overlapRows,
  ownershipSeries,
  parseDiagnostics,
  parseSurprise,
} from "./holding-charts";

const tx = (date: string, shares: string | null, cls: string, counts = true) => ({
  transaction_date: date,
  filed: date,
  shares,
  economic_class: cls,
  counts_toward_net_discretionary: counts,
});

describe("insiderMonthly", () => {
  it("nets buys against sells and leaves quiet months as gaps", () => {
    const r = insiderMonthly({
      transactions: [
        tx("2026-01-10", "100", "open_market_buy"),
        tx("2026-01-20", "30", "open_market_sell"),
        tx("2026-03-02", "50", "open_market_sell"),
        tx("2026-02-02", "999", "grant", false),
        tx("2026-03-03", null, "open_market_buy"),
      ],
    });
    expect(r.months.map((m) => [m.month, m.net])).toEqual([["2026-01", 70], ["2026-02", null], ["2026-03", -50]]);
    expect(r.countedRows).toBe(3);
    expect(r.excludedRows).toBe(2);
  });
  it("is empty for junk", () => {
    expect(insiderMonthly(null).months).toEqual([]);
  });
});

describe("parseSurprise", () => {
  it("keeps a missing consensus as null with the reason", () => {
    const s = parseSurprise("revenue", "FY2025Q1", { state: "no_consensus_before_report", actual: { value: "5" } });
    expect(s.fraction).toBeNull();
    expect(s.actual).toBe(5);
    expect(s.reason).toMatch(/No consensus/);
  });
  it("reads the surprise fraction", () => {
    const s = parseSurprise("revenue", "FY2025Q1", { state: "available", surprise_percent: "0.05", consensus: { value: 100 }, actual: { value: 105 } });
    expect(s.fraction).toBe(0.05);
    expect(s.consensus).toBe(100);
  });
});

describe("concentrationSides", () => {
  it("adds an explicit undisclosed remainder and names the denominator", () => {
    const [c] = concentrationSides({
      periods: [
        { fiscal_period: "FY2024", relationship: "customer", share_basis: "issuer_revenue", named_share_sum: "0.3", anonymous_share_sum: "0.1", reported_share_sum: "0.4" },
        { fiscal_period: "FY2023", relationship: "customer", share_basis: "issuer_revenue", named_share_sum: "0.9", anonymous_share_sum: "0", reported_share_sum: "0.9" },
      ],
    });
    expect(c.period).toBe("FY2024");
    expect(c.denominator).toBe("the issuer's revenue");
    expect(c.segments.map((s) => s.label)).toEqual(["Named", "Unnamed (anonymous)", "Undisclosed"]);
    expect(c.segments[2].value).toBeCloseTo(0.6);
  });
  it("adds no remainder when shares exceed the total", () => {
    const [s] = concentrationSides({ periods: [{ fiscal_period: "FY1", relationship: "supplier", share_basis: "issuer_purchases", named_share_sum: 1.2, reported_share_sum: 1.2 }] });
    expect(s.exceedsTotal).toBe(true);
    expect(s.segments.map((x) => x.label)).toEqual(["Named"]);
  });
});

describe("stakes", () => {
  it("sorts largest first with unknown percentages last", () => {
    const rows = controlStakeRows({
      stakes: [
        { accession: "a", stake_percent: null, holders: [{ person_name: "A" }] },
        { accession: "b", stake_percent: "9.5", holders: [{ person_name: "B" }] },
        { accession: "c", stake_percent: "5.1", holders: [{ person_name: "C" }] },
      ],
    });
    expect(rows.map((r) => r.label)).toEqual(["B", "C", "A"]);
    expect(rows[2].percent).toBeNull();
  });
  it("reads fund positions", () => {
    const rows = fundPositionRows({ positions: [{ series_id: "S1", fund_name: "F", pct_of_fund: "1.2", report_period_end: "2026-06-30" }] });
    expect(rows[0]).toMatchObject({ label: "F", percent: 1.2, asOf: "2026-06-30" });
  });
});

describe("parseDiagnostics", () => {
  it("does not claim zero missing days without a calendar", () => {
    const [r] = parseDiagnostics([{ symbol: "X", source: "s", bar_type: "daily", expected_sessions: 0, observed_sessions: 10, missing_days: 0 }]);
    expect(r.missing).toBeNull();
  });
  it("puts the chosen series first", () => {
    const rows = parseDiagnostics([
      { symbol: "A", expected_sessions: 10, observed_sessions: 9, missing_days: 1 },
      { symbol: "B", chosen: true, expected_sessions: 10, observed_sessions: 10, missing_days: 0 },
    ]);
    expect(rows[0].label).toMatch(/^B/);
  });
});

describe("overlapRows", () => {
  it("sorts descending and keeps unknown overlap as null", () => {
    const rows = overlapRows({
      peers: [
        { peer: "P1", overlap_share: null, peer_families: 0, own_families: 4 },
        { peer: "P2", overlap_share: 0.25, peer_families: 3, own_families: 4 },
        { peer: "P3", overlap_share: 0.75, peer_families: 3, own_families: 4 },
      ],
    });
    expect(rows.map((r) => r.peer)).toEqual(["P3", "P2", "P1"]);
    expect(rows[2].share).toBeNull();
    expect(rows[2].reason).toMatch(/no attributed/i);
  });
});

describe("ownershipSeries", () => {
  it("returns oldest first and keeps nulls", () => {
    const s = ownershipSeries(
      [
        { period: "2026-06-30", holders: 10, pct_of_shares_outstanding: null },
        { period: "2026-03-31", holders: 8, pct_of_shares_outstanding: 0.1 },
      ],
      "pct",
    );
    expect(s).toEqual([
      { period: "2026-03-31", value: 0.1 },
      { period: "2026-06-30", value: null },
    ]);
  });
});
