import { describe, expect, it } from "vitest";

import { parseActualVsConsensus, parseFacts, parseForwardPayload, parseIrReleases, parseIrSources, parsePeerMetrics, parsePeerSet } from "./parse";

const rawFact = (over: Record<string, unknown> = {}) => ({
  id: 1,
  concept: "us-gaap:Revenues",
  label: "Revenue",
  statement: "income",
  line_item: "revenue",
  period_start: "2025-01-01",
  period_end: "2025-12-31",
  fiscal_period: "FY2025",
  basis: "US_GAAP",
  value: "1234.5",
  unit: "USD",
  currency: "USD",
  dimensions: {},
  known_at: "2026-02-01T00:00:00Z",
  reconciliation: "reconciled",
  validation: { reconciled: true },
  ...over,
});

describe("parseFacts", () => {
  it("accepts numbers and numeric strings, rejects other shapes", () => {
    const p = parseFacts([rawFact(), rawFact({ id: 2, value: 7 }), rawFact({ id: 3, value: "abc" })])!;
    expect(p.rows.map((r) => r.value)).toEqual([1234.5, 7, null]);
  });

  it("is rejected when the body is not an array or no row is usable", () => {
    expect(parseFacts({ rows: [] })).toBeNull();
    expect(parseFacts([{ nope: 1 }])).toBeNull();
    expect(parseFacts([])).toEqual({ rows: [], dropped: 0 });
  });

  it("drops a malformed row and counts it", () => {
    const p = parseFacts([rawFact(), rawFact({ id: 2, reconciliation: "maybe" }), "x"])!;
    expect(p.rows).toHaveLength(1);
    expect(p.dropped).toBe(2);
  });

  it("reads dimensions and amendment lineage", () => {
    const p = parseFacts([rawFact({ dimensions: { members: [["Axis", "Member"]] }, amends_fact_id: 4, is_amendment: true })])!;
    expect(p.rows[0]).toMatchObject({ dimensional: true, members: [{ axis: "Axis", member: "Member" }], amendsFactId: 4, isAmendment: true });
  });

  it("surfaces the failed checks of a failed fact", () => {
    const p = parseFacts([rawFact({ reconciliation: "failed", validation: { reconciled: false, failed_checks: ["matches_companyfacts"] } })])!;
    expect(p.rows[0].checkNote).toContain("matches_companyfacts");
  });
});

const rawObs = (over: Record<string, unknown> = {}) => ({ id: 1, kind: "published_analyst_consensus", metric: "revenue", fiscal_period: "FY2026", known_at: "2026-01-01T00:00:00Z", value: 10, analyst_count: null, ...over });

describe("parseForwardPayload", () => {
  it("keeps a missing analyst count as null and rejects unknown kinds", () => {
    const p = parseForwardPayload({ observations: [rawObs(), rawObs({ id: 2, kind: "model_guess" })], consensus_series: [] })!;
    expect(p.observations).toHaveLength(1);
    expect(p.observations[0].analystCount).toBeNull();
    expect(p.dropped).toBe(1);
  });

  it("parses consensus series with their vintages", () => {
    const p = parseForwardPayload({ observations: [], consensus_series: [{ metric: "revenue", fiscal_period: "FY2026", provider: "P", currency: "USD", basis: "GAAP", vintages: [rawObs()], revision_since_previous: "1.5" }] })!;
    expect(p.series[0]).toMatchObject({ provider: "P", revisionSincePrevious: 1.5 });
    expect(p.series[0].vintages).toHaveLength(1);
  });

  it("rejects a response without observations", () => {
    expect(parseForwardPayload([])).toBeNull();
    expect(parseForwardPayload({ ticker: "X" })).toBeNull();
  });
});

describe("parseActualVsConsensus", () => {
  it("requires a state and parses both sides", () => {
    expect(parseActualVsConsensus({})).toBeNull();
    const p = parseActualVsConsensus({ state: "available", actual: { value: 1.5, reconciled: true }, consensus: { value: 1, low: 0.9, high: 1.1, analyst_count: 12 }, surprise: 0.5, surprise_percent: 0.5 })!;
    expect(p.actual).toMatchObject({ value: 1.5, reconciled: true });
    expect(p.consensus).toMatchObject({ analystCount: 12 });
    expect(p.surprisePct).toBe(0.5);
  });

  it("keeps an unavailable actual as null (no zero)", () => {
    expect(parseActualVsConsensus({ state: "no_actual", reason: "none" })!.actual).toBeNull();
  });
});

describe("peers and IR", () => {
  it("parses the peer set, dropping unknown statuses", () => {
    const p = parsePeerSet({ peers: [{ id: 1, peer_ticker: "A", status: "confirmed" }, { id: 2, peer_ticker: "B", status: "banana" }] })!;
    expect(p).toHaveLength(1);
    expect(parsePeerSet({})).toBeNull();
  });

  it("parses peer metrics with reasons", () => {
    const p = parsePeerMetrics({
      confirmed_peers: [{ peer_ticker: "A", basis: "US_GAAP" }],
      covered_peer_metrics: { gross_margin: { mean: "0.4", covered_peers: 1, included: ["A"], excluded: [], excluded_reasons: {}, holding_value: 0.5 } },
      excluded_peers: [{ peer: "Z", reason: "no facts" }],
      holding_period: { reconciliation: "mixed", unreconciled_inputs: ["cfo"] },
      formula_definitions: { gross_margin: "gp / revenue" },
    })!;
    expect(p.metrics[0]).toMatchObject({ id: "gross_margin", mean: 0.4, coveredPeers: 1 });
    expect(p.holdingPeriod?.unreconciledInputs).toEqual(["cfo"]);
    expect(parsePeerMetrics({})).toBeNull();
  });

  it("parses IR releases and sources", () => {
    expect(parseIrReleases({ releases: [{ id: 1, release_type: "guidance", source_url: "https://x" }, { id: "bad" }] })).toHaveLength(1);
    expect(parseIrSources({ sources: [{ id: 1, url: "https://x", release_type: "guidance", active: true }] })![0].active).toBe(true);
    expect(parseIrReleases(null)).toBeNull();
  });
});
