import { describe, expect, it } from "vitest";

import { digestKindLabel, digestOrder, forTicker } from "@/lib/disclosure/alerts";
import { confirmAllowed, ingestSummary, orderResults, orderSuggestions, serverMessage, suggestionFlags, type FilingResult, type IdentifierSuggestion } from "@/lib/disclosure/operations";

const f = (accession: string, o: Partial<FilingResult> = {}): FilingResult => ({ accession, status: "ingested", reason: null, quarantined: false, facts_mirrored: 1, note_rows: 0, gaps: [], ...o });
const s = (o: Partial<IdentifierSuggestion>): IdentifierSuggestion => ({ provider: "gleif", identifier_type: "lei", identifier: "X", issuer_id: 1, security_id: null, score: 0.9, name_only: false, ambiguous: false, stored: true, ...o });

describe("digest", () => {
  it("orders by count, then recency", () => {
    const g = (ticker: string, count: number, at: string) => ({ ticker, count, kinds: [], headlines: [], latest_known_at: at });
    expect(digestOrder([g("A", 1, "2026-06-05T00:00:00Z"), g("B", 3, "2026-06-01T00:00:00Z"), g("C", 1, "2026-06-09T00:00:00Z")]).map((x) => x.ticker)).toEqual(["B", "C", "A"]);
  });
  it("filters by ticker and keeps an unknown kind readable", () => {
    expect(forTicker([{ ticker: "A" }, { ticker: "B" }], "B")).toEqual([{ ticker: "B" }]);
    expect(forTicker([{ ticker: "A" }], null)).toHaveLength(1);
    expect(digestKindLabel("insider_trade")).toBe("Insider trade");
    expect(digestKindLabel("new_kind")).toBe("new kind");
  });
});

describe("ingest", () => {
  it("shows the server's message with its status", () => {
    expect(serverMessage({ detail: "Issuer has no CIK" }, 409, "Ingest")).toBe("Ingest failed (HTTP 409): Issuer has no CIK");
    expect(serverMessage({ detail: [{ msg: "bad limit" }] }, 422, "Ingest")).toContain("bad limit");
    expect(serverMessage(undefined, 502, "Ingest")).toContain("HTTP 502");
  });
  it("puts failures and gaps before clean filings", () => {
    expect(orderResults([f("ok"), f("gap", { gaps: ["no cash flow"] }), f("bad", { status: "failed", reason: "x" })]).map((x) => x.accession)).toEqual(["bad", "gap", "ok"]);
  });
  it("summarises counts", () => {
    expect(ingestSummary({ discovered: 3, ingested: 1, unchanged: 1, failed: 1, results: [] })).toContain("1 failed");
  });
});

describe("identifier decisions", () => {
  it("requires a 10-character note only for risky rows", () => {
    expect(confirmAllowed({ name_only: false, ambiguous: false }, "")).toBe(true);
    expect(confirmAllowed({ name_only: true, ambiguous: false }, "too short")).toBe(false);
    expect(confirmAllowed({ name_only: false, ambiguous: true }, "   short   ")).toBe(false);
    expect(confirmAllowed({ name_only: true, ambiguous: true }, "checked the register")).toBe(true);
  });
});

describe("identifier suggestions", () => {
  it("flags ambiguity and name-only matches and lists flagged first", () => {
    const list = [s({ identifier: "clean", score: 0.99 }), s({ identifier: "flag", score: 0.5, name_only: true, ambiguous: true })];
    expect(suggestionFlags(list[1])).toHaveLength(2);
    expect(orderSuggestions(list).map((x) => x.identifier)).toEqual(["flag", "clean"]);
  });
});
