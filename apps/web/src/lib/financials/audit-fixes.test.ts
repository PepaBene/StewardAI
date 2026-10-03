import { describe, expect, it } from "vitest";

import type { HoldingSegments } from "@/lib/api/client";

import {
  asOfParam,
  avcScaleMismatch,
  avcState,
  buildEstimates,
  buildSegmentSet,
  buildStatement,
  cashWalkFromFacts,
  debtFromFacts,
  peerComparison,
  peerState,
  revisionSlope,
  safeHref,
  sectionKey,
  segmentAlignment,
  surprise,
  localIsoDate,
} from "./logic";
import { parsePeerMetrics } from "./parse";
import type { ActualVsConsensus, ConsensusSeries, FactRow, ForwardObs, PeerMetricsPayload } from "./types";

let nextId = 1;
function fact(over: Partial<FactRow> & { lineItem: string; periodEnd: string }): FactRow {
  return {
    id: nextId++,
    concept: `us-gaap:${over.lineItem}`,
    label: null,
    statement: "income",
    periodStart: null,
    fiscalPeriod: null,
    basis: "US_GAAP",
    value: 1,
    unit: "USD",
    currency: "USD",
    dimensional: false,
    members: [],
    source: "SEC EDGAR",
    sourceUrl: null,
    publishedAt: null,
    knownAt: "2026-02-01T00:00:00Z",
    revision: 0,
    amendsFactId: null,
    amendsAccession: null,
    isAmendment: false,
    accession: `acc-${nextId}`,
    form: "10-K",
    reconciliation: "reconciled",
    checkNote: null,
    textBlock: false,
    ...over,
  };
}
type Over = Partial<Omit<FactRow, "lineItem" | "periodEnd">>;
const annual = (lineItem: string, year: number, value: number, over: Over = {}) =>
  fact({ lineItem, periodStart: `${year}-01-01`, periodEnd: `${year}-12-31`, fiscalPeriod: `FY${year}`, value, ...over });
const span = (lineItem: string, start: string, end: string, value: number, fiscalPeriod: string | null, over: Over = {}) =>
  fact({ lineItem, periodStart: start, periodEnd: end, fiscalPeriod, value, ...over });

describe("F4-01 surprise against a negative consensus", () => {
  it("a narrower loss is a positive surprise (abs of the expectation)", () => {
    const s = surprise({ actual: { value: -0.4 } as ActualVsConsensus["actual"], consensus: { value: -0.5, low: -0.6, high: -0.4 } as ActualVsConsensus["consensus"] });
    expect(s.pct).toBeCloseTo(0.2);
  });
  it("a wider loss is negative, a positive consensus is unchanged, the API figure wins", () => {
    const c = (v: number) => ({ value: v, low: null, high: null }) as ActualVsConsensus["consensus"];
    expect(surprise({ actual: { value: -0.6 } as ActualVsConsensus["actual"], consensus: c(-0.5) }).pct).toBeCloseTo(-0.2);
    expect(surprise({ actual: { value: 1.1 } as ActualVsConsensus["actual"], consensus: c(1) }).pct).toBeCloseTo(0.1);
    expect(surprise({ actual: { value: 1.1 } as ActualVsConsensus["actual"], consensus: c(1), surprisePct: 0.5 }).pct).toBe(0.5);
  });
});

describe("F4-02 estimates fan keeps one period length", () => {
  const obs = (fiscalPeriod: string, value: number): ForwardObs => ({
    id: nextId++, kind: "published_analyst_consensus", provider: "P", metric: "revenue", basis: null, fiscalPeriod, periodEnd: null, currency: "USD",
    value, low: null, high: null, analystCount: 5, vintage: null, publishedAt: null, knownAt: "2026-01-01T00:00:00Z", sourceUrl: null,
  });
  const facts = [
    annual("revenue", 2025, 400),
    span("revenue", "2025-01-01", "2025-03-31", 95, "FY2025Q1"),
    span("revenue", "2025-04-01", "2025-06-30", 100, "FY2025Q2"),
    span("revenue", "2025-07-01", "2025-09-30", 105, "FY2025Q3"),
  ];
  const series = (p: string): ConsensusSeries => ({ metric: "revenue", fiscalPeriod: p, provider: "P", currency: "USD", basis: null, vintages: [], revisionSincePrevious: null });

  it("annual consensus: actuals are annual only", () => {
    const m = buildEstimates({ observations: [obs("FY2026", 420)], series: [series("FY2026")] }, [...facts, annual("revenue", 2024, 380)], "revenue");
    expect(m.actuals.map((a) => a.period)).toEqual(["FY2024", "FY2025"]);
    expect(m.periods).toEqual(["FY2024", "FY2025", "FY2026"]);
  });
  it("quarterly consensus: actuals are quarterly only, chronological, and H1 sorts after Q1", () => {
    const m = buildEstimates({ observations: [obs("FY2025Q4", 110), obs("FY2026Q1", 112)], series: [series("FY2025Q4"), series("FY2026Q1")] }, facts, "revenue");
    expect(m.actuals.map((a) => a.period)).toEqual(["FY2025Q1", "FY2025Q2", "FY2025Q3"]);
    expect(m.periods).toEqual(["FY2025Q1", "FY2025Q2", "FY2025Q3", "FY2025Q4", "FY2026Q1"]);
    const mixed = buildEstimates({ observations: [obs("FY2026H1", 1), obs("FY2026Q1", 1)], series: [series("FY2026H1"), series("FY2026Q1")] }, [], "revenue");
    expect(mixed.points.map((p) => p.fiscalPeriod)).toEqual(["FY2026Q1", "FY2026H1"]);
  });
});

describe("F4-03 concept-aware picks", () => {
  const cf = (concept: string, value: number, lineItem = "net_income", statement = "income") => annual(lineItem, 2025, value, { concept, statement });
  const walk = (rows: FactRow[]) => cashWalkFromFacts([annual("cash_from_operations", 2025, 100, { statement: "cash_flow" }), ...rows]);
  it("net income does not depend on insertion order", () => {
    const a = walk([cf("us-gaap:ProfitLoss", 90), cf("us-gaap:NetIncomeLoss", 80)]);
    const b = walk([cf("us-gaap:NetIncomeLoss", 80), cf("us-gaap:ProfitLoss", 90)]);
    expect(a.walk!.netIncome).toBe(80);
    expect(b.walk!.netIncome).toBe(80);
  });
  it("capex prefers PP&E payments over productive assets, either order", () => {
    const pp = cf("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", 30, "capex", "cash_flow");
    const prod = cf("us-gaap:PaymentsToAcquireProductiveAssets", 50, "capex", "cash_flow");
    const ni = cf("us-gaap:NetIncomeLoss", 80);
    expect(walk([ni, prod, pp]).walk!.capex).toBe(30);
    expect(walk([ni, pp, prod]).walk!.capex).toBe(30);
  });
  it("debt ladder tranche uses the same rule (stable under reordering)", () => {
    const at = (concept: string, value: number) => fact({ lineItem: "debt_maturity_y1", statement: "balance_sheet", periodEnd: "2025-12-31", value, concept });
    expect(debtFromFacts([at("x:B", 2), at("x:A", 1)])!.tranches[0].amount).toBe(debtFromFacts([at("x:A", 1), at("x:B", 2)])!.tranches[0].amount);
  });
});

describe("F4-04 a tag switch does not blank periods", () => {
  it("each period uses the concept filed for it and the switch is reported", () => {
    const rev = (year: number, concept: string, value: number) => annual("revenue", year, value, { concept });
    const v = buildStatement([rev(2019, "us-gaap:Revenues", 9), rev(2020, "us-gaap:Revenues", 9), rev(2021, "us-gaap:SalesRevenueNet", 5), rev(2022, "us-gaap:SalesRevenueNet", 6), rev(2023, "us-gaap:SalesRevenueNet", 7), rev(2024, "us-gaap:Revenues", 20)], "income", "annual");
    const line = v.lines[0];
    expect(line.periods.map((p) => p.versions.length)).toEqual([1, 1, 1, 1, 1, 1]);
    expect(line.concepts).toHaveLength(2);
  });
});

describe("F4-05 quarterly view", () => {
  const q = (lineItem: string, start: string, end: string, value: number, fp: string, over: Over = {}) => span(lineItem, start, end, value, fp, over);
  it("keeps the year-end balance sheet as the fourth quarter", () => {
    const facts = [
      q("revenue", "2025-01-01", "2025-03-31", 1, "FY2025Q1"),
      fact({ lineItem: "total_assets", statement: "balance_sheet", periodEnd: "2025-03-31", value: 10, fiscalPeriod: "FY2025Q1" }),
      annual("revenue", 2025, 4),
      fact({ lineItem: "total_assets", statement: "balance_sheet", periodEnd: "2025-12-31", value: 12, fiscalPeriod: "FY2025" }),
    ];
    const v = buildStatement(facts, "balance_sheet", "quarter");
    expect(v.periods.map((p) => p.end)).toEqual(["2025-03-31", "2025-12-31"]);
    expect(v.notes?.join(" ")).toMatch(/year-end/);
  });
  it("derives discrete cash-flow quarters from year-to-date figures and says so", () => {
    const cf = (start: string, end: string, value: number, fp: string) => q("cash_from_operations", start, end, value, fp, { statement: "cash_flow" });
    const facts = [cf("2025-01-01", "2025-03-31", 10, "FY2025Q1"), cf("2025-01-01", "2025-06-30", 25, "FY2025H1"), cf("2025-01-01", "2025-09-30", 45, "FY2025M9"), cf("2025-01-01", "2025-12-31", 70, "FY2025")];
    const v = buildStatement(facts, "cash_flow", "quarter");
    const vals = v.lines[0].periods.map((p) => p.versions[0]?.value);
    expect(vals).toEqual([10, 15, 20, 25]);
    expect(v.periods.map((p) => p.label)).toEqual(["FY2025Q1", "FY2025Q2", "FY2025Q3", "FY2025Q4"]);
    expect(v.derivedCount).toBe(3);
    expect(v.notes?.join(" ")).toMatch(/derived/);
  });
  it("without the earlier YTD it does not invent a quarter and explains", () => {
    const facts = [q("cash_from_operations", "2025-01-01", "2025-03-31", 10, "FY2025Q1", { statement: "cash_flow" }), q("cash_from_operations", "2025-01-01", "2025-09-30", 45, "FY2025M9", { statement: "cash_flow" })];
    const v = buildStatement(facts, "cash_flow", "quarter");
    expect(v.periods).toHaveLength(1);
    expect(v.notes?.join(" ")).toMatch(/cumulative/);
  });
});

describe("F4-06 / F7-5 section key and as-of", () => {
  it("the key changes with asOf and reconciledOnly", () => {
    const k = sectionKey("AAPL", null, false, 0);
    expect(sectionKey("AAPL", "2024-03-01", false, 0)).not.toBe(k);
    expect(sectionKey("AAPL", null, true, 0)).not.toBe(k);
    expect(sectionKey("MSFT", null, false, 0)).not.toBe(k);
  });
});

describe("F4-08 duplicate peers", () => {
  it("parse dedupes by ticker and the comparison counts distinct tickers", () => {
    const body = { covered_peer_metrics: {}, confirmed_peers: [{ peer_ticker: "AAA", source: "finnhub" }, { peer_ticker: "AAA", source: "owner" }, { peer_ticker: "BBB" }] };
    expect(parsePeerMetrics(body)!.confirmedPeers.map((p) => p.peerTicker)).toEqual(["AAA", "BBB"]);
    const p: PeerMetricsPayload = {
      confirmedPeers: [{ peerTicker: "AAA", basis: null, source: "finnhub" }, { peerTicker: "AAA", basis: null, source: "owner" }, { peerTicker: "BBB", basis: null, source: null }],
      metrics: [{ id: "gross_margin", mean: 0.5, coveredPeers: 2, included: ["AAA", "BBB"], excluded: [], excludedReasons: {}, holdingValue: 0.6, holdingReason: null, periodEnd: null, periodStart: null, basis: null }],
      excludedPeers: [], holdingPeriod: { periodStart: null, periodEnd: null, basis: null, unit: null, reconciliation: "reconciled", unreconciledInputs: [] },
      reconciledOnly: false, formulas: {}, universe: null, knownAsOf: null,
    };
    const c = peerComparison(p, "gross_margin")!;
    expect([c.covered, c.total]).toEqual([2, 2]);
    expect(peerState(p, c).state).toBe("verified");
  });
});

describe("F4-09 segment profit axis", () => {
  const seg = (label: string, member: string, value: number) => ({ label, member, value, share: null, subtotal: false, label_source: "filing" as const });
  const data = {
    ticker: "X", basis: "annual", available_bases: ["annual"], period: { start: "2025-01-01", end: "2025-12-31", months: 12, basis: "annual", label: "FY2025" }, currency: "USD",
    geographic: [], segment: [seg("Services", "x:ServicesMember", 600), seg("Devices", "x:DevicesMember", 300)], product: [], total_revenue: 900, history: {}, source: null, as_of: "2026-02-01", notes: [],
  } as unknown as HoldingSegments;
  const profit = (axes: [string, string][], value: number, over: Over = {}) =>
    fact({ lineItem: "operating_income", statement: "segment", periodStart: "2025-01-01", periodEnd: "2025-12-31", value, dimensional: true, members: axes.map(([axis, member]) => ({ axis, member })), ...over });
  const SEG = "us-gaap:StatementBusinessSegmentsAxis";
  it("ignores a same-named member on another axis", () => {
    const set = buildSegmentSet(data, "segment", [profit([["us-gaap:ProductOrServiceAxis", "x:ServicesMember"]], 5), profit([[SEG, "x:DevicesMember"]], 30)])!;
    expect(set.segments.find((s) => s.name === "Services")!.operatingProfit).toBeNull();
    expect(set.segments.find((s) => s.name === "Devices")!.operatingProfit).toBe(30);
    expect(segmentAlignment(set).margin.aligned).toBe(false);
  });
  it("accepts a business-segment fact with the consolidation axis, and refuses another currency", () => {
    const ok = buildSegmentSet(data, "segment", [profit([[SEG, "x:ServicesMember"], ["us-gaap:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"]], 90), profit([[SEG, "x:DevicesMember"]], 30)])!;
    expect(segmentAlignment(ok).margin.aligned).toBe(true);
    const eur = buildSegmentSet(data, "segment", [profit([[SEG, "x:ServicesMember"]], 90, { currency: "EUR", unit: "EUR" }), profit([[SEG, "x:DevicesMember"]], 30)])!;
    expect(eur.segments.find((s) => s.name === "Services")!.operatingProfit).toBeNull();
  });
});

describe("F4-10 free-text vintage", () => {
  it("never yields NaN days and orders by known_at", () => {
    const o = (value: number, vintage: string, knownAt: string): ForwardObs => ({
      id: nextId++, kind: "published_analyst_consensus", provider: "P", metric: "revenue", basis: null, fiscalPeriod: "FY2026", periodEnd: null, currency: "USD",
      value, low: null, high: null, analystCount: null, vintage, publishedAt: null, knownAt, sourceUrl: null,
    });
    const r = revisionSlope({ fiscalPeriod: "FY2026", vintages: [o(10, "Q1 2026 preview", "2026-01-01T00:00:00Z"), o(11, "Q1 2026 update", "2026-01-31T00:00:00Z")] })!;
    expect(r.days).toBe(30);
    expect(Number.isFinite(r.perMonthPct as number)).toBe(true);
    expect(r.from.vintage).toBe("2026-01-01");
    expect(r.snapshots).toBe(2);
  });
});

describe("F4-12 actual vs consensus guards", () => {
  const avc = (actual: number, mean: number, reconciled = true): ActualVsConsensus => ({
    state: "available", reason: null, metric: "revenue", fiscalPeriod: "FY2025", surprise: null, surprisePct: null,
    actual: { value: actual, unit: "USD", currency: "USD", knownAt: null, accession: null, reconciled },
    consensus: { provider: "P", value: mean, low: null, high: null, analystCount: null, currency: "USD", basis: null, vintage: null, knownAt: null },
  });
  it("a million-fold gap is a scale mismatch, not a surprise", () => {
    expect(avcScaleMismatch(avc(5_000_000_000, 5000))).toBe(true);
    expect(avcState(avc(5_000_000_000, 5000)).state).toBe("disputed");
    expect(avcScaleMismatch(avc(105, 100))).toBe(false);
  });
  it("an unconfirmed reconciliation is not claimed to be unchecked", () => {
    expect(avcState(avc(105, 100, false)).reason).toMatch(/does not say/);
  });
});

describe("F4-14 as-of boundary", () => {
  it("includes the whole last second of the chosen day, and the picker uses the local date", () => {
    expect(asOfParam("2026-03-01")).toBe("2026-03-01T23:59:59.999999Z");
    expect(asOfParam(null)).toBeUndefined();
    expect(localIsoDate(new Date(2026, 2, 1, 0, 30))).toBe("2026-03-01");
  });
});

describe("F4-15 link scheme", () => {
  it("only http(s) is a link", () => {
    expect(safeHref("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,x")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });
});
