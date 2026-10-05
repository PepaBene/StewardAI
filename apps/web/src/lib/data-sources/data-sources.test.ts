import { describe, expect, it } from "vitest";

import { budgetRows, coverageMatrix, normalizeState, stateCounts, worstState, type DataCoverage, type SourceAccess } from "./data-sources";

const cov = (o: Partial<DataCoverage>): DataCoverage => ({
  provider: "eodhd",
  dataset: "eod_close",
  entity_key: "AAPL",
  state: "available",
  expected_periods: 10,
  available_periods: 10,
  earliest_period: null,
  latest_period: "2026-10-01",
  last_good_observation_id: null,
  checked_at: "2026-10-02T00:00:00Z",
  reason: null,
  entity_kind: "security",
  ...o,
});

const acc = (o: Partial<SourceAccess>): SourceAccess =>
  ({
    provider: "fmp",
    endpoint: "profile",
    account_tier: "free",
    state: "available",
    quota: 250,
    quota_period: "day",
    reset_timezone: null,
    request_weight: 1,
    tested_at: null,
    retry_at: null,
    last_success_at: null,
    hard_blocked: false,
    detail: null,
    last_denial_at: null,
    last_denial_state: null,
    quota_used: 50,
    freshness: "recent",
    ...o,
  }) as SourceAccess;

describe("coverage matrix", () => {
  it("shows the worst state of a cell so one gap is never averaged away", () => {
    const rows = coverageMatrix([cov({}), cov({ entity_key: "MSFT", state: "paid_only", available_periods: 0, reason: "premium" })]);
    expect(rows).toHaveLength(1);
    expect(rows[0].cell).toMatchObject({ state: "paid_only", entries: 2, available: 1, ratio: 0.5, reasons: ["premium"] });
  });

  it("keeps unknown ratios unknown instead of zero", () => {
    const [row] = coverageMatrix([cov({ expected_periods: null, available_periods: null })]);
    expect(row.cell.ratio).toBeNull();
  });

  it("orders states by severity and maps unknown states to missing", () => {
    expect(worstState(["available", "stale", "disputed"])).toBe("disputed");
    expect(worstState([])).toBe("available");
    expect(normalizeState("something_new")).toBe("missing");
    expect(stateCounts([cov({}), cov({ state: "stale" }), cov({ state: "zzz" })])).toMatchObject({ available: 1, stale: 1, missing: 1 });
  });
});

describe("budget rows", () => {
  it("sorts tracked quotas by burn and gives untracked ones a reason, not an empty bar", () => {
    const rows = budgetRows([
      acc({ endpoint: "a", quota_used: 10 }),
      acc({ endpoint: "b", quota_used: 250 }),
      acc({ endpoint: "c", quota: null, quota_used: null, headroom_reason: "no quota is recorded for this endpoint" }),
    ]);
    expect(rows.map((r) => r.endpoint)).toEqual(["b", "a", "c"]);
    expect(rows[0].burn).toBe(1);
    expect(rows[2]).toMatchObject({ burn: null, unknownReason: "no quota is recorded for this endpoint" });
  });
});
