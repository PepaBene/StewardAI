import { describe, expect, it } from "vitest";

import {
  emptyExposureDraft,
  emptyOverrideDraft,
  emptyPatentDraft,
  emptyPeerDraft,
  exposureRequest,
  listIn,
  mappedShare,
  parsePeerSet,
  parseReturns,
  validateExposure,
  validateOverride,
  validatePatent,
  validatePeer,
  validatePeerStatus,
} from "./endpoint-forms";

describe("peer validation", () => {
  it("needs evidence to confirm and rejects self-peering", () => {
    const d = { ...emptyPeerDraft(), peer: "AAPL", basis: "same industry", status: "confirmed" as const };
    expect(validatePeer(d, "MSFT").evidenceId).toBeTruthy();
    expect(validatePeer({ ...d, evidenceId: "4" }, "MSFT")).toEqual({});
    expect(validatePeer({ ...d, evidenceId: "4" }, "aapl").peer).toBeTruthy();
  });
  it("status change needs a reason and evidence when none is on file", () => {
    expect(validatePeerStatus("rejected", "no", "", false).reason).toBeTruthy();
    expect(validatePeerStatus("confirmed", "looks right", "", false).evidenceId).toBeTruthy();
    expect(validatePeerStatus("confirmed", "looks right", "", true)).toEqual({});
  });
  it("parses the peer set with unknown status as suggested", () => {
    const p = parsePeerSet({ ticker: "Y", peers: [{ id: 1, peer_ticker: "X", basis: "b", source: "s", status: "weird", evidence_id: null, known_at: "t" }] });
    expect(p).toHaveLength(1);
    expect(p[0].status).toBe("suggested");
    expect(p[0].evidenceId).toBeNull();
  });
});

describe("exposure", () => {
  it("requires a revision with a product code and a share within 0 to 100", () => {
    const d = { ...emptyExposureDraft("3"), segment: "Cloud", kind: "product" as const, hsCode: "8541", hsRevision: "", weightPct: "40", validFrom: "2025-01-01" };
    expect(validateExposure(d).hsRevision).toBeTruthy();
    expect(validateExposure({ ...d, hsRevision: "HS2022", weightPct: "140" }).weightPct).toBeTruthy();
    expect(validateExposure({ ...d, hsRevision: "HS2022" })).toEqual({});
  });
  it("sends the percentage as a fraction", () => {
    const r = exposureRequest({ ...emptyExposureDraft("3"), segment: "US", geography: "us", weightPct: "37.5", validFrom: "2025-01-01" });
    expect(r.weight).toBe("0.375");
    expect(r.geography).toBe("US");
    expect(r.hs_code).toBeNull();
  });
  it("flags only over-allocation, never fills the remainder", () => {
    const m = mappedShare([{ weight: "0.6", geography: "US" }, { weight: "0.5", geography: "DE" }, { weight: "0.2", hs_code: "8541" }]);
    expect(m.geographyOver).toBe(true);
    expect(m.productOver).toBe(false);
  });
});

describe("other forms", () => {
  it("override needs a target and a reason", () => {
    expect(validateOverride(emptyOverrideDraft()).issuerId).toBeTruthy();
    expect(validateOverride({ ...emptyOverrideDraft(), listingId: "9", reason: "checked FIRDS" })).toEqual({});
  });
  it("direct patent attribution needs evidence", () => {
    const d = { ...emptyPatentDraft(), familyKey: "F1", applicant: "ACME", sourceUrl: "https://epo.org/x", confidence: "0.8", basis: "direct" as const };
    expect(validatePatent(d).evidenceId).toBeTruthy();
  });
});

describe("returns", () => {
  it("keeps total return unknown with its reason, never 0", () => {
    const result = {
      symbol: "X", source: "s", chosen: true, raw_price_return: 0.1, price_return: 0.12, total_return: null, total_return_reason: "actions incomplete",
      action_coverage: "partial", coverage: 0.9, expected_sessions: 250, observed_sessions: 225, calendar_basis: "weekdays",
      missing_actions: { price_raw: [], price_adjusted: ["a"], total_return: ["b", "c"] }, observations: 225, start: "2025-01-01", end: "2026-01-01",
    };
    const v = parseReturns({ ticker: "X", known_as_of: "t", results: [result] });
    expect(v?.bars.find((b) => b.id === "total")).toMatchObject({ value: null, reason: "actions incomplete" });
    expect(v?.bars[0].value).toBeCloseTo(0.1);
    expect(v?.missingActions).toBe(3);
  });
  it("explains an empty result", () => {
    expect(parseReturns({ ticker: "X", known_as_of: "t", results: [], reason: "no stored price series" })?.reason).toBe("no stored price series");
  });
  it("finds the list in an untyped response", () => {
    expect(listIn({ ticker: "X", deals: [{ a: 1 }] }, ["deals"])).toHaveLength(1);
  });
});
