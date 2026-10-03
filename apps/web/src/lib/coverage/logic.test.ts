import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { adaptCoverage } from "@/lib/source-coverage/parse";
import type { CoverageEntry, ZeroCostGate } from "@/lib/source-coverage/types";

import type { DataState } from "@/lib/data-state/states";

import { LEDGER_ROW_CAP, atlasFooterState, buildAtlas, costScore, drillEntries, filterMeasured, gapsFirst, globalReachScore, identityScore, priceScore, provenanceScore, returnWindowScore, summaryKeyLabel } from "./logic";
import { adaptSummary, parseMeasuredCoverage } from "./parse";
import { atlasQuery, DEFAULT_ATLAS_FILTERS, parseAtlasParams } from "./url";

function row(over: Partial<components["schemas"]["DataCoverage"]>): CoverageEntry {
  return adaptCoverage({
    provider: "fred",
    dataset: "GDP",
    entity_key: "DE",
    state: "available",
    expected_periods: null,
    available_periods: null,
    earliest_period: null,
    latest_period: null,
    last_good_observation_id: null,
    checked_at: "2026-10-01T00:00:00Z",
    reason: null,
    entity_kind: "geography",
    ...over,
  });
}

describe("atlas matrix", () => {
  const entries = [
    row({ dataset: "GDP", entity_key: "DE" }),
    row({ dataset: "GDP", entity_key: "FR", state: "paid_only", reason: "premium" }),
    row({ dataset: "close", entity_key: "AAPL", entity_kind: "security", provider: "stooq" }),
  ];
  it("counts ledger rows per dataset and kind, leaving empty cells empty", () => {
    const atlas = buildAtlas(entries);
    expect(atlas.map((r) => r.dataset)).toEqual(["close", "GDP"]);
    const gdp = atlas[1].cells;
    expect(gdp.geography).toMatchObject({ entries: 2, available: 1, state: "partial" });
    expect(gdp.security).toMatchObject({ entries: 0, state: null });
    expect(atlas[0].providers).toEqual(["stooq"]);
  });
  it("drills to the rows with gaps first", () => {
    expect(drillEntries(entries, "GDP", "geography").map((e) => e.entityKey)).toEqual(["FR", "DE"]);
    expect(gapsFirst(entries)[0].state).not.toBe("verified");
  });
  it("labels the null summary key as 'other kinds', not an entity", () => {
    expect(summaryKeyLabel("security", null)).toMatch(/Not a security/);
    expect(summaryKeyLabel("metric", "GDP")).toBe("GDP");
  });
});

describe("summary adapter", () => {
  it("keeps null ratios null and never invents a denominator", () => {
    const s = adaptSummary({
      group_by: "metric",
      total_entries: 2,
      note: "n",
      groups: [{ group_by: "metric", key: "GDP", entries: 2, by_state: { available: 1, paid_only: 1 }, expected_periods: null, available_periods: null, entries_with_expected: 0, available_entries: 1, coverage_ratio: null }],
    });
    expect(s.groups[0]).toMatchObject({ expectedPeriods: null, coverageRatio: null, byState: { available: 1, paid_only: 1 } });
  });
});

describe("measured coverage parser", () => {
  const good = { jurisdiction: "DE", domain: "macro", metric: "GDP", observations: 12, entities: null, unknown_values: 0, sources: 1, earliest_period: "2020-01-01", latest_period: "2026-01-01", newest_known_at: "2026-02-01T00:00:00Z" };
  it("keeps null entities as not applicable", () => {
    expect(parseMeasuredCoverage([good])?.[0]).toMatchObject({ entities: null, observations: 12 });
  });
  it("rejects a malformed body instead of returning zeros", () => {
    expect(parseMeasuredCoverage({})).toBeNull();
    expect(parseMeasuredCoverage([{ ...good, observations: "many" }])).toBeNull();
    expect(parseMeasuredCoverage([{ ...good, entities: -1 }])).toBeNull();
    expect(parseMeasuredCoverage([])).toEqual([]);
  });
  it("filters by text", () => {
    const rows = parseMeasuredCoverage([good, { ...good, jurisdiction: "FR" }])!;
    expect(filterMeasured(rows, "fr")).toHaveLength(1);
  });
});

describe("scorecard", () => {
  const ctx = (num: number, den: number) => ({
    snapshotDate: "2026-10-01",
    knownAsOf: "2026-10-02",
    baseCurrency: "EUR",
    positionCount: 3,
    identity: { numerator: num, denominator: den, ratio: den ? num / den : null, basis: "total portfolio value" },
    returnBases: [{ basis: "total_return", complete: { numerator: 1, denominator: 2, ratio: 0.5, basis: "total portfolio value" }, missingTickers: [], partialTickers: [] }],
    gaps: [],
  });
  it("identity is met only at 100% with no unresolved holding", () => {
    expect(identityScore(ctx(100, 100), 0).status).toBe("met");
    expect(identityScore(ctx(100, 100), 1).status).toBe("not_met");
    expect(identityScore(ctx(90, 100), 0).status).toBe("not_met");
    expect(identityScore(ctx(0, 0), 0).status).toBe("unavailable");
    expect(identityScore(null, null).status).toBe("unavailable");
  });
  it("states the missing endpoints instead of inventing figures", () => {
    expect(provenanceScore().status).toBe("not_measured");
    const p = priceScore({ withClose: 3, total: 4, newestAsOf: "2026-10-01" });
    expect(p.status).toBe("not_measured");
    expect(p.measured).toMatch(/3 of 4 held positions/);
    expect(priceScore({ withClose: 0, total: 0, newestAsOf: null }).status).toBe("unavailable");
  });
  it("reports return coverage per basis without a verdict", () => {
    expect(returnWindowScore(ctx(1, 1)).status).toBe("reported");
    expect(returnWindowScore(null).status).toBe("unavailable");
  });
  it("cost is judged by the gate, not assumed", () => {
    const gate: ZeroCostGate = { passed: true, checked: 5, freeAvailable: 3, disabledOk: 1, gatedUnverified: 1, violationCount: 0, warningCount: 0, warningsByCode: {}, findings: [] };
    expect(costScore(gate).status).toBe("met");
    expect(costScore({ ...gate, passed: false, violationCount: 1 }).status).toBe("not_met");
    expect(costScore(null).status).toBe("unavailable");
  });
  it("global reach counts only measured rows", () => {
    expect(globalReachScore(null).status).toBe("unavailable");
    expect(globalReachScore([]).status).toBe("unavailable");
    const rows = parseMeasuredCoverage([{ jurisdiction: "DE", domain: "macro", metric: "GDP", observations: 1, entities: null, unknown_values: 0, sources: 1 }])!;
    const g = globalReachScore(rows);
    expect(g.measured).toBe("1 jurisdictions and 1 metrics have stored observations");
    expect(g.source).toMatch(/exchange or asset class/);
  });
});

describe("atlas url state", () => {
  it("round-trips filters and the drill", () => {
    const filters = { ...DEFAULT_ATLAS_FILTERS, provider: "fred", dataset: "GDP", kind: "geography" as const, period: "2026-01-31", group: "source" as const };
    const q = atlasQuery(filters, { dataset: "GDP", kind: "geography" });
    expect(parseAtlasParams(new URLSearchParams(q))).toEqual({ filters, drill: { dataset: "GDP", kind: "geography" } });
  });
  it("drops invalid values", () => {
    const p = parseAtlasParams(new URLSearchParams("kind=fund&period=yesterday&group=zzz&drill=nope"));
    expect(p.filters).toEqual(DEFAULT_ATLAS_FILTERS);
    expect(p.drill).toBeNull();
  });
});

describe("fix pass: scorecard honesty", () => {
  const empty: ZeroCostGate = { passed: true, checked: 0, freeAvailable: 0, disabledOk: 0, gatedUnverified: 0, violationCount: 0, warningCount: 0, warningsByCode: {}, findings: [] };
  it("F1-01: an empty manifest reads as unavailable (not verified), never Target met", () => {
    const c = costScore(empty);
    expect(c.status).toBe("unavailable");
    expect(c.detail).toMatch(/not verified/i);
  });
  it("F1-13: a full identity share with the unresolved list unknown is unavailable, not Target not met", () => {
    const ctx = { snapshotDate: "2026-10-01", knownAsOf: "2026-10-02", baseCurrency: "EUR", positionCount: 1, identity: { numerator: 1, denominator: 1, ratio: 1, basis: "total portfolio value" }, returnBases: [], gaps: [] };
    const s = identityScore(ctx, null);
    expect(s.status).toBe("unavailable");
    expect(s.detail).toMatch(/value/);
  });
  it("F1-06: the atlas footer state follows the entries, and rows beyond the cap are counted", () => {
    const e = (state: DataState, i: number) => ({ id: String(i), state, entityKey: `K${i}`, dataset: "d", provider: "p" }) as never;
    expect(atlasFooterState([])).toBe("not_covered");
    expect(atlasFooterState([e("paid_only", 1), e("paid_only", 2)])).toBe("paid_only");
    expect(atlasFooterState([e("verified", 1), e("verified", 2)])).toBe("verified");
    expect(atlasFooterState([e("verified", 1), e("paid_only", 2)])).toBe("partial");
    const many = Array.from({ length: LEDGER_ROW_CAP + 5 }, (_, i) => e("verified", i));
    expect(gapsFirst(many)).toHaveLength(LEDGER_ROW_CAP);
  });
});

describe("summary request", () => {
  it("passes the selected entity kind to the summary endpoint", async () => {
    const { vi } = await import("vitest");
    const get = vi.fn();
    vi.doMock("@/lib/api/client", () => ({ getBrowserApiClient: () => ({ GET: get }) }));
    vi.doMock("@/lib/hooks/use-api-resource", () => ({ useApiResource: (f: () => unknown) => (f(), { state: { kind: "loading" }, reload: () => {} }) }));
    vi.doMock("react", async (orig) => ({ ...(await orig<typeof import("react")>()), useCallback: (f: unknown) => f, useMemo: (f: () => unknown) => f() }));
    const { useCoverageSummary } = await import("./hooks");
    useCoverageSummary("source", { provider: "", dataset: "", state: "", period: "", entity: "", kind: "geography" });
    expect(get).toHaveBeenCalledWith("/data/coverage/summary", { params: { query: expect.objectContaining({ entity_kind: "geography" }) } });
  });
});
