import { describe, expect, it } from "vitest";

import { auditCounts, backtestBlocked, buildOverlapMatrix, latestAuditByFund, latestFactsheetVersions, ledgerCounts, ledgerProvenance, lagText } from "./funds";
import { parseAudit, parseFactsheet, toNum } from "./parse";
import type { CoverageLedger, FactsheetRow, FundOverlapReport, LedgerRow } from "./types";

function lrow(key: string, tier: LedgerRow["tier"], over: Partial<LedgerRow> = {}): LedgerRow {
  return { subject_kind: "fund", subject_key: key, label: key, tier, origin: "derived", proxy_fund_isin: null, total_return_available: null, historical_constituents: null, latest_holdings_as_of: null, reason: "r", source_url: null, checked_at: "2026-10-01T10:00:00Z", backtest_flags: [], usable_for_backtest: true, ...over };
}

describe("ledger", () => {
  const ledger: CoverageLedger = { entries: [lrow("a", "official_holdings"), lrow("b", "proxy_fund", { usable_for_backtest: false, backtest_flags: ["no historical constituents"] }), lrow("c", "unavailable")], tiers: {}, flagged_benchmarks: ["b"] };
  it("counts every tier including empty ones", () => {
    expect(ledgerCounts(ledger).map((c) => c.count)).toEqual([1, 0, 0, 1, 1]);
  });
  it("lists backtest-blocked rows", () => {
    expect(backtestBlocked(ledger).map((r) => r.subject_key)).toEqual(["b"]);
  });
  it("is partial when a subject has no constituent source and not_covered when empty", () => {
    expect(ledgerProvenance(ledger).state).toBe("partial");
    expect(ledgerProvenance(ledger).coverage).toEqual({ covered: 2, total: 3, unit: "funds and indices with a constituent source" });
    expect(ledgerProvenance({ entries: [], tiers: {}, flagged_benchmarks: [] }).state).toBe("not_covered");
  });
});

describe("overlap matrix", () => {
  const rep: FundOverlapReport = {
    as_of: "2026-10-03",
    notes: [],
    funds: [],
    pairs: [{ fund_a: "A", fund_b: "B", overlap: 0.4, shared_issuers: 100, as_of_a: "2026-09-01", as_of_b: "2026-08-20", same_date: false }],
  };
  it("is symmetric, blank on the diagonal and flags mixed dates", () => {
    const m = buildOverlapMatrix(rep);
    expect(m.cell("A", "B")?.overlap).toBe(0.4);
    expect(m.cell("B", "A")?.overlap).toBe(0.4);
    expect(m.cell("A", "A")).toBeNull();
    expect(m.cell("A", "C")).toBeNull();
    expect(m.mixedDates).toBe(true);
    expect(m.max?.overlap).toBe(0.4);
  });
  it("lagText never invents a lag", () => {
    expect(lagText(null)).toBe("lag unknown");
    expect(lagText(1)).toBe("1 day old");
    expect(lagText(12)).toBe("12 days old");
  });
});

describe("parseAudit", () => {
  const good = { fund_key: "cspx", provider: "ishares", as_of: "2026-09-30", fingerprint: "f", basis: "fund", rules: { cap: "x" }, pe: "27.1", pb: 5.2, index_pe: null, index_pb: null, verdict: "value_change", alert: true, detail: { changes: { pe: { from: 20, to: 27.1, relative: 0.35 } } }, observed_at: "2026-10-01T00:00:00Z" };
  it("parses numbers from strings and keeps null as null (not zero)", () => {
    const parsed = parseAudit([good])!;
    expect(parsed.rows[0]).toMatchObject({ pe: 27.1, pb: 5.2, indexPe: null, alert: true });
    expect(parsed.rows[0].changes).toEqual([{ metric: "pe", from: 20, to: 27.1, relative: 0.35 }]);
  });
  it("marks parser drift as needing review without an alert", () => {
    const row = parseAudit([{ ...good, verdict: "parser_drift", alert: false, detail: { needs_parser_review: true } }])!.rows[0];
    expect(row.needsParserReview).toBe(true);
    expect(row.alert).toBe(false);
  });
  it("rejects a non-array body and counts bad rows", () => {
    expect(parseAudit({ detail: "x" })).toBeNull();
    const parsed = parseAudit([good, { fund_key: "x" }, "no", { ...good, verdict: "weird" }])!;
    expect(parsed.rows).toHaveLength(1);
    expect(parsed.rejected).toBe(3);
  });
  it("latestAuditByFund and auditCounts", () => {
    const a = parseAudit([good, { ...good, as_of: "2026-08-01", verdict: "unchanged", alert: false }, { ...good, fund_key: "vwce", verdict: "parser_drift", alert: false }])!.rows;
    expect(latestAuditByFund(a).map((r) => [r.fundKey, r.asOf])).toEqual([["cspx", "2026-09-30"], ["vwce", "2026-09-30"]]);
    expect(auditCounts(a)).toEqual({ alerts: 1, drift: 1 });
  });
});

describe("parseFactsheet", () => {
  const good = { fund_isin: "IE00B5BMR087", as_of: "2026-09-30", source: "ishares_json", expense_ratio: "0.0007", total_assets: 1e10, reconciles: true, missing_fields: ["x"], field_names_verified: false, extraction_warnings: ["w"], fetched_at: "2026-10-01T00:00:00Z" };
  it("never defaults an absent expense ratio to zero and treats absent verification as unverified", () => {
    const rows = parseFactsheet([good, { ...good, expense_ratio: null, field_names_verified: undefined, as_of: "2026-08-31" }])!.rows;
    expect(rows[0].expenseRatio).toBe(0.0007);
    expect(rows[1].expenseRatio).toBeNull();
    expect(rows[1].fieldNamesVerified).toBe(false);
  });
  it("rejects a bad ISIN and a non-array body", () => {
    expect(parseFactsheet(null)).toBeNull();
    expect(parseFactsheet([{ ...good, fund_isin: "bad" }])!.rejected).toBe(1);
  });
  it("keeps the newest fetched version of each as-of/source", () => {
    const rows = parseFactsheet([good, { ...good, expense_ratio: 0.0008, fetched_at: "2026-10-02T00:00:00Z" }])!.rows as FactsheetRow[];
    const latest = latestFactsheetVersions(rows);
    expect(latest).toHaveLength(1);
    expect(latest[0].expenseRatio).toBe(0.0008);
  });
});

describe("toNum", () => {
  it("rejects blanks, NaN and non-numbers", () => {
    expect([toNum(""), toNum("  "), toNum(NaN), toNum(null), toNum({}), toNum("1.5"), toNum(2)]).toEqual([null, null, null, null, null, 1.5, 2]);
  });
});
