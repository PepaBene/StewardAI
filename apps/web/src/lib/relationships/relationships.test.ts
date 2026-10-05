import { describe, expect, it } from "vitest";

import {
  actionDecisionBody,
  actionRequest,
  decisionBody,
  decisionsFor,
  emptyActionDraft,
  parseNetwork,
  pageRange,
  parseGraphPage,
  parseHistory,
  parseQueue,
  reconcileBody,
  reconcileConfirmText,
  parseReconciliations,
  safeUrl,
  shareText,
  validateAction,
  validateActionDecision,
  validateReason,
  validateTicker,
} from "./relationships";

describe("decision validation", () => {
  it("requires a reason of 10 trimmed characters and at most 2000", () => {
    expect(validateReason("too short")).toBeDefined();
    expect(validateReason("   short   ")).toBeDefined();
    expect(validateReason("Matches the filing text")).toBeUndefined();
    expect(validateReason("x".repeat(2001))).toBeDefined();
  });
  it("trims the reason in the request", () => {
    expect(decisionBody("dispute", "  two sources disagree  ")).toEqual({ decision: "dispute", reason: "two sources disagree" });
  });
  it("offers only moves that make sense, and reopen for decided rows", () => {
    expect(decisionsFor("suggested")).toEqual(["confirm", "reject", "dispute"]);
    expect(decisionsFor("confirmed")).toEqual(["reject", "dispute"]);
    expect(decisionsFor("rejected")).toEqual(["reopen"]);
    expect(decisionsFor("disputed")).toEqual(["reopen"]);
  });
  it("checks tickers against the API pattern", () => {
    expect(validateTicker("AAPL_US_EQ")).toBeUndefined();
    expect(validateTicker("a b")).toBeDefined();
    expect(validateTicker("")).toBeDefined();
  });
});

describe("parsers", () => {
  it("reads the queue and keeps unknown values null", () => {
    const q = parseQueue({
      filing_hits: [{ id: 1, ticker: "AAA", review_state: "pending", status: "open", url: "https://sec.gov/x" }],
      suggested_disclosures: [
        { id: 5, ticker: "AAA", relationship: "customer", direction: "incoming", counterparty_name: "Acme", anonymous_counterparty: false, reported_share: 0.12, passage: "p", known_at: "t", status: "suggested" },
        { id: 6, ticker: "AAA", relationship: "customer", direction: "incoming", anonymous_counterparty: true, known_at: "t", status: "disputed" },
      ],
      contradictions: [{ id: 9, ticker: "AAA", kind: "share_mismatch", disclosure_id: 5, other_disclosure_id: 7, detail: { a: 1 }, created_at: "t", status: "open" }],
    });
    expect(q.hits).toMatchObject([{ id: 1, status: "open", filer: null }]);
    expect(q.disclosures[0]).toMatchObject({ id: 5, share: 0.12, status: "suggested", counterparty: "Acme" });
    expect(q.disclosures[1]).toMatchObject({ anonymous: true, share: null, status: "disputed", counterparty: "Not named by the filer" });
    expect(q.contradictions[0]).toMatchObject({ otherId: 7, detail: "A: 1", status: "open" });
  });
  it("reads the typed reconciliations and global lists, keeping nulls", () => {
    expect(parseReconciliations({ open: [{ id: 3, ticker: "AAA", change_kind: "parent_change", observed_source: "s", observed_at: "2026-09-01T00:00:00Z", outcome: "unexplained" }] })).toMatchObject([{ id: 3, fromKey: null, toKey: null }]);
    const page = parseGraphPage({
      total: 30,
      limit: 25,
      offset: 0,
      rows: [
        { id: 1, ticker: "AAA", kind: "supplier", status: "disputed", source: "s", source_url: "https://x.example/a", known_at: "2026-09-01T00:00:00Z" },
        { id: 2, ticker: "AAA", kind: "customer", status: "weird", source: "s", source_url: "", known_at: "t", anonymous_counterparty: true, reported_share: null },
      ],
    });
    expect(page.rows[0]).toMatchObject({ status: "disputed", rawStatus: "disputed", counterparty: null, reportedShare: null });
    expect(page.rows[1]).toMatchObject({ status: null, rawStatus: "weird", counterparty: "Not named by the filer", sourceUrl: null });
    expect(pageRange(page)).toBe("1 to 2 of 30");
  });
  it("orders decision history newest first and keeps an unknown status as its word", () => {
    const h = parseHistory({
      kind: "edge",
      id: 4,
      current_status: "confirmed",
      limitations: ["only owner decisions are logged"],
      entries: [
        { changed_at: "2026-09-01T10:00:00Z", from_status: null, to_status: "suggested" },
        { changed_at: "2026-09-02T10:00:00Z", from_status: "suggested", to_status: "confirmed", reason: "checked the filing", decided_by: "owner" },
      ],
    });
    expect(h.entries.map((e) => e.to)).toEqual(["confirmed", "suggested"]);
    expect(h.entries[0]).toMatchObject({ from: "suggested", reason: "checked the filing", by: "owner" });
    expect(h.entries[1].from).toBeNull();
  });
  it("builds the reconciliation resolve body from the owner's trimmed reason", () => {
    expect(reconcileBody("dismissed", "  not a real change  ")).toEqual({ resolution: "dismissed", reason: "not a real change" });
    expect(reconcileConfirmText({ id: 3, ticker: "AAA" }, "resolved")).toContain("#3");
  });
  it("survives a missing holding network", () => {
    expect(parseNetwork(undefined, "AAA")).toEqual({ edges: [], events: [], disclosures: [] });
  });
  it("reads the network with statuses", () => {
    const n = parseNetwork({ edges: [{ id: 1, object_name: "Beta", edge_type: "supplier", status: "disputed" }], events: [{ id: 2, status: "confirmed", event_type: "deal" }], disclosures: [{ id: 3, status: "rejected" }] }, "AAA");
    expect(n.edges[0].status).toBe("disputed");
    expect(n.events[0].status).toBe("confirmed");
    expect(n.disclosures[0]).toMatchObject({ ticker: "AAA", status: "rejected" });
  });
  it("formats a share as reported and only links web addresses", () => {
    expect(shareText(null)).toBe("not reported");
    expect(shareText(0.1)).toBe("10% as reported");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("https://example.com/a")).toBe("https://example.com/a");
  });
});

describe("corporate action entry", () => {
  const ok = { ...emptyActionDraft("AAA"), exDate: "2026-03-01", cashAmount: "0.5", currency: "USD", source: "Issuer", sourceUrl: "https://example.com/div" };
  it("accepts a complete cash dividend", () => {
    expect(validateAction(ok)).toEqual({});
    expect(actionRequest(ok)).toMatchObject({ action_type: "cash_dividend", cash_amount: 0.5, ratio: null, record_date: null, status: "suggested", evidence_id: null });
  });
  it("applies the cross-field rules of the model", () => {
    expect(validateAction({ ...ok, currency: "" }).currency).toBeDefined();
    expect(validateAction({ ...ok, cashAmount: "" }).cashAmount).toBeDefined();
    expect(validateAction({ ...ok, actionType: "split", cashAmount: "1" }).ratio).toBeDefined();
    expect(validateAction({ ...ok, actionType: "stock_dividend", ratio: "" }).ratio).toMatch(/cash amount/);
    expect(validateAction({ ...ok, actionType: "split", ratio: "0" }).ratio).toBeDefined();
    expect(validateAction({ ...ok, status: "confirmed" }).evidenceId).toBeDefined();
    expect(validateAction({ ...ok, status: "confirmed", evidenceId: "12" })).toEqual({});
  });
  it("checks the field limits", () => {
    expect(validateAction({ ...ok, symbol: "A B" }).symbol).toBeDefined();
    expect(validateAction({ ...ok, exDate: "01/03/2026" }).exDate).toBeDefined();
    expect(validateAction({ ...ok, currency: "usd" }).currency).toBeDefined();
    expect(validateAction({ ...ok, currency: "GBp" }).currency).toBeUndefined();
    expect(validateAction({ ...ok, source: "I" }).source).toBeDefined();
    expect(validateAction({ ...ok, sourceUrl: "ftp://x.example" }).sourceUrl).toBeDefined();
  });
  it("needs evidence to confirm unless one is already linked", () => {
    expect(validateActionDecision("confirmed", "Checked on the issuer site", "", null).evidence).toBeDefined();
    expect(validateActionDecision("confirmed", "Checked on the issuer site", "", 4)).toEqual({});
    expect(validateActionDecision("rejected", "short", "", null).reason).toBeDefined();
    expect(actionDecisionBody("confirmed", " Checked on the issuer site ", "7")).toEqual({ decision: "confirmed", reason: "Checked on the issuer site", evidence_id: 7 });
  });
});
