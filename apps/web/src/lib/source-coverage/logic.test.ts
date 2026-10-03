import { describe, expect, it } from "vitest";

import {
  buildBudgets,
  buildCell,
  buildMatrix,
  budgetTone,
  cellState,
  drillThroughHref,
  filterGroups,
  gateVerdict,
  matchFailedJob,
  overAllowance,
  scopeOf,
  stateCounts,
  tickerSet,
  usageSince,
  usedFraction,
} from "./logic";
import type { components } from "@/lib/api/schema";
import { adaptAccess, adaptCoverage, adaptUsage, parseZeroCostGate, staleBasisPhrase, toNumber } from "./parse";
import type { AccessRow, CoverageEntry, EntityScopes } from "./types";

// ---- small local builders -----------------------------------------------------------

function entry(over: Partial<CoverageEntry> & Pick<CoverageEntry, "provider" | "dataset" | "entityKey">): CoverageEntry {
  return {
    id: `${over.provider}:${over.dataset}:${over.entityKey}`,
    entityKind: "security",
    state: "verified",
    rawState: "available",
    stale: null,
    staleAfterHours: null,
    staleBasis: null,
    staleReason: null,
    expectedPeriods: null,
    availablePeriods: null,
    coverageRatio: null,
    earliestPeriod: null,
    latestPeriod: "2026-09-30",
    ageDays: 3,
    checkedAt: "2026-10-02T08:00:00Z",
    lastGoodObservationId: 1,
    reason: null,
    ...over,
  };
}

function access(over: Partial<AccessRow> & Pick<AccessRow, "provider" | "endpoint">): AccessRow {
  return {
    id: `${over.provider}/${over.endpoint}`,
    accountTier: "free",
    state: "verified",
    rawState: "available",
    freshness: "recent",
    lastSuccessAt: "2026-10-02T08:00:00Z",
    lastSuccessAgeSeconds: 3600,
    staleAfterHours: 72,
    testedAt: null,
    retryAt: null,
    hardBlocked: false,
    detail: null,
    lastDenialAt: null,
    lastDenialState: null,
    lastDenialStatusCode: null,
    lastDenialDetail: null,
    quota: 25,
    quotaPeriod: "day",
    resetTimezone: "UTC",
    requestWeight: 1,
    quotaWindowStart: "2026-10-03T00:00:00Z",
    quotaUsed: 5,
    quotaRemaining: 20,
    quotaHeadroom: 0.8,
    headroomReason: null,
    ...over,
  };
}

const scopes: EntityScopes = { held: tickerSet(["aapl_us_eq", "AAPL.US"]), watchlist: tickerSet(["MSFT_US_EQ"]) };

describe("held / watchlist classification", () => {
  it("matches security keys case-insensitively and puts held before watchlist", () => {
    expect(scopeOf({ entityKind: "security", entityKey: "AAPL_US_EQ" }, scopes)).toBe("held");
    expect(scopeOf({ entityKind: "security", entityKey: "aapl.us" }, scopes)).toBe("held");
    expect(scopeOf({ entityKind: "security", entityKey: "MSFT_US_EQ" }, scopes)).toBe("watchlist");
    expect(scopeOf({ entityKind: "security", entityKey: "ZZZ" }, scopes)).toBe("other");
    const both: EntityScopes = { held: tickerSet(["X"]), watchlist: tickerSet(["X"]) };
    expect(scopeOf({ entityKind: "security", entityKey: "X" }, both)).toBe("held");
  });

  it("never classifies geographies, and guesses nothing without the ticker sets", () => {
    expect(scopeOf({ entityKind: "geography", entityKey: "AAPL_US_EQ" }, scopes)).toBe("other");
    expect(scopeOf({ entityKind: "security", entityKey: "AAPL_US_EQ" }, null)).toBe("other");
  });

  it("ignores blank tickers", () => {
    expect([...tickerSet(["", " ", null, undefined, "a"])]).toEqual(["A"]);
  });
});

describe("cell state", () => {
  it("is null without entries: unknown, never zero", () => {
    expect(cellState([])).toBeNull();
    const c = buildCell("held", []);
    expect(c.state).toBeNull();
    expect(c.entries).toBe(0);
    expect(c.lastVerifiedAt).toBeNull();
    expect(c.maxAgeDays).toBeNull();
  });

  it("combines entries by precedence", () => {
    expect(cellState(["verified", "verified"])).toBe("verified");
    expect(cellState(["verified", "partial"])).toBe("partial");
    expect(cellState(["verified", "stale"])).toBe("stale");
    expect(cellState(["verified", "paid_only"])).toBe("partial");
    expect(cellState(["verified", "disputed", "paid_only"])).toBe("disputed");
    expect(cellState(["paid_only", "paid_only", "not_covered"])).toBe("paid_only");
  });

  it("breaks gap ties towards the more actionable state", () => {
    expect(cellState(["not_covered", "unavailable"])).toBe("unavailable");
    expect(cellState(["paid_only", "quota_exhausted"])).toBe("quota_exhausted");
  });

  it("flags a failing refresh only when a last-good observation exists", () => {
    const failing = entry({ provider: "p", dataset: "d", entityKey: "A", state: "unavailable", rawState: "temporarily_failed", lastGoodObservationId: 7 });
    const never = entry({ provider: "p", dataset: "d", entityKey: "B", state: "unavailable", rawState: "temporarily_failed", lastGoodObservationId: null });
    expect(buildCell("held", [failing]).refreshFailing).toBe(true);
    expect(buildCell("held", [never]).refreshFailing).toBe(false);
  });

  it("counts available entries, the stalest age, newest verification and top reasons", () => {
    const c = buildCell("held", [
      entry({ provider: "p", dataset: "d", entityKey: "A", ageDays: 2, checkedAt: "2026-10-01T00:00:00Z" }),
      entry({ provider: "p", dataset: "d", entityKey: "B", ageDays: 9, checkedAt: "2026-10-02T00:00:00Z" }),
      entry({ provider: "p", dataset: "d", entityKey: "C", state: "paid_only", rawState: "paid_only", reason: "premium", ageDays: null, latestPeriod: null, checkedAt: "2026-10-03T00:00:00Z" }),
    ]);
    expect(c.available).toBe(2);
    expect(c.entries).toBe(3);
    expect(c.maxAgeDays).toBe(9);
    expect(c.lastVerifiedAt).toBe("2026-10-02T00:00:00Z");
    expect(c.lastCheckedAt).toBe("2026-10-03T00:00:00Z");
    expect(c.reasons).toEqual(["premium"]);
    expect(c.gapKeys).toEqual(["C"]);
  });

  it("sums periods only over entries that state an expected count", () => {
    const c = buildCell("held", [
      entry({ provider: "p", dataset: "d", entityKey: "A", expectedPeriods: 10, availablePeriods: 8 }),
      entry({ provider: "p", dataset: "d", entityKey: "B" }),
    ]);
    expect(c.expectedPeriods).toBe(10);
    expect(c.availablePeriods).toBe(8);
    expect(buildCell("held", [entry({ provider: "p", dataset: "d", entityKey: "A" })]).expectedPeriods).toBeNull();
  });
});

describe("matrix", () => {
  const entries = [
    entry({ provider: "fmp", dataset: "profile", entityKey: "AAPL_US_EQ" }),
    entry({ provider: "fmp", dataset: "profile", entityKey: "MSFT_US_EQ", state: "paid_only", rawState: "paid_only" }),
    entry({ provider: "fmp", dataset: "profile", entityKey: "DE", entityKind: "geography" }),
    entry({ provider: "alpha_vantage", dataset: "overview", entityKey: "AAPL_US_EQ" }),
  ];
  const groups = buildMatrix(entries, scopes, [access({ provider: "finnhub", endpoint: "quote" }), access({ provider: "fmp", endpoint: "profile" })]);

  it("groups provider -> dataset -> scope cells, sorted by provider label", () => {
    expect(groups.map((g) => g.provider)).toEqual(["alpha_vantage", "finnhub", "fmp"]);
    const fmp = groups.find((g) => g.provider === "fmp")!;
    expect(fmp.rows).toHaveLength(1);
    expect(fmp.rows[0].cells.held.state).toBe("verified");
    expect(fmp.rows[0].cells.watchlist.state).toBe("paid_only");
    expect(fmp.rows[0].cells.other.entries).toBe(1);
    expect(fmp.access).toHaveLength(1);
  });

  it("keeps a provider that has an access row but no ledger entries", () => {
    const finnhub = groups.find((g) => g.provider === "finnhub")!;
    expect(finnhub.rows).toEqual([]);
    expect(finnhub.access).toHaveLength(1);
  });

  it("filters by provider, scope and state", () => {
    expect(filterGroups(groups, { provider: "fmp" })).toHaveLength(1);
    expect(filterGroups(groups, { state: "paid_only" }).map((g) => g.provider)).toEqual(["fmp"]);
    expect(filterGroups(groups, { scope: "watchlist", state: "verified" })).toEqual([]);
    expect(filterGroups(groups, { scope: "held" }).find((g) => g.provider === "fmp")!.rows).toHaveLength(1);
  });

  it("counts cell states in canonical order, ignoring empty cells", () => {
    const counts = stateCounts(groups);
    expect(counts.verified).toBe(3); // fmp held, fmp other, alpha held
    expect(counts.paid_only).toBe(1);
    expect(counts.disputed).toBe(0);
    expect(stateCounts(groups, "watchlist").paid_only).toBe(1);
  });
});

describe("budgets", () => {
  it("takes the cap from the access ledger and never invents one", () => {
    const [capped, uncapped, zero] = buildBudgets(
      [
        access({ provider: "alpha_vantage", endpoint: "overview" }),
        access({ provider: "ecb", endpoint: "data", quota: null, quotaPeriod: null, quotaUsed: null, quotaRemaining: null, quotaHeadroom: null, headroomReason: "no quota is recorded for this endpoint" }),
        access({ provider: "x", endpoint: "y", quota: 0 }),
      ],
      [],
    );
    expect(capped.limit).toBe(25);
    expect(capped.tone).toBe("ok");
    expect(uncapped.limit).toBeNull();
    expect(uncapped.used).toBeNull();
    expect(uncapped.tone).toBe("uncapped");
    expect(zero.limit).toBeNull();
  });

  it("separates exhausted, low, untracked and ok", () => {
    expect(budgetTone({ limit: 25, used: 25, remaining: 0, headroom: 0 })).toBe("exhausted");
    expect(budgetTone({ limit: 25, used: 23, remaining: 2, headroom: 0.08 })).toBe("low");
    expect(budgetTone({ limit: 25, used: null, remaining: null, headroom: null })).toBe("untracked");
    expect(budgetTone({ limit: 25, used: 1, remaining: 24, headroom: 0.96 })).toBe("ok");
  });

  it("builds history from usage windows of the cap's period only, oldest first, and finds the reset", () => {
    const a = access({ provider: "p", endpoint: "e", quotaWindowStart: "2026-10-03T00:00:00Z" });
    const usage = [
      { provider: "p", endpoint: "e", windowKind: "day", windowStart: "2026-10-03T00:00:00Z", windowEnd: "2026-10-04T00:00:00Z", credits: 5, requests: 5 },
      { provider: "p", endpoint: "e", windowKind: "day", windowStart: "2026-10-01T00:00:00Z", windowEnd: "2026-10-02T00:00:00Z", credits: 9, requests: 9 },
      { provider: "p", endpoint: "e", windowKind: "month", windowStart: "2026-10-01T00:00:00Z", windowEnd: "2026-11-01T00:00:00Z", credits: 99, requests: 99 },
      { provider: "other", endpoint: "e", windowKind: "day", windowStart: "2026-10-02T00:00:00Z", windowEnd: "2026-10-03T00:00:00Z", credits: 1, requests: 1 },
    ];
    const [b] = buildBudgets([a], usage);
    expect(b.history).toEqual([9, 5]);
    expect(b.resetsAt).toBe("2026-10-04T00:00:00Z");
    const [none] = buildBudgets([a], []);
    expect(none.resetsAt).toBeNull();
    expect(none.history).toEqual([]);
  });

  it("joins day and month credits from /sources/status", () => {
    const [b] = buildBudgets(
      [access({ provider: "p", endpoint: "e" })],
      [],
      [{ provider: "p", endpoint: "e", rawState: "available", state: "verified", hardBlocked: false, quota: 25, quotaPeriod: "day", lastSuccessAt: null, retryAt: null, dayCredits: 4, monthCredits: 40 }],
    );
    expect([b.spentDay, b.spentMonth]).toEqual([4, 40]);
  });

  it("flags recorded use above the declared allowance", () => {
    const budgets = buildBudgets([access({ provider: "p", endpoint: "e", quota: 10, quotaUsed: 12, quotaRemaining: 0, quotaHeadroom: 0 })], []);
    expect(overAllowance(budgets)).toHaveLength(1);
    expect(usedFraction(budgets[0])).toBeCloseTo(1.2);
    expect(overAllowance(buildBudgets([access({ provider: "p", endpoint: "e" })], []))).toEqual([]);
    expect(usedFraction({ limit: null, used: null })).toBeNull();
  });
});

describe("failed-job drill-through", () => {
  const jobs = [
    { id: 9, job_type: "news_ingest", error: "timeout", params: {} },
    { id: 8, job_type: "price_history", error: "Alpha Vantage returned HTTP 429", params: {} },
    { id: 7, job_type: "macro_collect", error: null, params: { provider: "ecb" } },
  ];

  it("matches the newest failed job naming the provider", () => {
    expect(matchFailedJob("alpha_vantage", jobs)?.id).toBe(8);
    expect(matchFailedJob("ecb", jobs)?.id).toBe(7);
    expect(matchFailedJob("fmp", jobs)).toBeNull();
  });

  it("falls back to the failed-jobs filter of the job log", () => {
    expect(drillThroughHref("alpha_vantage", jobs)).toEqual({ href: "/jobs/8", matched: jobs[1] });
    expect(drillThroughHref("fmp", jobs).href).toBe("/activity?status=failed#job-log");
  });
});

describe("adapters", () => {
  it("parses decimals sent as strings and rejects non-numbers", () => {
    expect(toNumber("12.5")).toBe(12.5);
    expect(toNumber("")).toBeNull();
    expect(toNumber("abc")).toBeNull();
    expect(toNumber(null)).toBeNull();
    expect(toNumber(Number.NaN)).toBeNull();
  });

  it("adapts coverage rows: available with a ratio below 1 is partial, temporarily_failed is unavailable", () => {
    const base = {
      provider: "fmp",
      dataset: "profile",
      entity_key: "AAPL_US_EQ",
      state: "available",
      expected_periods: 10,
      available_periods: 6,
      earliest_period: null,
      latest_period: "2026-09-30",
      last_good_observation_id: 3,
      checked_at: "2026-10-02T08:00:00Z",
      reason: null,
      entity_kind: "security" as const,
      coverage_ratio: "0.6",
      age_days: 3,
    };
    expect(adaptCoverage(base).state).toBe("partial");
    expect(adaptCoverage({ ...base, available_periods: 10, coverage_ratio: 1 }).state).toBe("verified");
    expect(adaptCoverage({ ...base, state: "temporarily_failed" }).state).toBe("unavailable");
    expect(adaptCoverage({ ...base, state: "vintage_unavailable" }).state).toBe("not_covered");
    const noPeriods = adaptCoverage({ ...base, expected_periods: null, available_periods: null, coverage_ratio: null });
    expect(noPeriods.state).toBe("verified");
    expect(noPeriods.coverageRatio).toBeNull();
    expect(adaptCoverage({ ...base, state: "mystery" }).state).toBe("unavailable");
  });

  it("derives Stale from the backend stale flag, with a reason; null stale stays unjudged", () => {
    const base = {
      provider: "fmp",
      dataset: "profile",
      entity_key: "AAPL_US_EQ",
      state: "available",
      expected_periods: 10,
      available_periods: 10,
      earliest_period: null,
      latest_period: "2026-09-30",
      last_good_observation_id: 3,
      reason: null,
      checked_at: "2026-10-01T00:00:00Z",
      entity_kind: "security" as const,
      coverage_ratio: 1,
      stale_after_hours: 48 as number | null,
      stale_basis: "job_schedule" as const,
    };
    const now = Date.parse("2026-10-04T00:00:00Z");
    const stale = adaptCoverage({ ...base, stale: true }, now);
    expect(stale.state).toBe("stale");
    expect(stale.staleReason).toBe("last checked 72 h ago, older than 48 h (twice the scheduled refresh gap)");
    expect(stale.staleAfterHours).toBe(48);
    expect(adaptCoverage({ ...base, stale: false }, now).state).toBe("verified");
    const unknown = adaptCoverage({ ...base, stale: null, stale_after_hours: null }, now);
    expect(unknown.state).toBe("verified");
    expect(unknown.stale).toBeNull();
    expect(adaptCoverage({ ...base }, now).stale).toBeNull();
    // a gap state is not hidden behind Stale
    expect(adaptCoverage({ ...base, state: "temporarily_failed", stale: true }, now).state).toBe("unavailable");
  });

  it("stale wording comes from the backend's stale_basis, not a fixed phrase", () => {
    const base = {
      provider: "macro_release_calendar",
      dataset: "fred",
      entity_key: "calendar",
      state: "available",
      expected_periods: null,
      available_periods: 3,
      earliest_period: null,
      latest_period: null,
      last_good_observation_id: null,
      reason: null,
      checked_at: "2026-10-01T00:00:00Z",
      entity_kind: "other" as const,
      stale: true,
      stale_after_hours: 48,
    };
    const now = Date.parse("2026-10-04T00:00:00Z");
    const job = adaptCoverage({ ...base, stale_basis: "job_schedule" }, now);
    expect(job.staleReason).toBe("last checked 72 h ago, older than 48 h (twice the scheduled refresh gap)");
    expect(job.staleBasis).toBe("job_schedule");
    const dflt = adaptCoverage({ ...base, stale_basis: "documented_default" }, now);
    expect(dflt.staleReason).toBe("last checked 72 h ago, older than 48 h (twice the documented default refresh interval)");
    expect(dflt.staleReason).not.toMatch(/scheduled refresh gap/);
    // no basis stated: the threshold is shown, but no explanation is invented
    const none = adaptCoverage({ ...base, stale_basis: null }, now);
    expect(none.staleReason).toBe("last checked 72 h ago, older than 48 h");
    expect(adaptCoverage({ ...base, stale_basis: "something_new" as never }, now).staleBasis).toBeNull();
    expect(staleBasisPhrase(null)).toBeNull();
  });

  it("adapts access rows and lets freshness override a stale or never-used available endpoint", () => {
    const raw: components["schemas"]["SourceAccess"] = {
      provider: "fmp",
      endpoint: "profile",
      account_tier: "free",
      state: "available",
      quota: "250",
      quota_period: "day",
      reset_timezone: "UTC",
      request_weight: "1",
      tested_at: null,
      retry_at: null,
      last_success_at: "2026-09-01T00:00:00Z",
      hard_blocked: false,
      detail: null,
      last_denial_at: null,
      last_denial_state: null,
      freshness: "stale",
      stale_after_hours: 72,
    };
    const a = adaptAccess(raw);
    expect(a.state).toBe("stale");
    expect(a.quota).toBe(250);
    expect(a.quotaUsed).toBeNull();
    expect(adaptAccess({ ...raw, freshness: "never_succeeded", last_success_at: null }).state).toBe("access_unverified");
    expect(adaptAccess({ ...raw, freshness: "recent" }).state).toBe("verified");
    expect(adaptAccess({ ...raw, state: "paid_only", freshness: "recent" }).state).toBe("paid_only");
    expect(adaptAccess({ ...raw, quota: null, quota_period: null }).quota).toBeNull();
  });

  it("adapts usage credits", () => {
    expect(adaptUsage({ provider: "p", endpoint: "e", window_kind: "day", window_start: "a", window_end: "b", credits: "3.5", requests: 2 }).credits).toBe(3.5);
  });
});

describe("zero-cost gate parser", () => {
  const ok = {
    passed: true,
    checked: 5,
    free_available: 3,
    disabled_ok: 1,
    gated_unverified: 1,
    violation_count: 0,
    warning_count: 1,
    warnings_by_code: { no_consumer: 1 },
    findings: [{ provider: "fmp", endpoint: "profile", severity: "warning", code: "no_consumer", message: "m" }],
  };

  it("parses a well-formed audit", () => {
    const g = parseZeroCostGate(ok);
    expect(g?.passed).toBe(true);
    expect(g?.warningsByCode).toEqual({ no_consumer: 1 });
    expect(g?.findings).toHaveLength(1);
  });

  it("rejects malformed bodies instead of reading them as a pass", () => {
    expect(parseZeroCostGate(null)).toBeNull();
    expect(parseZeroCostGate([])).toBeNull();
    expect(parseZeroCostGate({})).toBeNull();
    expect(parseZeroCostGate({ ...ok, passed: "yes" })).toBeNull();
    expect(parseZeroCostGate({ ...ok, checked: -1 })).toBeNull();
    expect(parseZeroCostGate({ ...ok, findings: [{ provider: "x" }] })).toBeNull();
    expect(parseZeroCostGate({ ...ok, findings: "none" })).toBeNull();
  });

  it("rejects a pass that lists violations", () => {
    const bad = { ...ok, findings: [{ provider: "a", endpoint: "b", severity: "violation", code: "c", message: "m" }], violation_count: 1 };
    expect(parseZeroCostGate(bad)).toBeNull();
    expect(parseZeroCostGate({ ...bad, passed: false })?.violationCount).toBe(1);
  });
});

describe("fix pass: zero-cost gate verdict and consistency", () => {
  const body = { passed: true, checked: 0, free_available: 0, disabled_ok: 0, gated_unverified: 0, violation_count: 0, warning_count: 0, warnings_by_code: {}, findings: [] };
  it("F1-01: a gate that checked zero endpoints is not verified, never passed", () => {
    const g = parseZeroCostGate(body)!;
    expect(g.passed).toBe(true);
    expect(gateVerdict(g)).toBe("not_verified");
    expect(gateVerdict({ ...g, checked: 4 })).toBe("passed");
    expect(gateVerdict({ ...g, checked: 4, passed: false, violationCount: 1 })).toBe("failed");
  });
  it("F1-02: passed with a violation count, or failed with none, is rejected", () => {
    expect(parseZeroCostGate({ ...body, checked: 3, violation_count: 3 })).toBeNull();
    expect(parseZeroCostGate({ ...body, checked: 3, passed: false, violation_count: 0 })).toBeNull();
    expect(parseZeroCostGate({ ...body, checked: 3, passed: false, violation_count: 1, findings: [] })?.violationCount).toBe(1);
  });
  it("F1-03: the usage fetch reaches back to the start of the month so a monthly window is not dropped", () => {
    const since = new Date(usageSince(new Date("2026-10-20T12:00:00Z")));
    expect(since.getTime()).toBeLessThanOrEqual(Date.UTC(2026, 9, 1));
    // early in the month the 15-day lookback is the earlier bound
    expect(new Date(usageSince(new Date("2026-10-03T12:00:00Z"))).toISOString().slice(0, 10)).toBe("2026-09-18");
  });
  it("F1-15: filterGroups says how many dataset rows the scope hid", () => {
    const entries = [entry({ provider: "fmp", dataset: "profile", entityKey: "OTHER1" })];
    const groups = buildMatrix(entries, { held: new Set(["AAA"]), watchlist: new Set() }, []);
    const [g] = filterGroups(groups, { scope: "held" });
    expect(g.rows).toHaveLength(0);
    expect(g.hiddenRows).toBe(1);
  });
});
