import { describe, expect, it } from "vitest";

import { claimKnownAsOf, disagreementRows, gapState, isFutureDay, latestKnownAt, mergeGaps, sectionState, sourceDateRange, summariseHistory, toNumber, worstCoverage } from "./logic";
import { parseNetworkSummary, parsePatentSummary } from "./parse";
import type { Disagreement, FinancialHistory, Gap } from "./types";

const dis = (over: Partial<Disagreement>): Disagreement => ({
  conflict_id: 1, metric: "Revenues", status: "open", tolerance: "0.5", difference: "1", preferred_source: "sec_xbrl", preferred_provenance: "primary",
  preferred_value: "100", preferred_known_at: "2026-01-01T00:00:00Z", alternative_source: "fmp", alternative_provenance: "vendor", alternative_value: "101",
  alternative_known_at: "2026-01-02T00:00:00Z", period_end: "2025-12-31", resolution: null, ...over,
});

describe("toNumber", () => {
  it("parses numbers and numeric strings; everything else is missing, not zero", () => {
    expect(toNumber("1.5")).toBe(1.5);
    expect(toNumber(0)).toBe(0);
    expect(toNumber("")).toBeNull();
    expect(toNumber(null)).toBeNull();
    expect(toNumber(NaN)).toBeNull();
    expect(toNumber("abc")).toBeNull();
  });
});

describe("as-of day guard", () => {
  it("flags a future day only; today and past pass; garbage is not flagged", () => {
    expect(isFutureDay("2026-10-04", "2026-10-03")).toBe(true);
    expect(isFutureDay("2026-10-03", "2026-10-03")).toBe(false);
    expect(isFutureDay("", "2026-10-03")).toBe(false);
  });
});

describe("states", () => {
  it("maps coverage and gap vocabularies", () => {
    expect(sectionState({ state: "covered", reason: "" })).toBe("verified");
    expect(sectionState({ state: "not_covered", reason: "" })).toBe("not_covered");
    expect(gapState("missing")).toBe("unavailable");
    expect(gapState("unresolved")).toBe("disputed");
    expect(gapState("stale")).toBe("stale");
  });
  it("a group of channels is covered only when all are", () => {
    const c = (state: "covered" | "partial" | "not_covered") => ({ state, reason: "" });
    expect(worstCoverage([c("covered"), c("covered")]).state).toBe("covered");
    expect(worstCoverage([c("covered"), c("not_covered")]).state).toBe("partial");
    expect(worstCoverage([c("not_covered"), c("not_covered")]).state).toBe("not_covered");
  });
});

describe("mergeGaps", () => {
  it("deduplicates by domain and reason and sorts stably", () => {
    const g = (domain: string, reason: string): Gap => ({ domain, state: "missing", reason });
    const merged = mergeGaps([g("patents", "a"), g("business_network", "b")], [g("patents", "a"), g("patents", "c")], null);
    expect(merged.map((x) => `${x.domain}:${x.reason}`)).toEqual(["business_network:b", "patents:a", "patents:c"]);
  });
});

describe("summariseHistory", () => {
  const h = (over: Partial<FinancialHistory>): FinancialHistory => ({ total_rows: 10, returned_rows: 10, statements: { income: 6, balance: 4 }, earliest_period: null, latest_period: null, provenance: { primary: 7, vendor: 3 }, facts: [], ...over });
  it("divides provenance by total rows and orders by count", () => {
    const s = summariseHistory(h({}));
    expect(s.statements[0]).toEqual({ name: "income", count: 6 });
    expect(s.provenance[0]).toEqual({ cls: "primary", count: 7, share: 0.7 });
    expect(s.truncated).toBe(false);
  });
  it("a zero base gives no share and a truncated list is flagged", () => {
    const s = summariseHistory(h({ total_rows: 0, returned_rows: 0, provenance: { vendor: 0 }, statements: {} }));
    expect(s.provenance[0].share).toBeNull();
    expect(summariseHistory(h({ returned_rows: 4 })).truncated).toBe(true);
  });
});

describe("disagreementRows", () => {
  it("sorts by absolute difference, unknown last, ties by metric then id; flags tolerance and cross-class", () => {
    const rows = disagreementRows([
      dis({ conflict_id: 3, metric: "B", difference: null }),
      dis({ conflict_id: 2, metric: "B", difference: "-5", tolerance: "1" }),
      dis({ conflict_id: 1, metric: "A", difference: "5" }),
      dis({ conflict_id: 4, metric: "C", difference: "0.1", alternative_provenance: "primary" }),
    ]);
    expect(rows.map((r) => r.id)).toEqual([1, 2, 4, 3]);
    expect(rows[1].exceedsTolerance).toBe(true);
    expect(rows[3].exceedsTolerance).toBeNull();
    expect(rows[2].crossClass).toBe(false);
    expect(rows[0].crossClass).toBe(true);
  });
});

describe("claimKnownAsOf", () => {
  const src = new Map<string, { as_of?: string | null }>([["S1", { as_of: "2025-03-01" }], ["S2", { as_of: "2025-06-30T12:00:00Z" }], ["S3", { as_of: null }], ["S4", { as_of: "Q2 2025" }]]);
  it("is the latest dated cited source", () => {
    expect(claimKnownAsOf(["S1", "S2"], src)).toEqual({ date: "2025-06-30", cited: 2, undated: 0 });
  });
  it("counts undated and unparseable citations instead of hiding them", () => {
    expect(claimKnownAsOf(["S1", "S3", "S4"], src)).toEqual({ date: "2025-03-01", cited: 3, undated: 2 });
  });
  it("ignores unknown ids and duplicates; no citation gives no date", () => {
    expect(claimKnownAsOf(["S1", "S1", "ZZ"], src)).toEqual({ date: "2025-03-01", cited: 1, undated: 0 });
    expect(claimKnownAsOf([], src)).toEqual({ date: null, cited: 0, undated: 0 });
  });
  it("source range reports min, max and undated", () => {
    expect(sourceDateRange([{ as_of: "2025-03-01" }, { as_of: "2025-01-05" }, { as_of: undefined }])).toEqual({ min: "2025-01-05", max: "2025-03-01", undated: 1 });
    expect(sourceDateRange([])).toEqual({ min: null, max: null, undated: 0 });
  });
});

describe("latestKnownAt", () => {
  it("ignores null and invalid", () => {
    expect(latestKnownAt([null, "bad", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"])).toBe("2026-02-01T00:00:00Z");
    expect(latestKnownAt([])).toBeNull();
  });
});

describe("parsers for the untyped payloads", () => {
  it("summarises the company graph, separating confirmed from candidates", () => {
    const s = parseNetworkSummary({ edges: [{ status: "confirmed" }, { status: "suggested" }, {}], events: [{}], disclosures: [], limitations: ["x", 2] });
    expect(s).toMatchObject({ edges: 3, confirmed: 1, candidates: 2, events: 1, disclosures: 0, limitations: ["x"] });
    expect(s?.byStatus.unknown).toBe(1);
  });
  it("rejects unexpected shapes instead of returning zeros", () => {
    expect(parseNetworkSummary(null)).toBeNull();
    expect(parseNetworkSummary({ edges: "x", events: [], disclosures: [] })).toBeNull();
    expect(parsePatentSummary({ trend: [] })).toBeNull();
    expect(parsePatentSummary([])).toBeNull();
  });
  it("summarises patents", () => {
    expect(parsePatentSummary({ families: [{ attributed: true, legal_status: "granted", known_at: "2026-01-01" }, { attributed: false, known_at: "2026-03-01" }], trend: [{}] })).toEqual({
      families: 2, attributed: 1, byLegalStatus: { granted: 1, unknown: 1 }, latestKnownAt: "2026-03-01", trendRows: 1,
    });
    expect(parsePatentSummary({ families: [] })?.trendRows).toBeNull();
  });
});

import { evidenceItems } from "./evidence-items";
import type { EvidenceSummary } from "./types";

describe("evidenceItems", () => {
  const cov = { state: "covered" as const, reason: "ok" };
  const summary = {
    ticker: "X", known_as_of: "2026-01-01T00:00:00Z", gaps: [],
    filed_notes: { coverage: cov, notes: [
      { fact_id: 1, concept: "Leases", label: null, period_end: "2025-12-31", fiscal_period: "FY", accession: "0001", form: "10-K", source: "sec", source_url: "https://sec.gov/x", passage: "text", passage_basis: "filed_text_block", passage_reason: null, known_at: "2026-02-01T00:00:00Z", revision: 1, evidence_id: 5 },
      { fact_id: 2, concept: "Debt", label: "Debt note", period_end: "2025-12-31", fiscal_period: null, accession: null, form: null, source: "sec", source_url: "u", passage: null, passage_basis: null, passage_reason: null, known_at: "2026-02-01T00:00:00Z", revision: 2, evidence_id: null },
    ] },
    positions: { not_additive: "n", control: { channel: "beneficial_ownership_13dg", coverage: cov, stakes: [] }, institutional: { channel: "institutional_13f", coverage: cov, ownership: null, pit_note: null }, funds: { channel: "fund_positions_nport", coverage: cov, positions: { positions: [] } } },
    insider_activity: { coverage: cov, known_by: "2026-01-01", activity: {}, net_discretionary_derived_from: [] },
    dependencies: { coverage: cov, dependencies: [], excluded_unconfirmed: 0 },
    deals: { coverage: cov, deals: [] },
    consensus: { coverage: cov, series: [], issuer_published: [] },
  } as unknown as EvidenceSummary;

  it("keeps passage and known-at, and explains a missing passage instead of leaving it blank", () => {
    const [a, b] = evidenceItems(summary, "filed-notes");
    expect(a.passage).toBe("text");
    expect(a.knownAt).toBe("2026-02-01T00:00:00Z");
    expect(b.passage).toBeNull();
    expect(b.passageReason).toMatch(/No passage stored/);
    expect(b.title).toBe("Debt note");
  });
  it("empty domains give no items (the coverage reason, not zeros, explains why)", () => {
    expect(evidenceItems(summary, "positions")).toEqual([]);
    expect(evidenceItems(summary, "deals")).toEqual([]);
  });
});
