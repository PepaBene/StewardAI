import { describe, expect, it } from "vitest";

import type { HoldingSegments } from "@/lib/api/client";

import {
  actualsFor,
  avcState,
  buildEstimates,
  buildSegmentSet,
  buildStatement,
  cashWalk,
  cashWalkFromFacts,
  debtBreakdown,
  debtFromFacts,
  knownDates,
  linePoints,
  peerComparison,
  peerState,
  periodClass,
  restatements,
  revisionSlope,
  segmentAlignment,
  segmentTiles,
  statementState,
  surprise,
  versionAt,
} from "./logic";
import type { ConsensusSeries, FactRow, ForwardObs, PeerMetricsPayload } from "./types";

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
    knownAt: "2025-02-01T00:00:00Z",
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
const flow = (lineItem: string, year: number, value: number, over: Over = {}) =>
  fact({ lineItem, periodStart: `${year}-01-01`, periodEnd: `${year}-12-31`, fiscalPeriod: `FY${year}`, value, ...over });

describe("periodClass", () => {
  it("separates annual, quarter, other and instant", () => {
    expect(periodClass({ periodStart: "2025-01-01", periodEnd: "2025-12-31" })).toBe("annual");
    expect(periodClass({ periodStart: "2025-01-01", periodEnd: "2025-03-31" })).toBe("quarter");
    expect(periodClass({ periodStart: "2025-01-01", periodEnd: "2025-06-30" })).toBe("other");
    expect(periodClass({ periodStart: null, periodEnd: "2025-12-31" })).toBe("instant");
  });
});

describe("as-filed statements, versions and restatements", () => {
  const facts = [
    flow("revenue", 2023, 23880, { knownAt: "2024-02-20T00:00:00Z", accession: "a1" }),
    flow("revenue", 2023, 22640, { knownAt: "2025-02-18T00:00:00Z", accession: "a2", amendsFactId: 1, revision: 1 }),
    flow("revenue", 2024, 25000, { knownAt: "2025-02-18T00:00:00Z", accession: "a2" }),
  ];
  const view = buildStatement(facts, "income", "annual");
  const line = view.lines.find((l) => l.id === "revenue")!;

  it("keeps each filing as a version and marks the restating one", () => {
    const fy23 = line.periods.find((p) => p.label === "FY2023")!;
    expect(fy23.versions.map((v) => v.value)).toEqual([23880, 22640]);
    expect(versionAt(fy23, { kind: "latest" })!.value).toBe(22640);
    expect(versionAt(fy23, { kind: "original" })!.value).toBe(23880);
    expect(linePoints(line, { kind: "latest" })[0].restated).toBe(true);
    expect(linePoints(line, { kind: "original" })[0].restated).toBe(false);
  });

  it("lists only value changes as restatements", () => {
    expect(restatements(line)).toEqual([{ period: "FY2023", original: 23880, latest: 22640, delta: -1240, filedAt: "2025-02-18T00:00:00Z" }]);
  });

  it("returns a null value for a period with nothing filed, never zero", () => {
    const withGap = buildStatement([...facts, flow("net_income", 2024, 5)], "income", "annual");
    const ni = withGap.lines.find((l) => l.id === "net_income")!;
    const pts = linePoints(ni, { kind: "latest" });
    expect(pts.map((p) => p.value)).toEqual([null, 5]);
  });

  it("lists filing dates newest first", () => {
    expect(knownDates(view.lines)).toEqual(["2025-02-18", "2024-02-20"]);
  });

  it("never mixes a quarter into the annual view, nor a USD line with a EUR line", () => {
    const q = fact({ lineItem: "revenue", periodStart: "2024-01-01", periodEnd: "2024-03-31", value: 99 });
    const eur = flow("revenue", 2024, 7, { unit: "EUR", currency: "EUR" });
    const v = buildStatement([...facts, q, eur], "income", "annual");
    const l = v.lines.find((x) => x.id === "revenue")!;
    expect(l.unit).toBe("USD");
    expect(l.periods.flatMap((p) => p.versions.map((x) => x.value))).not.toContain(99);
    expect(l.periods.flatMap((p) => p.versions.map((x) => x.value))).not.toContain(7);
  });

  it("joins an instant balance-sheet fact to the annual period that ends on its date", () => {
    const bs = fact({ lineItem: "total_assets", statement: "balance_sheet", periodEnd: "2024-12-31", value: 500 });
    const all = [...facts, bs];
    const v = buildStatement(all, "balance_sheet", "annual");
    expect(v.lines.find((l) => l.id === "total_assets")!.periods.map((p) => p.end)).toContain("2024-12-31");
    expect(buildStatement(all, "balance_sheet", "quarter").lines).toHaveLength(0);
  });

  it("leaves failed facts out, counts them, and reports the state", () => {
    const bad = flow("gross_profit", 2024, 9, { reconciliation: "failed" });
    const unchecked = flow("cost_of_revenue", 2024, 3, { reconciliation: "unchecked" });
    const v = buildStatement([...facts, bad, unchecked], "income", "annual");
    expect(v.lines.find((l) => l.id === "gross_profit")).toBeUndefined();
    expect(v.counts).toMatchObject({ failed: 1, unchecked: 1 });
    expect(statementState(v).state).toBe("disputed");
    expect(statementState(buildStatement([...facts, unchecked], "income", "annual")).state).toBe("partial");
    expect(statementState(buildStatement(facts, "income", "annual")).state).toBe("verified");
  });

  it("is not covered with no facts at all", () => {
    expect(statementState(buildStatement([], "income", "annual")).state).toBe("not_covered");
  });

  it("ignores segment (dimensional) facts in the consolidated statement", () => {
    const seg = flow("revenue", 2024, 1, { dimensional: true, members: [{ axis: "a", member: "b" }] });
    const v = buildStatement([...facts, seg], "income", "annual");
    expect(v.counts.total).toBe(3);
  });

  it("uses one concept per line: the one with most periods", () => {
    const other = flow("revenue", 2024, 1, { concept: "us-gaap:Revenues" });
    const v = buildStatement([...facts, other], "income", "annual");
    expect(v.lines.find((l) => l.id === "revenue")!.concept).toBe("us-gaap:revenue");
  });
});

describe("cash walk from filed facts", () => {
  const base = (over: Over = {}) => ({ statement: "cash_flow", ...over });
  const closing = [
    flow("net_income", 2025, 100),
    flow("cash_from_operations", 2025, 180, base()),
    flow("depreciation_amortization", 2025, 50, base()),
    flow("share_based_compensation", 2025, 30, base()),
    flow("capex", 2025, -40, base()),
  ];

  it("reconciles when net income plus filed adjustments equals filed CFO", () => {
    const r = cashWalkFromFacts(closing);
    expect(r.walk).not.toBeNull();
    const w = cashWalk(r.walk!);
    expect(w.reconciles).toBe(true);
    expect(w.fcf).toBe(140);
    expect(w.fcfConversion).toBeCloseTo(1.4);
    expect(w.steps.map((s) => s.kind)).toEqual(["start", "delta", "delta", "subtotal", "delta", "end"]);
  });

  it("states the unexplained gap rather than closing it, when parts do not add up", () => {
    const r = cashWalkFromFacts([flow("net_income", 2025, 100), flow("cash_from_operations", 2025, 300, base()), flow("depreciation_amortization", 2025, 50, base())]);
    const w = cashWalk(r.walk!);
    expect(w.reconciles).toBe(false);
    expect(w.cfoGap).toBe(-150);
    expect(w.fcf).toBeNull();
  });

  it("is unavailable without cash from operations, or with a failed one", () => {
    expect(cashWalkFromFacts([flow("net_income", 2025, 1)]).walk).toBeNull();
    const failed = cashWalkFromFacts([flow("net_income", 2025, 1), flow("cash_from_operations", 2025, 1, { reconciliation: "failed" })]);
    expect(failed.walk).toBeNull();
    expect(failed.reason).toMatch(/failed/);
  });

  it("does not join net income of another unit", () => {
    const r = cashWalkFromFacts([flow("net_income", 2025, 1, { unit: "EUR", currency: "EUR" }), flow("cash_from_operations", 2025, 1, base())]);
    expect(r.walk).toBeNull();
  });

  it("picks the latest filed version of a value", () => {
    const r = cashWalkFromFacts([flow("net_income", 2025, 100), flow("net_income", 2025, 90, { knownAt: "2026-03-01T00:00:00Z" }), flow("cash_from_operations", 2025, 90, base())]);
    expect(r.walk!.netIncome).toBe(90);
  });

  it("gives no conversion for non-positive net income", () => {
    const r = cashWalkFromFacts([flow("net_income", 2025, -5), flow("cash_from_operations", 2025, 10, base()), flow("capex", 2025, 3, base())]);
    expect(cashWalk(r.walk!).fcfConversion).toBeNull();
  });
});

describe("debt maturities", () => {
  const at = (lineItem: string, value: number, over: Over = {}) => fact({ lineItem, statement: "balance_sheet", periodEnd: "2025-12-31", value, ...over });
  const ladder = [at("debt_maturity_y1", 100), at("debt_maturity_y2", 200), at("debt_maturity_after_y5", 700), at("long_term_debt_total", 1000)];

  it("builds the ladder in filed order with the reported total", () => {
    const d = debtFromFacts(ladder)!;
    expect(d.tranches.map((t) => t.label)).toEqual(["Next 12 months", "Year 2", "After year 5"]);
    expect(d.reportedTotal).toBe(1000);
    expect(debtBreakdown(d)).toMatchObject({ classified: 1000, unclassified: 0, reconciles: true });
  });

  it("shows an explicit unclassified remainder and never spreads it", () => {
    const d = debtFromFacts([...ladder.slice(0, 3), at("long_term_debt_total", 1500)])!;
    const b = debtBreakdown(d);
    expect(b.unclassified).toBe(500);
    expect(b.unclassifiedShare).toBeCloseTo(1 / 3);
    expect(b.reconciles).toBe(false);
  });

  it("flags a maturity table larger than reported debt as a data error", () => {
    const b = debtBreakdown(debtFromFacts([...ladder.slice(0, 3), at("long_term_debt_total", 500)])!);
    expect(b.overClassified).toBe(true);
    expect(b.unclassified).toBe(0);
  });

  it("tolerates principal vs carrying differences within 2%", () => {
    expect(debtBreakdown(debtFromFacts([...ladder.slice(0, 3), at("long_term_debt_total", 990)])!).reconciles).toBe(true);
  });

  it("falls back to current + non-current, and has no total when only one exists", () => {
    const parts = debtFromFacts([...ladder.slice(0, 3), at("long_term_debt_current", 100), at("long_term_debt_noncurrent", 900)])!;
    expect(parts.reportedTotal).toBe(1000);
    expect(parts.totalBasis).toContain("+");
    const none = debtFromFacts([...ladder.slice(0, 3), at("long_term_debt_current", 100)])!;
    expect(none.reportedTotal).toBeNull();
    expect(debtBreakdown(none).unclassified).toBe(0);
  });

  it("returns null with no maturity facts and ignores failed ones", () => {
    expect(debtFromFacts([at("long_term_debt_total", 1)])).toBeNull();
    expect(debtFromFacts([at("debt_maturity_y1", 1, { reconciliation: "failed" })])).toBeNull();
  });

  it("collects lease facts of the same unit up to the balance date", () => {
    const d = debtFromFacts([...ladder, at("lease_liability", 40)])!;
    expect(d.leases).toEqual([{ id: "lease_liability", label: "Lease liability", value: 40, periodEnd: "2025-12-31" }]);
  });
});

describe("segments", () => {
  const seg = (label: string, member: string | null, value: number, subtotal = false) => ({ label, member, value, share: null, subtotal, label_source: "filing" as const });
  const data = (over: Partial<HoldingSegments> = {}): HoldingSegments =>
    ({
      ticker: "X",
      basis: "annual",
      available_bases: ["annual"],
      period: { start: "2025-01-01", end: "2025-12-31", months: 12, basis: "annual", label: "FY2025" },
      currency: "USD",
      geographic: [],
      segment: [seg("Cloud", "x:CloudMember", 600), seg("Devices", "x:DevicesMember", 300), seg("International", "x:IntlMember", 900, true)],
      product: [],
      total_revenue: 900,
      history: {
        segment: [{ period: { start: "2024-01-01", end: "2024-12-31", months: 12, basis: "annual", label: "FY2024" }, rows: [seg("Cloud", "x:CloudMember", 500), seg("Devices", "x:DevicesMember", 310)] }],
      },
      source: null,
      as_of: "2026-02-01",
      notes: [],
      ...over,
    }) as unknown as HoldingSegments;
  const profit = (member: string, value: number, concept = "us-gaap:OperatingIncomeLoss"): FactRow =>
    fact({ lineItem: "operating_income", statement: "segment", periodStart: "2025-01-01", periodEnd: "2025-12-31", value, concept, dimensional: true, members: [{ axis: "us-gaap:StatementBusinessSegmentsAxis", member }] });

  it("sizes by revenue, leaves subtotals out and counts them", () => {
    const set = buildSegmentSet(data(), "segment", null)!;
    expect(set.segments.map((s) => s.name)).toEqual(["Cloud", "Devices"]);
    expect(set.subtotalsLeftOut).toBe(1);
  });

  it("colours by growth only when every tile has a prior-year figure of the same member", () => {
    const set = buildSegmentSet(data(), "segment", null)!;
    expect(segmentAlignment(set).growth.aligned).toBe(true);
    const t = segmentTiles(set, "growth");
    expect(t.items.find((i) => i.label === "Cloud")!.change).toBeCloseTo(0.2);
    const noPrior = buildSegmentSet(data({ history: {} }), "segment", null)!;
    expect(segmentAlignment(noPrior).growth.aligned).toBe(false);
    expect(segmentTiles(noPrior, "growth").items.every((i) => i.change === null)).toBe(true);
  });

  it("matches margin only with one shared profit definition for every tile", () => {
    const ok = buildSegmentSet(data(), "segment", [profit("x:CloudMember", 120), profit("x:DevicesMember", 30)])!;
    expect(segmentAlignment(ok).margin.aligned).toBe(true);
    expect(segmentTiles(ok, "margin").items[0].change).toBeCloseTo(0.2);
    const mixed = buildSegmentSet(data(), "segment", [profit("x:CloudMember", 120), profit("x:DevicesMember", 30, "x:SegmentProfit")])!;
    expect(segmentAlignment(mixed).margin.aligned).toBe(false);
    expect(segmentAlignment(mixed).margin.reason).toMatch(/different definitions/);
    const missing = buildSegmentSet(data(), "segment", [profit("x:CloudMember", 120)])!;
    expect(segmentAlignment(missing).margin.reason).toMatch(/Devices/);
  });

  it("offers no margin for regions and is null without a period", () => {
    const geo = buildSegmentSet(data({ geographic: [seg("US", "country:US", 10)] }), "geographic", null)!;
    expect(segmentAlignment(geo).margin.aligned).toBe(false);
    expect(buildSegmentSet(data({ period: null }), "segment", null)).toBeNull();
  });

  it("does not take a prior period of another length as the prior year", () => {
    const quarterly = data({ history: { segment: [{ period: { start: "2024-10-01", end: "2024-12-31", months: 3, basis: "quarter", label: "x" }, rows: [seg("Cloud", "x:CloudMember", 1)] }] } } as unknown as Partial<HoldingSegments>);
    expect(segmentAlignment(buildSegmentSet(quarterly, "segment", null)!).growth.aligned).toBe(false);
  });
});

describe("estimates", () => {
  let oid = 1;
  const obs = (over: Partial<ForwardObs>): ForwardObs => ({
    id: oid++,
    kind: "published_analyst_consensus",
    provider: "Finnhub",
    metric: "eps_diluted",
    basis: "GAAP",
    fiscalPeriod: "FY2026",
    periodEnd: null,
    currency: "USD",
    value: 10,
    low: 9,
    high: 11,
    analystCount: 20,
    vintage: "2026-01-10",
    publishedAt: null,
    knownAt: "2026-01-10T00:00:00Z",
    sourceUrl: null,
    ...over,
  });
  const series = (vintages: ForwardObs[], over: Partial<ConsensusSeries> = {}): ConsensusSeries => ({ metric: "eps_diluted", fiscalPeriod: "FY2026", provider: "Finnhub", currency: "USD", basis: "GAAP", vintages, revisionSincePrevious: null, ...over });

  it("computes a revision slope across snapshots of one series", () => {
    const s = series([obs({ value: 10, vintage: "2026-01-01", knownAt: "2026-01-01T00:00:00Z" }), obs({ value: 11, vintage: "2026-01-31", knownAt: "2026-01-31T00:00:00Z" })]);
    const r = revisionSlope(s)!;
    expect(r.change).toBe(1);
    expect(r.pct).toBeCloseTo(0.1);
    expect(r.days).toBe(30);
    expect(r.perMonthPct).toBeCloseTo(0.1);
  });

  it("gives no slope for a single snapshot or a same-day repeat", () => {
    expect(revisionSlope(series([obs({})]))).toBeNull();
    expect(revisionSlope(series([obs({ value: 1 }), obs({ value: 2, knownAt: "2026-01-10T05:00:00Z" })]))).toBeNull();
  });

  it("keeps different providers, currencies and bases out of one chart and says why", () => {
    const a = obs({ fiscalPeriod: "FY2026" });
    const b = obs({ provider: "Other", fiscalPeriod: "FY2026", value: 99 });
    const c = obs({ kind: "company_guidance", currency: "EUR", provider: "Issuer", value: 8 });
    const m = buildEstimates({ observations: [a, b, c], series: [series([a]), series([b], { provider: "Other" })] }, [], "eps_diluted");
    expect(m.points.map((p) => p.id)).toEqual([a.id]);
    expect(m.unplotted).toHaveLength(2);
    expect(m.unplotted.map((u) => u.reason).join(" ")).toMatch(/different provider|EUR/i);
    expect(m.seriesOptions).toHaveLength(2);
  });

  it("does not infer an analyst count and lists periods without consensus", () => {
    const cons = obs({ analystCount: null });
    const guide = obs({ kind: "company_guidance", fiscalPeriod: "FY2027", provider: "Issuer", value: 12, low: 11, high: 13 });
    const m = buildEstimates({ observations: [cons, guide], series: [series([cons])] }, [], "eps_diluted");
    expect(m.points.find((p) => p.kind === "published_analyst_consensus")!.analystCount).toBeNull();
    expect(m.consensusGaps).toEqual(["FY2027"]);
  });

  it("takes the latest vintage per kind and period", () => {
    const old = obs({ value: 5, knownAt: "2025-12-01T00:00:00Z" });
    const now = obs({ value: 6, knownAt: "2026-01-01T00:00:00Z" });
    const m = buildEstimates({ observations: [old, now], series: [series([old, now])] }, [], "eps_diluted");
    expect(m.points).toHaveLength(1);
    expect(m.points[0].value).toBe(6);
  });

  it("draws actuals only when currency and scale match", () => {
    const cons = obs({ metric: "revenue", value: 1000, low: 900, high: 1100 });
    const rev = (v: number, cur = "USD") => flow("revenue", 2025, v, { currency: cur });
    const ok = buildEstimates({ observations: [cons], series: [series([cons], { metric: "revenue" })] }, [rev(950)], "revenue");
    expect(ok.actuals).toHaveLength(1);
    const scale = buildEstimates({ observations: [cons], series: [series([cons], { metric: "revenue" })] }, [rev(950_000_000)], "revenue");
    expect(scale.actuals).toHaveLength(0);
    expect(scale.actualsNote).toMatch(/scale/);
    const eur = buildEstimates({ observations: [cons], series: [series([cons], { metric: "revenue" })] }, [rev(950, "EUR")], "revenue");
    expect(eur.actualsNote).toMatch(/EUR/);
  });

  it("maps a metric to actuals only where the backend does", () => {
    expect(actualsFor("weird_metric", [flow("revenue", 2025, 1)]).points).toEqual([]);
    expect(actualsFor("revenue", [flow("revenue", 2025, 7)]).points).toMatchObject([{ period: "FY2025", value: 7 }]);
  });
});

describe("actual vs consensus", () => {
  const actual = { value: 105, unit: "USD", currency: "USD", knownAt: "x", accession: "a", reconciled: true };
  const consensus = { provider: "P", value: 100, low: 95, high: 110, analystCount: 10, currency: "USD", basis: "GAAP", vintage: null, knownAt: null };

  it("positions the actual against the range", () => {
    expect(surprise({ actual, consensus })).toMatchObject({ position: "within_range" });
    expect(surprise({ actual: { ...actual, value: 120 }, consensus }).position).toBe("above_range");
    expect(surprise({ actual: { ...actual, value: 90 }, consensus }).position).toBe("below_range");
  });

  it("never invents a surprise without consensus, a zero mean or a range", () => {
    expect(surprise({ actual, consensus: null })).toEqual({ pct: null, position: "no_range" });
    expect(surprise({ actual, consensus: { ...consensus, value: 0 } }).pct).toBeNull();
    expect(surprise({ actual, consensus: { ...consensus, low: null } }).position).toBe("no_range");
  });

  it("maps the API's states", () => {
    const base = { reason: null, metric: "m", fiscalPeriod: "FY2025", actual: null, consensus: null, surprise: null, surprisePct: null };
    expect(avcState({ ...base, state: "available", actual }).state).toBe("verified");
    expect(avcState({ ...base, state: "available", actual: { ...actual, reconciled: false } }).state).toBe("partial");
    expect(avcState({ ...base, state: "no_consensus_before_report" }).state).toBe("not_covered");
    expect(avcState({ ...base, state: "currency_mismatch" }).state).toBe("disputed");
    expect(avcState({ ...base, state: "no_actual" }).state).toBe("not_covered");
    expect(avcState({ ...base, state: "surprise" }).state).toBe("unavailable");
  });
});

describe("peers", () => {
  const payload = (over: Partial<PeerMetricsPayload> = {}): PeerMetricsPayload => ({
    confirmedPeers: [
      { peerTicker: "A", basis: "US_GAAP", source: null },
      { peerTicker: "B", basis: "IFRS", source: null },
      { peerTicker: "C", basis: "US_GAAP", source: null },
    ],
    metrics: [
      { id: "gross_margin", mean: 0.5, coveredPeers: 1, included: ["A"], excluded: ["B", "C"], excludedReasons: { B: "accounting basis IFRS differs from US_GAAP: not comparable" }, holdingValue: 0.6, holdingReason: null, periodEnd: "2025-12-31", periodStart: "2025-01-01", basis: "US_GAAP" },
    ],
    excludedPeers: [{ peer: "C", reason: "no filed statement facts" }],
    holdingPeriod: { periodStart: null, periodEnd: null, basis: null, unit: null, reconciliation: "reconciled", unreconciledInputs: [] },
    reconciledOnly: false,
    formulas: { gross_margin: "gross profit / revenue" },
    universe: null,
    knownAsOf: null,
    ...over,
  });

  it("reports covered/total and the reason every excluded peer is out", () => {
    const c = peerComparison(payload(), "gross_margin")!;
    expect(c).toMatchObject({ covered: 1, total: 3, mean: 0.5, holdingValue: 0.6, definition: "gross profit / revenue" });
    expect(c.excluded).toEqual([
      { ticker: "B", reason: "accounting basis IFRS differs from US_GAAP: not comparable" },
      { ticker: "C", reason: "no filed statement facts" },
    ]);
    expect(peerState(payload(), c).state).toBe("partial");
  });

  it("is not covered with no confirmed peers and falls back to a generic reason", () => {
    const none = payload({ confirmedPeers: [], excludedPeers: [] });
    expect(peerState(none, peerComparison(none, "gross_margin")).state).toBe("not_covered");
    const gen = peerComparison(payload({ excludedPeers: [] }), "gross_margin")!;
    expect(gen.excluded[1].reason).toBe("No value for this metric.");
    expect(peerComparison(payload(), "nope")).toBeNull();
  });

  it("is verified only when all confirmed peers are covered and inputs reconciled", () => {
    const all = payload({ confirmedPeers: [{ peerTicker: "A", basis: null, source: null }] });
    expect(peerState(all, peerComparison(all, "gross_margin")).state).toBe("verified");
  });
});
