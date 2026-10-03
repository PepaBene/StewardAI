import { describe, expect, it } from "vitest";

import { asDay, asNumber, parseCorporateActions, parseExchangeSessions, parseExpectedSessions, parseListing, parseReturns } from "./parse";

const row = (over: Record<string, unknown> = {}) => ({
  id: 7,
  symbol: "CEZ.PR",
  action_type: "cash_dividend",
  ex_date: "2026-06-01",
  record_date: null,
  payment_date: "2026-06-20",
  ratio: null,
  cash_amount: "46.0",
  currency: "CZK",
  source: "issuer",
  source_url: "https://example.test/a",
  known_at: "2026-05-01T10:00:00Z",
  evidence_id: 3,
  status: "confirmed",
  raw: '{"per":"ordinary_share","amount_basis":"split_adjusted"}',
  decided_at: null,
  decision_reason: null,
  ...over,
});

describe("scalars", () => {
  it("reads numbers and numeric strings, never NaN or empty", () => {
    expect(asNumber("46.0")).toBe(46);
    expect(asNumber(1.5)).toBe(1.5);
    expect(asNumber("")).toBeNull();
    expect(asNumber("abc")).toBeNull();
    expect(asNumber(Number.NaN)).toBeNull();
    expect(asNumber(null)).toBeNull();
    expect(asDay("2026-06-01T10:00:00Z")).toBe("2026-06-01");
    expect(asDay("June")).toBeNull();
  });
});

describe("corporate actions", () => {
  it("parses a ledger row, including a JSON-string raw and Decimal strings", () => {
    const r = parseCorporateActions([row()]);
    expect(r.ok && r.data[0]).toMatchObject({ id: 7, type: "cash_dividend", cashAmount: 46, per: "ordinary_share", amountBasis: "split_adjusted", status: "confirmed", evidenceId: 3 });
  });
  it("keeps a missing amount null, not zero", () => {
    const r = parseCorporateActions([row({ cash_amount: null, evidence_id: null })]);
    expect(r.ok && r.data[0].cashAmount).toBeNull();
    expect(r.ok && r.data[0].evidenceId).toBeNull();
  });
  it("accepts an empty ledger and rejects wrong shapes instead of defaulting", () => {
    expect(parseCorporateActions([])).toEqual({ ok: true, data: [] });
    expect(parseCorporateActions({}).ok).toBe(false);
    expect(parseCorporateActions([row({ action_type: "rights_issue" })]).ok).toBe(false);
    expect(parseCorporateActions([row({ status: "maybe" })]).ok).toBe(false);
    expect(parseCorporateActions([row({ ex_date: null })]).ok).toBe(false);
    expect(parseCorporateActions([row(), 5]).ok).toBe(false);
  });
});

describe("returns", () => {
  const result = {
    symbol: "cez.cz",
    source: "stooq",
    chosen: true,
    currency: "CZK",
    mic: "XPRA",
    price_return: 0.12,
    price_return_reason: null,
    raw_price_return: "-0.5",
    total_return: null,
    total_return_reason: "corporate-action coverage is incomplete",
    action_coverage: "incomplete",
    coverage: "0.9",
    expected_sessions: 250,
    observed_sessions: 225,
    calendar_basis: "weekdays_minus_configured_closures",
    missing_actions: { price_raw: [], price_adjusted: [], total_return: ["x"] },
    start: "2025-10-03",
    end: "2026-10-03",
  };
  it("parses figures, reasons and coverage without turning null into zero", () => {
    const r = parseReturns({ ticker: "CEZ", known_as_of: "2026-10-03", results: [result] });
    expect(r.ok && r.data.result).toMatchObject({
      priceReturn: { value: 0.12, reason: null },
      rawPriceReturn: { value: -0.5 },
      totalReturn: { value: null, reason: "corporate-action coverage is incomplete" },
      coverage: 0.9,
      actionCoverage: "incomplete",
      missingActions: { totalReturn: ["x"] },
    });
  });
  it("reports an empty result list with the backend reason", () => {
    const r = parseReturns({ ticker: "CEZ", known_as_of: "2026-10-03", results: [], reason: "no stored price series with a known quote currency" });
    expect(r.ok && r.data).toMatchObject({ result: null, reason: "no stored price series with a known quote currency" });
  });
  it("rejects unknown coverage values and malformed bodies", () => {
    expect(parseReturns({ ticker: "CEZ", results: [{ ...result, action_coverage: "yes" }] }).ok).toBe(false);
    expect(parseReturns({ ticker: "CEZ" }).ok).toBe(false);
    expect(parseReturns(null).ok).toBe(false);
    expect(parseReturns({ ticker: "CEZ", results: [{ ...result, start: null }] }).ok).toBe(false);
  });
});

describe("listing and sessions", () => {
  it("parses a listing with and without an attached listing row", () => {
    const withListing = parseListing({ ticker: "CEZ", status: "confirmed", score: "0.98", listing: { id: 4, mic: "XPRA", ticker: "CEZ", currency: "CZK", isin: "CZ0005112300" }, provider_symbols: [{ provider: "stooq", identifier: "cez.cz", match_status: "confirmed" }] });
    expect(withListing.ok && withListing.data).toMatchObject({ score: 0.98, listing: { mic: "XPRA" }, providerSymbols: [{ provider: "stooq" }] });
    const none = parseListing({ ticker: "CEZ", status: "suggested", listing: null, provider_symbols: [] });
    expect(none.ok && none.data.listing).toBeNull();
    expect(parseListing({ status: "x" }).ok).toBe(false);
  });
  it("parses sessions and rejects unknown states", () => {
    const s = parseExchangeSessions([{ mic: "XPRA", session_date: "2026-10-01", state: "half_day", source: "x", confirmed: true, reason: null }]);
    expect(s.ok && s.data[0]).toMatchObject({ state: "half_day", confirmed: true });
    expect(parseExchangeSessions([{ mic: "XPRA", session_date: "2026-10-01", state: "weird" }]).ok).toBe(false);
  });
  it("parses expected sessions and rejects a bad day", () => {
    const e = parseExpectedSessions({ mic: "XPRA", start: "2026-10-01", end: "2026-10-02", count: 2, basis: "confirmed_calendar", confirmed_days: 2, days: ["2026-10-01", "2026-10-02"] });
    expect(e.ok && e.data.days).toHaveLength(2);
    expect(parseExpectedSessions({ mic: "XPRA", start: "2026-10-01", end: "2026-10-02", count: 1, basis: "b", days: ["nope"] }).ok).toBe(false);
  });
});
