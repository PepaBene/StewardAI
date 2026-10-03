import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { HoldingSegments } from "@/lib/api/client";
import { cashWalkFromFacts } from "@/lib/financials/logic";
import type { ActualVsConsensus, DebtSchedule, FactRow, ForwardObs, ForwardPayload, PeerMetricsPayload } from "@/lib/financials/types";

import { CashWaterfall } from "./cash-waterfall";
import { DebtMaturityChart } from "./debt-maturity-chart";
import { EarningsCard } from "./earnings-card";
import { EstimatesFan } from "./estimates-fan";
import { HoldingSelector } from "./holding-selector";
import { IrPanel } from "./ir-panel";
import { PeerSetList } from "./peer-set-panel";
import { PeerTable } from "./peer-table";
import { SegmentTreemapPanel } from "./segment-treemap-panel";
import { StatementExplorer } from "./statement-explorer";

let id = 1;
function fact(over: Partial<FactRow> & { lineItem: string }): FactRow {
  return {
    id: id++,
    concept: `us-gaap:${over.lineItem}`,
    label: null,
    statement: "income",
    periodStart: "2025-01-01",
    periodEnd: "2025-12-31",
    fiscalPeriod: "FY2025",
    basis: "US_GAAP",
    value: 100,
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
    accession: "a",
    form: "10-K",
    reconciliation: "reconciled",
    checkNote: null,
    textBlock: false,
    ...over,
  };
}

describe("financial components render without throwing", () => {
  it("holding selector: loading, error, empty and populated", () => {
    expect(renderToStaticMarkup(h(HoldingSelector, { holdings: [], value: null, onChange: () => undefined, status: { kind: "loading" } }))).toContain("Loading holdings");
    expect(renderToStaticMarkup(h(HoldingSelector, { holdings: [], value: null, onChange: () => undefined, status: { kind: "error", title: "Backend unreachable" } }))).toContain("Holdings unavailable");
    expect(renderToStaticMarkup(h(HoldingSelector, { holdings: [{ ticker: "AAPL", name: "Apple" }], value: "AAPL", onChange: () => undefined, status: { kind: "ok" } }))).toContain("Apple");
  });

  it("statement explorer: empty facts show not covered, never a zero", () => {
    const html = renderToStaticMarkup(h(StatementExplorer, { name: "X", facts: { rows: [], dropped: 0 }, asOf: null }));
    expect(html).toContain("Not covered");
    const withData = renderToStaticMarkup(h(StatementExplorer, { name: "X", facts: { rows: [fact({ lineItem: "revenue" })], dropped: 0 }, asOf: "2026-03-01" }));
    expect(withData).toContain("Revenue");
    expect(withData).toContain("as known on");
  });

  it("cash walk: unavailable, gap, reconciled", () => {
    expect(renderToStaticMarkup(h(CashWaterfall, { result: { walk: null, reason: "No filed annual cash-from-operations fact is stored." } }))).toContain("Not covered");
    const cf = (lineItem: string, value: number) => fact({ lineItem, value, statement: "cash_flow" });
    const gap = cashWalkFromFacts([fact({ lineItem: "net_income", value: 100 }), cf("cash_from_operations", 300)]);
    const gapHtml = renderToStaticMarkup(h(CashWaterfall, { result: gap }));
    expect(gapHtml).toContain("Not explained by the stored lines");
    expect(gapHtml).not.toContain('role="img"');
    const ok = cashWalkFromFacts([fact({ lineItem: "net_income", value: 100 }), cf("cash_from_operations", 150), cf("depreciation_amortization", 50), cf("capex", -20)]);
    expect(renderToStaticMarkup(h(CashWaterfall, { result: ok }))).toContain('role="img"');
  });

  it("debt maturity: unavailable, ladder with unclassified remainder", () => {
    expect(renderToStaticMarkup(h(DebtMaturityChart, { schedule: null }))).toContain("Not covered");
    const schedule: DebtSchedule = {
      periodEnd: "2025-12-31",
      currency: "USD",
      tranches: [{ key: "debt_maturity_y1", label: "Next 12 months", amount: 100 }],
      reportedTotal: 500,
      totalBasis: "us-gaap:LongTermDebt",
      leases: [{ id: "lease_liability", label: "Lease liability", value: 10, periodEnd: "2025-12-31" }],
      knownAt: "2026-02-01T00:00:00Z",
      accession: "a",
      source: "SEC",
      sourceUrl: null,
    };
    const html = renderToStaticMarkup(h(DebtMaturityChart, { schedule }));
    expect(html).toContain("Unclassified");
    expect(html).toContain("Lease liability");
    expect(renderToStaticMarkup(h(DebtMaturityChart, { schedule: { ...schedule, reportedTotal: null } }))).toContain("Partial");
  });

  it("segments: not covered without a breakdown", () => {
    const data = { ticker: "X", basis: null, available_bases: [], period: null, currency: null, geographic: [], segment: [], product: [], total_revenue: null, history: {}, source: null, as_of: null, notes: ["Nothing stored"] } as unknown as HoldingSegments;
    const html = renderToStaticMarkup(h(SegmentTreemapPanel, { data, profitFacts: null }));
    expect(html).toContain("Not covered");
    expect(html).toContain("Nothing stored");
  });

  const obs = (over: Partial<ForwardObs>): ForwardObs => ({ id: id++, kind: "published_analyst_consensus", provider: "P", metric: "eps_diluted", basis: "GAAP", fiscalPeriod: "FY2026", periodEnd: null, currency: "USD", value: 5, low: 4, high: 6, analystCount: null, vintage: "2026-01-10", vintageDate: null, publishedAt: null, knownAt: "2026-01-10T00:00:00Z", sourceUrl: null, ...over });

  it("estimates fan: nothing stored, and every kind present", () => {
    const empty: ForwardPayload = { observations: [], series: [], knownAsOf: null, dropped: 0 };
    expect(renderToStaticMarkup(h(EstimatesFan, { payload: empty, facts: [], asOf: null }))).toContain("never fills one in");
    const c = obs({});
    const payload: ForwardPayload = {
      observations: [c, obs({ kind: "company_guidance", provider: "Issuer", low: 4.5, high: 5.5 }), obs({ kind: "owner_scenario", provider: "owner", low: null, high: null }), obs({ kind: "event_calendar_estimate", low: null, high: null }), obs({ provider: "Other", value: 9 })],
      series: [{ metric: "eps_diluted", fiscalPeriod: "FY2026", provider: "P", currency: "USD", basis: "GAAP", vintages: [c], revisionSincePrevious: null }],
      knownAsOf: null,
      dropped: 0,
    };
    const html = renderToStaticMarkup(h(EstimatesFan, { payload, facts: [fact({ lineItem: "eps_diluted", value: 4.8, unit: "USD/shares" })], asOf: "2026-02-01" }));
    expect(html).toContain("not stated");
    expect(html).toContain("One snapshot only");
    expect(html).toContain("Figures not plotted");
  });

  it("earnings card: every answer state", () => {
    const base: ActualVsConsensus = { state: "available", reason: null, metric: "revenue", fiscalPeriod: "FY2025", actual: { value: 105, unit: "USD", currency: "USD", knownAt: "2026-02-01T00:00:00Z", accession: "a", reconciled: true, reconciliationState: "passed" as const }, consensus: { provider: "P", value: 100, low: 95, high: 110, analystCount: 10, currency: "USD", basis: "GAAP", vintage: "2026-01-01", vintageDate: null, knownAt: "2026-01-02T00:00:00Z" }, surprise: 5, surprisePct: 0.05 };
    expect(renderToStaticMarkup(h(EarningsCard, { avc: base }))).toContain("Within the analyst range");
    expect(renderToStaticMarkup(h(EarningsCard, { avc: { ...base, state: "no_consensus_before_report", consensus: null, reason: "none known" } }))).toContain("No estimate is created");
    expect(renderToStaticMarkup(h(EarningsCard, { avc: { ...base, state: "currency_mismatch" } }))).toContain("Disputed");
    expect(renderToStaticMarkup(h(EarningsCard, { avc: { ...base, state: "no_actual", actual: null, consensus: null, reason: "no reported figure" } }))).toContain("Not covered");
  });

  it("peer table and list: none confirmed, partial", () => {
    const none: PeerMetricsPayload = { confirmedPeers: [], metrics: [], excludedPeers: [], holdingPeriod: null, reconciledOnly: false, formulas: {}, universe: null, knownAsOf: null };
    expect(renderToStaticMarkup(h(PeerTable, { data: none, ticker: "X" }))).toContain("No confirmed peer");
    const partial: PeerMetricsPayload = {
      ...none,
      confirmedPeers: [{ peerTicker: "A", basis: null, source: null, sources: [] }, { peerTicker: "B", basis: null, source: null, sources: [] }],
      metrics: [{ id: "gross_margin", mean: 0.4, coveredPeers: 1, included: ["A"], excluded: ["B"], excludedReasons: { B: "different basis" }, peers: [], holdingValue: 0.5, holdingReason: null, periodEnd: "2025-12-31", periodStart: null, basis: "US_GAAP" }],
    };
    const html = renderToStaticMarkup(h(PeerTable, { data: partial, ticker: "X" }));
    expect(html).toContain("1 of 2");
    expect(html).toContain("different basis");
    expect(renderToStaticMarkup(h(PeerSetList, { peers: [] }))).toContain("Not covered");
    expect(renderToStaticMarkup(h(PeerSetList, { peers: [{ id: 1, peerTicker: "A", basis: null, status: "suggested", evidenceId: null, source: "finnhub", validFrom: null, validTo: null, knownAt: null }] }))).toContain("Suggested");
  });

  it("IR panel: empty and populated", () => {
    expect(renderToStaticMarkup(h(IrPanel, { releases: [], sources: [] }))).toContain("Not covered");
    const html = renderToStaticMarkup(h(IrPanel, { releases: [{ id: 1, releaseType: "guidance", sourceUrl: "https://ir.example/q4", publishedAt: null, knownAt: null, analystCount: null, basis: null, status: "parsed" }], sources: [] }));
    expect(html).toContain("not stated");
  });
});
