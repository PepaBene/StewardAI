import { describe, expect, it } from "vitest";

import {
  asOfParam,
  bubbleSize,
  buildPatentMatrix,
  cellKey,
  citationLevel,
  concentrationRows,
  dealStages,
  dealStateIsProvisional,
  edgeCoverage,
  edgeGroup,
  filterEdges,
  isIsoDate,
  latestDate,
  leadershipEntries,
  rankPeers,
  rowsState,
  termRows,
  validityBar,
  validityDomain,
  whatChanged,
} from "./logic";
import type { BusinessNetwork, ConcentrationPeriod, NetworkEdge, PatentFamily, PatentOverlapRow, RowStatus } from "./types";

function edge(over: Partial<NetworkEdge> = {}): NetworkEdge {
  return {
    id: "1", rowId: 1, legacy: false, subjectKey: "T", objectKey: "x", objectName: "X Corp", edgeType: "partner", direction: "symmetric",
    status: "suggested", validFrom: null, validTo: null, tier: "vendor", source: "diffbot", sourceUrl: null, evidenceId: null,
    passage: null, knownAt: null, decisionReason: null, detail: {}, ...over,
  };
}

function patent(over: Partial<PatentFamily> = {}): PatentFamily {
  return {
    familyKey: "F1", priorityDate: "2020-03-01", firstPublicationDate: null, title: "t", cpcCodes: ["H01L21/02"], jurisdictions: ["EP"],
    citationsForward: 4, citationsBackward: null, legalStatus: "granted", source: "epo_ops", sourceUrl: null, knownAt: "2026-01-01T00:00:00Z",
    applicant: "A", ownershipConfidence: 0.9, ownershipBasis: "direct", ownershipValidFrom: null, ownershipValidTo: null, evidenceId: null,
    attributed: true, ...over,
  };
}

function net(over: Partial<BusinessNetwork> = {}): BusinessNetwork {
  return { ticker: "T", knownAsOf: null, edges: [], events: [], disclosures: [], patents: [], limitations: [], ...over };
}

describe("as-of", () => {
  it("accepts only real calendar dates", () => {
    expect(isIsoDate("2026-02-28")).toBe(true);
    expect(isIsoDate("2026-02-30")).toBe(false);
    expect(isIsoDate("26-02-28")).toBe(false);
    expect(isIsoDate("")).toBe(false);
  });
  it("sends end of day UTC, nothing for empty or invalid input", () => {
    expect(asOfParam("2026-03-01")).toBe("2026-03-01T23:59:59Z");
    expect(asOfParam("")).toBeUndefined();
    expect(asOfParam("nope")).toBeUndefined();
  });
});

describe("edge classification", () => {
  it("keeps consolidation apart from ownership and transactions", () => {
    expect(edgeGroup("parent")).toBe("control");
    expect(edgeGroup("subsidiary")).toBe("control");
    expect(edgeGroup("owner")).toBe("ownership");
    expect(edgeGroup("investor")).toBe("ownership");
    expect(edgeGroup("acquirer")).toBe("transaction");
    expect(edgeGroup("mystery")).toBe("other");
  });
  it("filters by status and orders groups, status then recency", () => {
    const edges = [
      edge({ id: "a", objectName: "B", status: "suggested", edgeType: "partner" }),
      edge({ id: "b", objectName: "A", status: "confirmed", edgeType: "partner" }),
      edge({ id: "c", status: "confirmed", edgeType: "parent" }),
      edge({ id: "d", status: "rejected", edgeType: "partner" }),
    ];
    const all = filterEdges(edges, { statuses: new Set<RowStatus>(["confirmed", "suggested", "rejected"]), groups: null });
    expect(all.map((e) => e.id)).toEqual(["c", "b", "a", "d"]);
    const facts = filterEdges(edges, { statuses: new Set<RowStatus>(["confirmed"]), groups: new Set(["commercial"]) });
    expect(facts.map((e) => e.id)).toEqual(["b"]);
  });
  it("counts coverage over live edges only and never calls an empty set verified", () => {
    const edges = [edge({ status: "confirmed" }), edge({ id: "2", status: "suggested" }), edge({ id: "3", status: "rejected" })];
    expect(edgeCoverage(edges)).toEqual({ covered: 1, total: 2, unit: "edges confirmed" });
    expect(rowsState([])).toBe("not_covered");
    expect(rowsState(edges)).toBe("partial");
    expect(rowsState([edge({ status: "confirmed" })])).toBe("verified");
    expect(rowsState([edge({ status: "confirmed" }), edge({ status: "disputed" })])).toBe("disputed");
    expect(rowsState([edge({ status: "rejected" })])).toBe("not_covered");
  });
});

describe("validity bars", () => {
  const end = new Date("2026-01-01T00:00:00Z");
  it("has no domain when nothing is dated", () => {
    expect(validityDomain([edge()], end)).toBeNull();
    expect(validityBar(edge(), null)).toEqual({ kind: "undated" });
  });
  it("places open-ended and dated bars on one axis", () => {
    const edges = [edge({ validFrom: "2024-01-01", validTo: "2025-01-01" }), edge({ id: "2", validFrom: "2025-01-01" })];
    const domain = validityDomain(edges, end)!;
    const a = validityBar(edges[0]!, domain);
    const b = validityBar(edges[1]!, domain);
    expect(a.kind === "dated" && a.openEnded).toBe(false);
    expect(b.kind === "dated" && b.openEnded).toBe(true);
    if (a.kind === "dated" && b.kind === "dated") {
      expect(a.left).toBe(0);
      expect(b.left).toBeGreaterThan(a.left);
      expect(b.left + b.width).toBeCloseTo(100, 5);
    }
  });
  it("never lets the domain collapse to zero width", () => {
    const d = validityDomain([edge({ validFrom: "2026-01-01" })], new Date("2026-01-01T00:00:00Z"))!;
    expect(d.to).toBeGreaterThan(d.from);
  });
});

describe("patent matrix", () => {
  it("counts a family once per cell and keeps unattributed families out", () => {
    const m = buildPatentMatrix([
      patent({ familyKey: "F1", cpcCodes: ["H01L21/02", "H01L21/04"], citationsForward: 4 }),
      patent({ familyKey: "F1", cpcCodes: ["H01L21/02"] }), // duplicate owner row
      patent({ familyKey: "F2", cpcCodes: ["G06N3/08"], priorityDate: "2022-05-01", citationsForward: null, legalStatus: "application" }),
      patent({ familyKey: "F3", attributed: false }),
    ]);
    expect(m.attributedFamilies).toBe(2);
    expect(m.unattributedFamilies).toBe(1);
    expect(m.placedFamilies).toBe(2);
    expect(m.cells.get(cellKey("H01L", 2020))?.families).toBe(1);
    expect(m.cells.get(cellKey("H01L", 2020))?.citations).toBe(4);
    const g = m.cells.get(cellKey("G06N", 2022))!;
    expect(g.citations).toBe(0);
    expect(g.citationsUnknown).toBe(1);
    expect(g.dominantStatus).toBe("application");
    expect(m.years).toEqual([2020, 2021, 2022]);
  });
  it("reports families without a priority date or CPC class instead of dropping them silently", () => {
    const m = buildPatentMatrix([patent({ familyKey: "A", priorityDate: null }), patent({ familyKey: "B", cpcCodes: [] }), patent({ familyKey: "C" })]);
    expect(m.missingPriorityDate).toBe(1);
    expect(m.missingCpc).toBe(1);
    expect(m.placedFamilies).toBe(1);
    expect(m.attributedFamilies).toBe(3);
  });
  it("is empty (not zero-filled) without attributed families", () => {
    const m = buildPatentMatrix([patent({ attributed: false })]);
    expect(m.placedFamilies).toBe(0);
    expect(m.subclasses).toEqual([]);
    expect(m.years).toEqual([]);
  });
  it("marks mixed legal status and caps rows", () => {
    const m = buildPatentMatrix(
      [patent({ familyKey: "A", legalStatus: "granted" }), patent({ familyKey: "B", legalStatus: "expired" }), patent({ familyKey: "C", cpcCodes: ["B01D1/00"] })],
      1,
    );
    expect(m.cells.get(cellKey("H01L", 2020))?.dominantStatus).toBe("mixed");
    expect(m.subclasses).toEqual(["H01L"]);
    expect(m.hiddenSubclasses).toBe(1);
  });
  it("scales bubbles by area and citations by square root", () => {
    expect(bubbleSize(0, 4)).toBe(0);
    expect(bubbleSize(4, 4)).toBe(30);
    expect(bubbleSize(1, 4)).toBeLessThan(bubbleSize(4, 4));
    expect(citationLevel(0, 10)).toBe(0);
    expect(citationLevel(10, 10)).toBe(3);
    expect(citationLevel(1, 100)).toBe(1);
  });
});

describe("peer ranking", () => {
  const row = (peer: string, overlap: number | null, jac: number | null, peerFamilies: number): PatentOverlapRow => ({
    peer, peerFamilies, ownFamilies: 5, overlapShare: overlap, jaccard: jac, sharedSubclasses: [], interpretation: "",
  });
  it("ranks by overlap, shares a rank on ties and leaves peers without data unranked", () => {
    const out = rankPeers([row("C", 0.2, 0.1, 3), row("A", 0.5, 0.3, 4), row("B", 0.5, 0.3, 2), row("Z", null, null, 0)]);
    expect(out.map((r) => [r.peer, r.rank])).toEqual([["A", 1], ["B", 1], ["C", 3], ["Z", null]]);
    expect(out[3]!.ranked).toBe(false);
  });
});

describe("leadership", () => {
  it("lists executive edges and leadership events newest first and drops rejected ones", () => {
    const out = leadershipEntries({
      edges: [
        edge({ id: "1", edgeType: "executive", objectName: "Ann", validFrom: "2020-01-01", detail: { title: "CFO" }, status: "confirmed" }),
        edge({ id: "2", edgeType: "executive", objectName: "Bob", validFrom: "2024-01-01", status: "rejected" }),
        edge({ id: "3", edgeType: "partner", objectName: "NotAPerson" }),
      ],
      events: [
        { id: 9, eventType: "leadership", eventState: "announced", announcedAt: "2025-06-01", effectiveAt: null, knownAt: null, source: "8-K", sourceUrl: null, evidenceId: null, counterpartyKey: null, terms: {}, status: "suggested", thesisKpi: null, detail: { person: "Cy", title: "CEO" } },
      ],
    });
    expect(out.map((e) => e.name)).toEqual(["Cy", "Ann"]);
    expect(out[1]!.role).toBe("CFO");
  });
});

describe("what changed", () => {
  const n = net({
    edges: [edge({ id: "1", knownAt: "2026-03-10T00:00:00Z" }), edge({ id: "2", knownAt: "2026-01-10T00:00:00Z" }), edge({ id: "3", knownAt: null })],
    patents: [patent({ familyKey: "P", knownAt: "2026-03-11T00:00:00Z" }), patent({ familyKey: "P", knownAt: "2026-03-11T00:00:00Z" })],
  });
  it("lists rows learned of after the review date, newest first, and nothing without a date", () => {
    expect(whatChanged(n, null)).toEqual([]);
    expect(whatChanged(n, "garbage")).toEqual([]);
    const out = whatChanged(n, "2026-02-01T00:00:00Z");
    expect(out.map((c) => c.key)).toEqual(["patent:P", "edge:1"]);
  });
  it("honours the as-of upper bound and skips rows with no known-at", () => {
    const out = whatChanged(n, "2026-02-01T00:00:00Z", "2026-03-10T23:59:59Z");
    expect(out.map((c) => c.key)).toEqual(["edge:1"]);
  });
  it("finds the latest review date", () => {
    expect(latestDate(["2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", null, "bad"])).toBe("2026-02-01T00:00:00Z");
    expect(latestDate([])).toBeNull();
  });
});

describe("deals", () => {
  const step = (state: string, status: RowStatus) => ({ eventId: 1, state, status, date: null, knownAt: null, evidenceId: null, source: "s", sourceUrl: null, tier: "primary" as const });
  it("marks a stage confirmed only by a confirmed event, suggested-only stays provisional", () => {
    const stages = dealStages({ timeline: [step("announced", "confirmed"), step("approved", "suggested"), step("closed", "rejected"), step("amended", "disputed")] });
    const by = Object.fromEntries(stages.map((s) => [s.id, s.reach]));
    expect(by).toEqual({ announcement: "confirmed", amendment: "none", approval: "suggested", closing: "none", termination: "none" });
  });
  it("flags a shown state that is not the confirmed one", () => {
    expect(dealStateIsProvisional({ state: "approved", confirmedState: "announced" })).toBe(true);
    expect(dealStateIsProvisional({ state: "announced", confirmedState: "announced" })).toBe(false);
    expect(dealStateIsProvisional({ state: null, confirmedState: null })).toBe(false);
  });
  it("formats terms with explicit units and never invents a currency", () => {
    const f = (n: number) => String(n);
    const rows = termRows({ currency: "USD", total_value: "1500", cash_pct: 40, exchange_ratio: 0.5, note: "x" }, f);
    const get = (k: string) => rows.find((r) => r.key === k)?.value;
    expect(get("total_value")).toBe("1500 USD");
    expect(get("cash_pct")).toBe("40%");
    expect(get("exchange_ratio")).toBe("0.5");
    expect(rows.some((r) => r.key === "currency")).toBe(false);
    expect(termRows({ total_value: 5 }, f)[0]!.value).toContain("currency not stated");
  });
});

describe("concentration", () => {
  const p = (over: Partial<ConcentrationPeriod>): ConcentrationPeriod => ({
    fiscalPeriod: "FY2024", relationship: "customer", shareBasis: "issuer_revenue", namedCount: 1, anonymousCount: 0, namedShareSum: 0.2,
    anonymousShareSum: 0, reportedShareSum: 0.2, missingShareCount: 0, largestShare: 0.2, shareExceedsTotal: false, openContradictions: 0, ...over,
  });
  it("orders newest period first and flags sums with missing shares as a floor", () => {
    const rows = concentrationRows([p({}), p({ fiscalPeriod: "FY2025", missingShareCount: 1, namedCount: 2 })]);
    expect(rows.map((r) => r.fiscalPeriod)).toEqual(["FY2025", "FY2024"]);
    expect(rows[0]!.isFloor).toBe(true);
    expect(rows[1]!.isFloor).toBe(false);
    expect(rows[1]!.hasShare).toBe(true);
  });
  it("does not claim a share when every disclosure lacks one", () => {
    const rows = concentrationRows([p({ missingShareCount: 1, namedCount: 1, reportedShareSum: 0, namedShareSum: 0, largestShare: null })]);
    expect(rows[0]!.hasShare).toBe(false);
  });
});
