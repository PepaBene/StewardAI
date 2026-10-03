import { describe, expect, it } from "vitest";

import { allowedDecisions } from "@/components/network/decision-control";

import {
  asOfParam,
  buildPatentMatrix,
  concentrationRows,
  dealStages,
  familyDetailMap,
  largestReportedShare,
  leadershipEntries,
  peerTakeaway,
  rankPeers,
  resourceReason,
  resourceStatus,
  reviewState,
  sideShareLabel,
  termRows,
} from "./logic";
import { forwardCitations, parseBusinessNetwork } from "./parse";
import type { ConcentrationPeriod, NetworkEvent, PatentFamily, PatentOverlapRow } from "./types";

const period = (over: Partial<ConcentrationPeriod> = {}): ConcentrationPeriod => ({
  fiscalPeriod: "FY2025", relationship: "customer", shareBasis: "issuer_revenue", namedCount: 0, anonymousCount: 0, namedShareSum: 0, anonymousShareSum: 0,
  reportedShareSum: null, missingShareCount: 0, namedMissingShareCount: null, anonymousMissingShareCount: null, namedReportedShareSum: null, anonymousReportedShareSum: null, totalReportedShareSum: null, namedAtLeast: null, anonymousAtLeast: null, totalAtLeast: null, largestShare: null, shareExceedsTotal: false, openContradictions: 0, ...over,
});
const fam = (over: Partial<PatentFamily> = {}): PatentFamily => ({
  familyKey: "F1", priorityDate: "2020-03-01", firstPublicationDate: null, title: "t", cpcCodes: ["H01L21/02"], jurisdictions: [], citationsForward: 1, citationsBackward: null,
  legalStatus: "granted", source: "owner", sourceUrl: null, knownAt: null, applicant: null, ownershipConfidence: null, ownershipBasis: null, ownershipValidFrom: null, ownershipValidTo: null,
  evidenceId: null, attributed: true, ...over,
});
const fmt = (v: number) => `${(v * 100).toFixed(1)}%`;

describe("F5-02 as-of is never a future instant", () => {
  const now = new Date("2026-10-03T12:00:00Z");
  it("today and later days mean 'now' (no param); past days are end of day UTC", () => {
    expect(asOfParam("2026-10-03", now)).toBeUndefined();
    expect(asOfParam("2026-10-04", now)).toBeUndefined();
    expect(asOfParam("2026-10-02", now)).toBe("2026-10-02T23:59:59Z");
    expect(asOfParam("", now)).toBeUndefined();
  });
  it("compares in UTC: the end of a day is allowed once it has passed", () => {
    expect(asOfParam("2026-10-03", new Date("2026-10-04T00:00:00Z"))).toBe("2026-10-03T23:59:59Z");
  });
});

describe("F5-04 concentration floors and unknowns", () => {
  it("anonymous disclosures missing a share are unknown, not 0.0%", () => {
    const [r] = concentrationRows([period({ namedCount: 2, anonymousCount: 1, namedShareSum: 0.5, anonymousShareSum: 0, missingShareCount: 1, namedMissingShareCount: null, anonymousMissingShareCount: null, namedReportedShareSum: null, anonymousReportedShareSum: null, totalReportedShareSum: null, namedAtLeast: null, anonymousAtLeast: null, totalAtLeast: null, largestShare: 0.3 })]);
    expect(sideShareLabel(r!.anonymous, fmt)).toBe("not reported");
    expect(sideShareLabel(r!.named, fmt)).toBe("at least 50.0%");
  });
  it("a side with no counterparty is 'none disclosed', never 0.0%", () => {
    const [r] = concentrationRows([period({ namedCount: 2, anonymousCount: 0, namedShareSum: 0.3, anonymousShareSum: 0, missingShareCount: 1, namedMissingShareCount: null, anonymousMissingShareCount: null, namedReportedShareSum: null, anonymousReportedShareSum: null, totalReportedShareSum: null, namedAtLeast: null, anonymousAtLeast: null, totalAtLeast: null, largestShare: 0.3 })]);
    expect(sideShareLabel(r!.anonymous, fmt)).toBe("none disclosed");
    expect(r!.named.kind).toBe("floor");
  });
  it("every share reported is exact; nothing reported is unknown", () => {
    const [exact] = concentrationRows([period({ namedCount: 2, namedShareSum: 0.5, largestShare: 0.3 })]);
    expect(sideShareLabel(exact!.named, fmt)).toBe("50.0%");
    const [none] = concentrationRows([period({ namedCount: 2, namedShareSum: 0, missingShareCount: 2 })]);
    expect(sideShareLabel(none!.named, fmt)).toBe("not reported");
  });
  it("the takeaway maximum ignores periods without a share", () => {
    expect(largestReportedShare([{ largestShare: null }, { largestShare: null }])).toBeNull();
    expect(largestReportedShare([{ largestShare: null }, { largestShare: 0.2 }])).toBe(0.2);
  });
});

describe("per-side concentration labels (exact)", () => {
  const side = (over: Partial<ConcentrationPeriod>) => concentrationRows([period({ namedCount: 2, anonymousCount: 1, ...over })])[0]!;
  it("labels a floor only on the side whose disclosure lacks a share", () => {
    const r = side({
      namedReportedShareSum: 0.5, namedMissingShareCount: 0, namedAtLeast: false,
      anonymousReportedShareSum: 0.1, anonymousMissingShareCount: 1, anonymousAtLeast: true,
      totalReportedShareSum: 0.6, totalAtLeast: true, missingShareCount: 1,
    });
    expect(sideShareLabel(r.named, fmt)).toBe("50.0%");
    expect(sideShareLabel(r.anonymous, fmt)).toBe("at least 10.0%");
  });
  it("a null side sum is not reported, never 0; the other side stays separate", () => {
    const r = side({
      namedReportedShareSum: 0.4, namedMissingShareCount: 0, namedAtLeast: false,
      anonymousReportedShareSum: null, anonymousMissingShareCount: 1, anonymousAtLeast: true,
      missingShareCount: 1,
    });
    expect(sideShareLabel(r.anonymous, fmt)).toBe("not reported");
    expect(sideShareLabel(r.named, fmt)).toBe("40.0%");
  });
});

describe("F5-06 forward citations 0 from EPO OPS are unknown", () => {
  it("normalises the OPS zero to null, keeps real zeros and counts", () => {
    expect(forwardCitations(0, "epo_ops")).toBeNull();
    expect(forwardCitations(0, "owner")).toBe(0);
    expect(forwardCitations(7, "epo_ops")).toBe(7);
  });
  it("parsed OPS families count as unknown in the matrix", () => {
    const parsed = parseBusinessNetwork({
      ticker: "T", known_as_of: null, edges: [], events: [], disclosures: [], limitations: [],
      patents: [{ family_key: "F1", source: "epo_ops", citations_forward: 0, attributed: true, priority_date: "2020-01-01", cpc_codes: ["H01L21/02"] }],
    });
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const m = buildPatentMatrix(parsed.value.patents);
    expect([...m.cells.values()][0]!.citationsUnknown).toBe(1);
  });
});

describe("F5-11 decision buttons by status", () => {
  it("an unrecognised status offers no decision", () => {
    expect(allowedDecisions("unrecognised", true)).toEqual([]);
    expect(allowedDecisions("unrecognised", false)).toEqual([]);
  });
  it("a legacy confirmed edge cannot be rejected; suggested and disputed can be confirmed or rejected", () => {
    expect(allowedDecisions("confirmed", false)).toEqual([]);
    expect(allowedDecisions("rejected", false)).toEqual([]);
    expect(allowedDecisions("suggested", false)).toEqual(["confirm", "reject"]);
    expect(allowedDecisions("disputed", false)).toEqual(["confirm", "reject"]);
  });
  it("normalised edges keep the full transition set", () => {
    expect(allowedDecisions("confirmed", true)).toEqual(["reject", "dispute"]);
    expect(allowedDecisions("suggested", true)).toEqual(["confirm", "reject", "dispute"]);
  });
});

describe("F5-12 peer takeaway", () => {
  const row = (over: Partial<PatentOverlapRow>): PatentOverlapRow => ({ peer: "P", peerFamilies: 3, ownFamilies: 0, overlapShare: null, jaccard: null, sharedSubclasses: [], interpretation: "", ...over });
  it("does not print 0% when no peer is ranked", () => {
    const ranked = rankPeers([row({ peer: "A" }), row({ peer: "B" })]);
    const text = peerTakeaway(ranked, 2, fmt);
    expect(text).toContain("0 of 2");
    expect(text).not.toContain("0.0%");
    expect(text).not.toContain("top peer shares");
  });
  it("names the top RANKED peer share", () => {
    const ranked = rankPeers([row({ peer: "A" }), row({ peer: "B", overlapShare: 0.4, jaccard: 0.2 })]);
    expect(peerTakeaway(ranked, 2, fmt)).toContain("top peer shares 40.0%");
  });
});

describe("F5-13 deal stages ignore events the state machine ignored", () => {
  const step = (eventId: number, state: string) => ({ eventId, state, status: "confirmed" as const, date: null, knownAt: null, evidenceId: null, source: "s", sourceUrl: null, tier: "primary" as const });
  it("an ignored confirmed 'approved' event does not mark the approval stage", () => {
    const stages = dealStages({ timeline: [step(1, "announced"), step(2, "approved")], ignored: [{ eventId: 2, reason: "after terminated" }] });
    expect(stages.find((s) => s.id === "approval")!.reach).toBe("none");
    expect(stages.find((s) => s.id === "announcement")!.reach).toBe("confirmed");
  });
});

describe("F5-14 patent matrix year axis", () => {
  it("keeps the newest years when the span exceeds the cap", () => {
    const m = buildPatentMatrix([fam({ familyKey: "A", priorityDate: "1900-01-01" }), fam({ familyKey: "B", priorityDate: "2020-01-01" })]);
    expect(m.years.at(-1)).toBe(2020);
    expect(m.years).toHaveLength(60);
    expect(m.olderYearsDropped).toBeGreaterThan(0);
  });
});

describe("F5-15 detail list uses the same owner row as the matrix", () => {
  it("prefers the attributed owner row of a family", () => {
    const m = familyDetailMap([fam({ applicant: "Sub", attributed: false }), fam({ applicant: "Parent", attributed: true })]);
    expect(m.get("F1")!.applicant).toBe("Parent");
    expect(buildPatentMatrix([fam({ attributed: false }), fam({ attributed: true })]).attributedFamilies).toBe(1);
  });
});

describe("F5-16 review provenance", () => {
  it("all rejected or none is not verified", () => {
    expect(reviewState([{ status: "rejected" }])).toBe("not_covered");
    expect(reviewState([])).toBe("not_covered");
    expect(reviewState([{ status: "confirmed" }])).toBe("verified");
    expect(reviewState([{ status: "confirmed" }, { status: "unrecognised" }])).toBe("partial");
  });
});

describe("F5-17 labels and units", () => {
  it("ownership_pct and interest_rate_pct are shown as percentages", () => {
    const rows = termRows({ ownership_pct: "5.5", interest_rate_pct: 3.25 }, (n) => String(n));
    expect(rows.find((r) => r.key === "ownership_pct")!.value).toBe("5.5%");
    expect(rows.find((r) => r.key === "interest_rate_pct")!.value).toBe("3.25%");
  });
  it("a leadership event has no end date and says so instead of 'present'", () => {
    const ev = { id: 1, eventType: "leadership", eventState: "announced", announcedAt: "2025-01-01", effectiveAt: null, knownAt: null, source: "x", sourceUrl: null, evidenceId: null, counterpartyKey: null, terms: {}, status: "suggested", thesisKpi: null, detail: {} } as NetworkEvent;
    const [e] = leadershipEntries({ edges: [], events: [ev] });
    expect(e!.endUnknown).toBe(true);
    expect(e!.to).toBeNull();
  });
});

describe("F5-09 failed requests are not empty results", () => {
  it("loading, error and a rejected shape are never 'ok'", () => {
    expect(resourceStatus({ kind: "loading" })).toBe("loading");
    expect(resourceStatus({ kind: "error", title: "HTTP 500" })).toBe("unavailable");
    expect(resourceStatus({ kind: "ok", data: { ok: false, reason: "bad" } })).toBe("unavailable");
    expect(resourceStatus({ kind: "ok", data: { ok: true } })).toBe("ok");
    expect(resourceReason({ kind: "error", title: "HTTP 500" })).toBe("HTTP 500");
  });
});
