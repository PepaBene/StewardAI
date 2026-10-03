import { describe, expect, it } from "vitest";

import { num, parseBusinessNetwork, parseConcentration, parseDeals, parseKnockOn, parsePatentOverlap, parsePatentTrends, parseReviewQueue, toStatus, toTier } from "./parse";

const base = { ticker: "T", known_as_of: null, edges: [], events: [], disclosures: [], patents: [], limitations: ["l"] };

describe("primitives", () => {
  it("reads numbers and numeric strings, never coercing blanks to zero", () => {
    expect(num(3)).toBe(3);
    expect(num("0.25")).toBe(0.25);
    expect(num("")).toBeNull();
    expect(num(null)).toBeNull();
    expect(num(Number.NaN)).toBeNull();
    expect(num("abc")).toBeNull();
  });
  it("never upgrades an unknown status or tier", () => {
    expect(toStatus("confirmed")).toBe("confirmed");
    expect(toStatus("CONFIRMED")).toBe("unrecognised");
    expect(toStatus(undefined)).toBe("unrecognised");
    expect(toTier("primary")).toBe("primary");
    expect(toTier("gold")).toBe("unknown");
  });
});

describe("parseBusinessNetwork", () => {
  it("rejects a body of the wrong shape instead of returning an empty graph", () => {
    expect(parseBusinessNetwork(null).ok).toBe(false);
    expect(parseBusinessNetwork([]).ok).toBe(false);
    expect(parseBusinessNetwork({ ...base, edges: "none" }).ok).toBe(false);
    expect(parseBusinessNetwork({ ...base, edges: [{ id: "x", object_name: "a", edge_type: "partner" }] }).ok).toBe(false);
  });
  it("parses normalised and legacy edges and keeps jsonb text details", () => {
    const r = parseBusinessNetwork({
      ...base,
      edges: [
        { id: 4, subject_key: "T", object_key: "k", object_name: "Acme", edge_type: "partner", direction: "symmetric", status: "suggested", evidence_tier: "vendor", valid_from: "2024-01-01", detail: '{"title":"x"}' },
        { id: "legacy:7", subject_key: "T", object_key: "l", object_name: "Parent Co", edge_type: "parent", status: "confirmed", evidence_tier: "primary", detail: {} },
      ],
    });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const [a, b] = r.value.edges;
    expect(a!.status).toBe("suggested");
    expect(a!.legacy).toBe(false);
    expect(a!.detail).toEqual({ title: "x" });
    expect(b!.legacy).toBe(true);
    expect(b!.rowId).toBe(7);
  });
  it("treats a patent without the attributed flag as not attributed", () => {
    const r = parseBusinessNetwork({ ...base, patents: [{ family_key: "F", cpc_codes: ["H01L21/02"], citations_forward: "3", legal_status: "granted" }] });
    expect(r.ok && r.value.patents[0]!.attributed).toBe(false);
    expect(r.ok && r.value.patents[0]!.citationsForward).toBe(3);
    expect(r.ok && r.value.patents[0]!.citationsBackward).toBeNull();
  });
  it("accepts CPC codes delivered as a JSON string", () => {
    const r = parseBusinessNetwork({ ...base, patents: [{ family_key: "F", cpc_codes: '["A61K1/00"]', attributed: true }] });
    expect(r.ok && r.value.patents[0]!.cpcCodes).toEqual(["A61K1/00"]);
  });
});

describe("other payloads", () => {
  it("parses deals with timeline and pro forma and rejects a missing timeline", () => {
    const deal = { deal_key: "T:deal:acme", state: "announced", confirmed_state: null, confirmed_terms: {}, timeline: [{ event_id: 1, state: "announced", status: "suggested", evidence_tier: "vendor" }], pro_forma: null, pro_forma_unavailable: "no confirmed terms" };
    const ok = parseDeals({ ticker: "T", known_as_of: null, deals: [deal], limitations: [] });
    expect(ok.ok && ok.value.deals[0]!.confirmedState).toBeNull();
    expect(ok.ok && ok.value.deals[0]!.proFormaUnavailable).toBe("no confirmed terms");
    expect(parseDeals({ ticker: "T", deals: [{ deal_key: "k" }] }).ok).toBe(false);
  });
  it("parses concentration with decimal strings", () => {
    const r = parseConcentration({
      ticker: "T",
      periods: [{ fiscal_period: "FY2024", relationship: "customer", share_basis: "issuer_revenue", named_count: 1, anonymous_count: 0, named_share_sum: "0.31", anonymous_share_sum: 0, reported_share_sum: "0.31", missing_share_count: 0, largest_share: "0.31", share_exceeds_total: false, open_contradictions: 0 }],
      limitations: [],
    });
    expect(r.ok && r.value.periods[0]!.largestShare).toBe(0.31);
    expect(parseConcentration({ ticker: "T", periods: [{ fiscal_period: "FY" }] }).ok).toBe(false);
  });
  it("parses the per-side concentration fields, keeping a null sum null", () => {
    const r = parseConcentration({
      ticker: "T",
      periods: [{ fiscal_period: "FY2024", relationship: "customer", named_count: 1, anonymous_count: 1, missing_share_count: 1, named_missing_share_count: 0, anonymous_missing_share_count: 1, named_reported_share_sum: "0.2", anonymous_reported_share_sum: null, total_reported_share_sum: "0.2", named_at_least: false, anonymous_at_least: true, total_at_least: true }],
    });
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const p = r.value.periods[0]!;
    expect([p.namedReportedShareSum, p.anonymousReportedShareSum, p.totalReportedShareSum]).toEqual([0.2, null, 0.2]);
    expect([p.namedAtLeast, p.anonymousAtLeast, p.totalAtLeast]).toEqual([false, true, true]);
    expect(p.anonymousMissingShareCount).toBe(1);
  });
  it("keeps null forward citations as unknown and parses families_with_citation_data", () => {
    const t = parsePatentTrends([{ cpc: "H01L", year: 2020, families: 3, forward_citations: null, families_with_citation_data: 0, legal_status_counts: {} }]);
    expect(t.ok && t.value[0]!.forwardCitations).toBeNull();
    expect(t.ok && t.value[0]!.familiesWithCitationData).toBe(0);
  });
  it("parses trends and overlap, keeping null overlap as null", () => {
    const t = parsePatentTrends([{ cpc: "H01L21/02", year: 2020, families: 2, forward_citations: 5, legal_status_counts: { granted: 2 }, median_years_to_grant: null }]);
    expect(t.ok && t.value[0]!.medianYearsToGrant).toBeNull();
    expect(parsePatentTrends({}).ok).toBe(false);
    const o = parsePatentOverlap({ peers: [{ peer: "P", peer_families: 0, own_families: 4, overlap_share: null, jaccard: null, shared_subclasses: [] }] });
    expect(o.ok && o.value[0]!.overlapShare).toBeNull();
  });
  it("parses the review queue and the knock-on list", () => {
    const q = parseReviewQueue({ filing_hits: [{ id: 1, ticker: "T", review_state: "pending" }], suggested_disclosures: [], contradictions: [{ id: 2, ticker: "T", relationship: "customer", kind: "share_over_100" }] });
    expect(q.ok && q.value.filingHits[0]!.reviewState).toBe("pending");
    expect(parseReviewQueue({ filing_hits: [] }).ok).toBe(false);
    const k = parseKnockOn({ known_as_of: null, links: [{ ticker: "A", counterparty_ticker: "B", relationship: "customer", direction: "issuer_sells_to", reported_share: "0.1" }], limitations: [] });
    expect(k.ok && k.value.links[0]!.reportedShare).toBe(0.1);
  });
});
