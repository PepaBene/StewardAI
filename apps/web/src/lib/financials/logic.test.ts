import { describe, expect, it } from "vitest";

import {
  CASH_WALK_FIXTURE,
  DEBT_FIXTURE,
  EARNINGS_FIXTURE,
  ESTIMATES_FIXTURE,
  PEERS_FIXTURE,
  SEGMENTS_FIXTURE,
  SEGMENTS_MISALIGNED_FIXTURE,
  STATEMENTS_FIXTURE,
} from "./fixture";
import {
  cashWalk,
  comparableSnapshots,
  debtBreakdown,
  knownDates,
  linePoints,
  peerComparison,
  restatements,
  revisionSlope,
  segmentAlignment,
  segmentTiles,
  surprise,
  versionAt,
} from "./logic";

const revenue = STATEMENTS_FIXTURE.lines.find((l) => l.id === "revenue")!;
const fy = (p: string) => revenue.periods.find((x) => x.period === p)!;

describe("as-filed versions and point-in-time", () => {
  it("shows the restated value in the current view but the original in an old as-of view", () => {
    expect(versionAt(fy("FY2023"), { kind: "latest" })!.value).toBe(22640);
    expect(versionAt(fy("FY2023"), { kind: "original" })!.value).toBe(23880);
    // Before the 2025 restatement was filed, an as-of report still sees the original.
    expect(versionAt(fy("FY2023"), { kind: "as_of", date: "2024-12-31" })!.value).toBe(23880);
    expect(versionAt(fy("FY2023"), { kind: "as_of", date: "2025-02-18" })!.value).toBe(22640);
  });

  it("returns null for a period not yet filed on the as-of date, never a later value", () => {
    expect(versionAt(fy("FY2025"), { kind: "as_of", date: "2026-01-31" })).toBeNull();
    const pts = linePoints(revenue, { kind: "as_of", date: "2024-03-01" });
    expect(pts.map((p) => p.value)).toEqual([18420, 21310, 23880, null, null]);
    expect(pts.every((p) => !p.restated)).toBe(true);
  });

  it("flags restated points under the latest view and lists what a restatement changed", () => {
    const pts = linePoints(revenue, { kind: "latest" });
    expect(pts.filter((p) => p.restated).map((p) => p.period)).toEqual(["FY2022", "FY2023"]);
    expect(restatements(revenue)).toEqual([
      { period: "FY2022", original: 21310, latest: 20180, delta: -1130, filedAt: "2025-02-18" },
      { period: "FY2023", original: 23880, latest: 22640, delta: -1240, filedAt: "2025-02-18" },
    ]);
  });

  it("offers every filing date, newest first", () => {
    const d = knownDates(STATEMENTS_FIXTURE.lines);
    expect(d[0]).toBe("2026-02-20");
    expect([...d].sort().reverse()).toEqual(d);
  });
});

describe("cash walk", () => {
  it("reconciles the fixture to the filed totals", () => {
    const r = cashWalk(CASH_WALK_FIXTURE);
    expect(r.derivedCfo).toBe(3620);
    expect(r.derivedFcf).toBe(2310);
    expect(r.reconciles).toBe(true);
    expect(r.fcfConversion).toBeCloseTo(2310 / 2810);
    expect(r.steps.map((s) => s.kind)).toEqual(["start", "delta", "delta", "delta", "delta", "subtotal", "delta", "end"]);
  });

  it("builds each step from the previous level", () => {
    const r = cashWalk(CASH_WALK_FIXTURE);
    expect(r.steps[1]).toMatchObject({ from: 2810, to: 3990 });
    expect(r.steps[3]).toMatchObject({ from: 4230, to: 3710 });
    expect(r.steps[6]).toMatchObject({ from: 3620, to: 2310 });
  });

  it("flags parts that do not explain the filed total", () => {
    const r = cashWalk({ ...CASH_WALK_FIXTURE, adjustments: CASH_WALK_FIXTURE.adjustments.slice(0, 3) });
    expect(r.cfoGap).toBe(90);
    expect(r.reconciles).toBe(false);
  });

  it("gives no conversion ratio when net income is not positive", () => {
    expect(cashWalk({ ...CASH_WALK_FIXTURE, netIncome: -50 }).fcfConversion).toBeNull();
  });
});

describe("debt maturities", () => {
  const b = debtBreakdown(DEBT_FIXTURE);

  it("buckets by year and currency in the reporting currency", () => {
    expect(b.buckets.map((x) => x.label)).toEqual(["2026", "2027", "2028", "2029", "2030", "2031", "After 2031"]);
    expect(b.buckets[1].byCurrency).toEqual({ USD: 800, EUR: 545 });
    expect(b.buckets[1].total).toBeCloseTo(1345);
    expect(b.currencies).toEqual(["USD", "EUR"]);
    expect(b.hasNonReportingCurrency).toBe(true);
  });

  it("keeps the unclassified remainder explicit and never negative", () => {
    expect(b.classified).toBeCloseTo(450 + 800 + 545 + 600 + 763 + 900 + 1100 + 900);
    expect(b.unclassified).toBeCloseTo(6740 - b.classified);
    expect(b.unclassified).toBeGreaterThan(0);
    const over = debtBreakdown({ ...DEBT_FIXTURE, reportedTotal: 1000 });
    expect(over.unclassified).toBe(0);
    expect(over.overClassified).toBe(true);
  });

  it("skips a tranche whose currency has no FX rate instead of guessing", () => {
    const x = debtBreakdown({ ...DEBT_FIXTURE, fxToReporting: {} });
    expect(x.currencies).toEqual(["USD"]);
    expect(x.classified).toBe(450 + 800 + 600 + 900 + 1100 + 900);
  });
});

describe("segments", () => {
  it("colours by growth and margin when the data is aligned", () => {
    const a = segmentAlignment(SEGMENTS_FIXTURE);
    expect(a.growth.aligned && a.margin.aligned).toBe(true);
    const g = segmentTiles(SEGMENTS_FIXTURE, "growth");
    expect(g.items.find((i) => i.id === "Automation")!.change).toBeCloseTo(9800 / 8900 - 1);
    expect(segmentTiles(SEGMENTS_FIXTURE, "margin").items.find((i) => i.id === "Services")!.change).toBeCloseTo(790 / 3950);
    expect(g.items.reduce((s, i) => s + i.weight, 0)).toBe(SEGMENTS_FIXTURE.totalRevenue);
  });

  it("drops the colour, not the area, when profit definitions differ", () => {
    const a = segmentAlignment(SEGMENTS_MISALIGNED_FIXTURE);
    expect(a.margin.aligned).toBe(false);
    expect(a.margin.reason).toMatch(/defined differently/);
    const t = segmentTiles(SEGMENTS_MISALIGNED_FIXTURE, "margin");
    expect(t.colored).toBe(false);
    expect(t.items).toHaveLength(4);
    expect(t.items.every((i) => i.change === null)).toBe(true);
  });

  it("requires a prior-year figure for every segment before colouring by growth", () => {
    expect(segmentAlignment(SEGMENTS_MISALIGNED_FIXTURE).growth).toEqual({ aligned: false, reason: "No prior-year revenue for Services." });
  });
});

describe("estimates", () => {
  const ref = ESTIMATES_FIXTURE.forward.find((p) => p.kind === "published_analyst_consensus" && p.period === "FY2026")!;

  it("computes the revision of the consensus mean across comparable snapshots", () => {
    const r = revisionSlope(ESTIMATES_FIXTURE.snapshots, ref)!;
    expect(r.from).toEqual({ vintage: "2026-03-02", mean: 4.31 });
    expect(r.to).toEqual({ vintage: "2026-09-01", mean: 4.52 });
    expect(r.change).toBeCloseTo(0.21);
    expect(r.pct).toBeCloseTo(0.21 / 4.31);
    expect(r.days).toBe(183);
  });

  it("reports a downward revision as negative", () => {
    const fy27 = ESTIMATES_FIXTURE.forward.find((p) => p.kind === "published_analyst_consensus" && p.period === "FY2027")!;
    expect(revisionSlope(ESTIMATES_FIXTURE.snapshots, fy27)!.pct!).toBeLessThan(0);
  });

  it("never joins snapshots of a different basis: FY2028 has one comparable snapshot, so no slope", () => {
    const fy28 = ESTIMATES_FIXTURE.forward.find((p) => p.kind === "published_analyst_consensus" && p.period === "FY2028")!;
    expect(comparableSnapshots(ESTIMATES_FIXTURE.snapshots, fy28)).toHaveLength(1);
    expect(revisionSlope(ESTIMATES_FIXTURE.snapshots, fy28)).toBeNull();
  });

  it("measures surprise against the range, and says nothing without a consensus", () => {
    const [eps, rev] = EARNINGS_FIXTURE;
    expect(surprise(eps).position).toBe("above_range");
    expect(surprise(eps).pct).toBeCloseTo(1.42 / 1.35 - 1);
    expect(surprise(rev)).toEqual({ pct: null, position: "no_range" });
    expect(surprise({ actual: 1.3, consensusMean: 1.35, consensusLow: 1.28, consensusHigh: 1.41 }).position).toBe("within_range");
  });

  it("never invents a consensus: periods without one are listed as missing with a reason", () => {
    expect(ESTIMATES_FIXTURE.missing[0]).toMatchObject({ period: "FY2029", state: "paid_only" });
    expect(ESTIMATES_FIXTURE.forward.some((p) => p.period === "FY2029")).toBe(false);
  });

  it("keeps the four forward kinds apart and gives only consensus an analyst count", () => {
    const kinds = new Set(ESTIMATES_FIXTURE.forward.map((p) => p.kind));
    expect(kinds).toEqual(new Set(["company_guidance", "published_analyst_consensus", "owner_scenario", "event_calendar_estimate"]));
    expect(ESTIMATES_FIXTURE.forward.filter((p) => p.analystCount !== null).every((p) => p.kind === "published_analyst_consensus")).toBe(true);
  });
});

describe("peers", () => {
  it("excludes a peer on a different definition, another fiscal period or a missing value, with the reason", () => {
    const c = peerComparison(PEERS_FIXTURE, "roic");
    expect(c.rows.map((r) => r.ticker)).toEqual(["ALDC", "FIXI", "BRND"]);
    expect(c.excluded.map((e) => e.ticker).sort()).toEqual(["CRVP", "DNMR", "ESKF"]);
    expect(c.excluded.find((e) => e.ticker === "CRVP")!.reason).toMatch(/different definition/);
    expect(c.excluded.find((e) => e.ticker === "DNMR")!.reason).toMatch(/FY2024/);
    expect(c.excluded.find((e) => e.ticker === "ESKF")!.reason).toMatch(/No value/);
    expect(c.covered).toBe(2);
    expect(c.total).toBe(5);
  });

  it("allows a GAAP and an IFRS peer when both use the same definition", () => {
    const c = peerComparison(PEERS_FIXTURE, "operating_margin");
    expect(c.rows.map((r) => r.basis)).toContain("IFRS");
    expect(c.rows.find((r) => r.ticker === "FIXI")!.subject).toBe(true);
  });

  it("takes the peer median over covered peers only, without the subject", () => {
    const c = peerComparison(PEERS_FIXTURE, "leverage");
    // Covered peers: 0.6, 0.9, 1.9, 2.3 (Dunmore is excluded for its fiscal period).
    expect(c.covered).toBe(4);
    expect(c.peerMedian).toBeCloseTo(1.4);
    expect(peerComparison({ ...PEERS_FIXTURE, peers: [] }, "roic").peerMedian).toBeNull();
  });
});
