import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildBudgets, buildMatrix, tickerSet } from "@/lib/source-coverage/logic";
import type { AccessRow, CoverageEntry } from "@/lib/source-coverage/types";

import { BudgetBurn } from "./budget-burn";
import { CoverageMatrix } from "./coverage-matrix";

const now = new Date("2026-10-03T12:00:00Z");

function entry(state: CoverageEntry["state"], key: string, over: Partial<CoverageEntry> = {}): CoverageEntry {
  return {
    id: `p:d:${key}`,
    provider: "fmp",
    dataset: "profile",
    entityKey: key,
    entityKind: "security",
    state,
    rawState: state,
    stale: null,
    staleAfterHours: null,
    staleReason: null,
    expectedPeriods: null,
    availablePeriods: null,
    coverageRatio: null,
    earliestPeriod: null,
    latestPeriod: null,
    ageDays: null,
    checkedAt: "2026-10-02T08:00:00Z",
    lastGoodObservationId: null,
    reason: state === "paid_only" ? "Premium endpoint" : null,
    ...over,
  };
}

function access(state: AccessRow["state"], over: Partial<AccessRow> = {}): AccessRow {
  return {
    id: "fmp/profile",
    provider: "fmp",
    endpoint: "profile",
    accountTier: "free",
    state,
    rawState: state,
    freshness: "recent",
    lastSuccessAt: null,
    lastSuccessAgeSeconds: null,
    staleAfterHours: 72,
    testedAt: null,
    retryAt: null,
    hardBlocked: false,
    detail: null,
    lastDenialAt: null,
    lastDenialState: null,
    lastDenialStatusCode: null,
    lastDenialDetail: null,
    quota: null,
    quotaPeriod: null,
    resetTimezone: null,
    requestWeight: null,
    quotaWindowStart: null,
    quotaUsed: null,
    quotaRemaining: null,
    quotaHeadroom: null,
    headroomReason: "no quota is recorded for this endpoint",
    ...over,
  };
}

const noop = async () => null;

describe("CoverageMatrix SSR", () => {
  it("renders every state without throwing and never draws a zero for a missing cell", () => {
    const states = ["verified", "stale", "partial", "not_covered", "paid_only", "quota_exhausted", "access_unverified", "disputed", "unavailable"] as const;
    const entries = states.map((s, i) => entry(s, `T${i}`, { lastGoodObservationId: s === "unavailable" ? 4 : null }));
    const groups = buildMatrix(entries, { held: tickerSet(["T0", "T8"]), watchlist: tickerSet(["T3"]) }, [access("paid_only", { hardBlocked: true, lastDenialAt: "2026-10-01T00:00:00Z", lastDenialState: "paid_only", lastDenialStatusCode: 403 })]);
    const html = renderToStaticMarkup(createElement(CoverageMatrix, { groups, now, failedJobs: [{ id: 5, job_type: "price_history", error: "FMP failed", params: {} }], onResetBlock: noop }));
    expect(html).toContain("Premium endpoint");
    expect(html).toContain("Refresh failing");
    expect(html).toContain("/jobs/5");
    expect(html).toContain("Reset block");
  });

  it("shows an empty cell as unknown, not zero", () => {
    const groups = buildMatrix([entry("verified", "A")], { held: tickerSet(["A"]), watchlist: tickerSet([]) }, []);
    const html = renderToStaticMarkup(createElement(CoverageMatrix, { groups, now, failedJobs: [], onResetBlock: noop }));
    expect(html).toContain("No ledger entry");
    expect(html).toContain("1 of 1 ledger entry available");
  });

  it("renders an empty ledger group and a provider without entries", () => {
    const groups = buildMatrix([], null, [access("access_unverified")]);
    const html = renderToStaticMarkup(createElement(CoverageMatrix, { groups, now, failedJobs: null, onResetBlock: noop, scopeUnavailable: true }));
    expect(html).toContain("unknown, not covered and not zero");
    expect(html).toContain("Access unverified");
  });

  it("offers no reset for an unblocked endpoint", () => {
    const groups = buildMatrix([entry("verified", "A")], null, [access("verified")]);
    const html = renderToStaticMarkup(createElement(CoverageMatrix, { groups, now, failedJobs: [], onResetBlock: noop }));
    expect(html).not.toContain("Reset block");
  });
});

describe("BudgetBurn SSR", () => {
  it("says no published cap, never an empty bar", () => {
    const html = renderToStaticMarkup(createElement(BudgetBurn, { budgets: buildBudgets([access("verified")], []) }));
    expect(html).toContain("No published cap");
    expect(html).not.toContain('role="meter"');
  });

  it("charts a capped endpoint and flags use above the allowance", () => {
    const budgets = buildBudgets([access("verified", { quota: 10, quotaPeriod: "day", quotaUsed: 12, quotaRemaining: 0, quotaHeadroom: 0, headroomReason: null })], []);
    const html = renderToStaticMarkup(createElement(BudgetBurn, { budgets }));
    expect(html).toContain('role="meter"');
    expect(html).toContain("exceeds the declared allowance");
  });

  it("renders with no budgets at all", () => {
    expect(renderToStaticMarkup(createElement(BudgetBurn, { budgets: [] }))).toContain("No endpoint publishes");
  });
});
