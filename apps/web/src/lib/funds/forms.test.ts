import { describe, expect, it } from "vitest";

import { emptyFactsheetDraft, emptyLedgerDraft, factsheetRequest, ledgerRequest, parseAudit, pinRequest, validateFactsheet, validateLedger, verdictCounts } from "./forms";

describe("ledger form", () => {
  it("requires a reason of 3+ characters and an ISIN for proxy tier", () => {
    const d = { ...emptyLedgerDraft(), subjectKey: "IE00B4L5Y983", label: "Core", reason: "ab", tier: "proxy_fund" as const };
    const e = validateLedger(d);
    expect(e.reason).toBeDefined();
    expect(e.proxyIsin).toBeDefined();
    expect(validateLedger({ ...d, reason: "abc", proxyIsin: "ie00b4l5y983" })).toEqual({});
  });

  it("maps unknown tri-state to null and uppercases the ISIN", () => {
    const req = ledgerRequest({ ...emptyLedgerDraft(), subjectKey: " X ", label: "L", reason: "why", proxyIsin: "ie00b4l5y983", totalReturn: "yes" });
    expect(req.total_return_available).toBe(true);
    expect(req.historical_constituents).toBeNull();
    expect(req.proxy_fund_isin).toBe("IE00B4L5Y983");
    expect(req.subject_key).toBe("X");
    expect(req.source_url).toBeNull();
  });

  it("rejects a non-http source", () => {
    expect(validateLedger({ ...emptyLedgerDraft(), subjectKey: "a", label: "b", reason: "abc", sourceUrl: "ftp://x" }).sourceUrl).toBeDefined();
  });
});

describe("factsheet form", () => {
  const base = emptyFactsheetDraft("2026-01-01");
  it("needs at least one figure", () => {
    expect(validateFactsheet(base, "2026-02-01").asOf).toBeDefined();
  });
  it("bounds the charge below 20 percent and converts to a fraction", () => {
    expect(validateFactsheet({ ...base, expensePct: "20" }, "2026-02-01").expensePct).toBeDefined();
    expect(validateFactsheet({ ...base, expensePct: "0.07" }, "2026-02-01")).toEqual({});
    expect(factsheetRequest({ ...base, expensePct: "0.07" }).expense_ratio).toBe(0.0007);
  });
  it("keeps blanks null rather than 0 and ties assets to a currency", () => {
    const req = factsheetRequest({ ...base, benchmark: "MSCI World" });
    expect(req.expense_ratio).toBeNull();
    expect(req.total_assets).toBeNull();
    expect(validateFactsheet({ ...base, totalAssets: "100" }, "2026-02-01").currency).toBeDefined();
  });
  it("rejects future dates", () => {
    expect(validateFactsheet({ ...base, benchmark: "x", asOf: "2027-01-01" }, "2026-02-01").asOf).toBeDefined();
  });
});

describe("pin and audit", () => {
  it("builds a date pin request", () => {
    expect(pinRequest("2026-03-01", " ")).toEqual({ ref_kind: "manual", ref_id: "look-through:2026-03-01", as_of: "2026-03-01", reason: null });
  });
  it("parses audit rows with missing figures as null", () => {
    const base = { provider: "p", as_of: "2026-09-30", fingerprint: "f", basis: "b", observed_at: "2026-10-01T00:00:00Z" };
    const rows = parseAudit([
      { ...base, fund_key: "A", verdict: "value_change", alert: true, pe: 21.5, pb: null },
      { ...base, fund_key: "B", verdict: "weird", alert: false },
    ]);
    expect(rows[0]).toMatchObject({ pe: 21.5, pb: null, alert: true });
    expect(rows[1].verdict).toBe("unknown");
    expect(verdictCounts(rows)).toEqual([{ id: "value_change", label: "Value changed", count: 1 }]);
  });
});
