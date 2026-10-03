import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { adaptCoverage } from "@/lib/source-coverage/parse";
import type { CoverageEntry, ZeroCostGate } from "@/lib/source-coverage/types";

import type { DataState } from "@/lib/data-state/states";

import { LEDGER_ROW_CAP, atlasFooterState, buildAtlas, costScore, drillEntries, filterMeasured, gapsFirst, globalReachScore, identityScore, priceHoldingState, priceScore, provenanceScore, returnWindowScore, summaryKeyLabel } from "./logic";
import { adaptMeasuredCoverage, adaptSummary } from "./parse";
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

type Schemas = components["schemas"];
const review = (over: Partial<Schemas["IdentityReview"]>): Schemas["IdentityReview"] => ({ snapshot_date: "2026-10-01", held_positions: 3, reviewed: 3, owner_reviewed: 2, auto_confirmed: 1, reviewed_share: 1, owner_reviewed_share: 2 / 3, unresolved_count: 0, unresolved: [], accounted_share: 1, target: 1, meets_target: true, ...over });
const price = (over: Partial<Schemas["PriceCoverage"]>): Schemas["PriceCoverage"] => ({ snapshot_date: "2026-10-01", as_of: "2026-10-03", base_currency: "EUR", denominator_base: 1000, usable_value_base: 600, unknown_value_base: 400, usable_share: 0.6, unknown_share: 0.4, target: 0.95, meets_target: false, max_stale_sessions: 3, holdings_total: 3, holdings_usable: 1, holdings_stale: 1, holdings_no_price: 1, holdings_basis_unknown: 0, holdings: [], ...over });
const reachRow = (over: Partial<Schemas["ReachRow"]>): Schemas["ReachRow"] => ({ exchange_mic: "XETR", country: null, asset_class: null, metric: "price", tested_symbols: 4, valid_records: 100, history_first: "2020-01-02", history_last: "2026-10-02", history_count: 100, freshest_known_at: "2026-10-02T20:00:00Z", coverage_states: { available: 4 }, ...over });
const reach = (over: Partial<Schemas["DataReach"]>): Schemas["DataReach"] => ({ universe_note: "Counts stored rows, never a share of a global universe.", as_of: "2026-10-03T10:00:00Z", held_tickers: 2, watchlist_tickers: 1, tested_symbols: 4, cells: [], by_exchange: [reachRow({}), reachRow({ exchange_mic: null, metric: "fundamentals" })], by_country: [reachRow({ exchange_mic: null, country: "DE" })], by_asset_class: [reachRow({ exchange_mic: null, asset_class: "security" })], ...over });

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

describe("measured coverage adapter (typed response)", () => {
  const good = { jurisdiction: "DE", domain: "macro", metric: "GDP", observations: 12, entities: null, unknown_values: 0, sources: 1, earliest_period: "2020-01-01", latest_period: "2026-01-01", newest_known_at: "2026-02-01T00:00:00Z" };
  it("keeps null entities as not applicable and empty dates as unknown", () => {
    expect(adaptMeasuredCoverage([good])[0]).toMatchObject({ entities: null, observations: 12, unknownValues: 0, earliestPeriod: "2020-01-01" });
    expect(adaptMeasuredCoverage([{ ...good, earliest_period: null, newest_known_at: "" }])[0]).toMatchObject({ earliestPeriod: null, newestKnownAt: null });
    expect(adaptMeasuredCoverage([])).toEqual([]);
  });
  it("filters by text", () => {
    const rows = adaptMeasuredCoverage([good, { ...good, jurisdiction: "FR" }]);
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
  it("identity comes from the backend's reviewed path share, judged strictly", () => {
    expect(identityScore(review({})).status).toBe("met");
    const part = identityScore(review({ reviewed: 2, owner_reviewed: 1, auto_confirmed: 1, reviewed_share: 2 / 3, owner_reviewed_share: 1 / 3, unresolved_count: 1, accounted_share: 1, meets_target: false }));
    expect(part.status).toBe("not_met");
    expect(part.measured).toMatch(/2 of 3 held positions/);
    expect(part.measured).toMatch(/1 confirmed by you, 1 by the matcher/);
    expect(part.detail).toMatch(/100.0% of positions are either reviewed or a visible unresolved exception/);
    // no positions: unknown, never a 0% or 100%
    expect(identityScore(review({ held_positions: 0, reviewed: 0, reviewed_share: null, accounted_share: null, meets_target: false })).status).toBe("unavailable");
    expect(identityScore(null).status).toBe("unavailable");
  });
  it("price coverage is the value-weighted share with its unknown bucket, not a holding count", () => {
    const p = priceScore(price({}));
    expect(p.status).toBe("not_met");
    expect(p.share).toBeCloseTo(0.6);
    expect(p.measured).toMatch(/60.0% of held market value \(600 of 1,000 EUR\)/);
    expect(p.measured).toMatch(/40.0% of held value \(400 EUR\) is the unknown bucket: 1 stale, 1 with no stored close, 0 with no recorded currency/);
    expect(p.measured).not.toMatch(/positions have a stored latest close/);
    expect(priceScore(price({ usable_share: 0.97, unknown_share: 0.03, meets_target: true })).status).toBe("met");
    expect(priceScore(price({ holdings_total: 0, usable_share: null, unknown_share: null })).status).toBe("unavailable");
    expect(priceScore(null).status).toBe("unavailable");
  });
  it("price holding states go through the nine-state vocabulary; no close is a gap, never a verified zero", () => {
    expect(priceHoldingState("usable")).toBe("verified");
    expect(priceHoldingState("stale")).toBe("stale");
    expect(priceHoldingState("no_price")).toBe("not_covered");
    expect(priceHoldingState("basis_unknown")).toBe("partial");
    expect(priceHoldingState("something_new")).toBe("unavailable");
  });
  it("provenance is measured per table, with empty tables and missing columns named", () => {
    const dim = (dimension: "source" | "as_of" | "known_at" | "method" | "access_state", column: string | null, present: number, rows: number) => ({ dimension, column, present, missing: rows - present, share: rows ? present / rows : null });
    const full = (table: string, rows: number) => ({ table, rows, dimensions: (["source", "as_of", "known_at", "method", "access_state"] as const).map((d) => dim(d, d, rows, rows)), complete_rows: rows, complete_share: rows ? 1 : null, target: 1, meets_target: rows > 0 });
    const noAccess = { ...full("peer_set", 5), dimensions: full("peer_set", 5).dimensions.map((d) => (d.dimension === "access_state" ? dim("access_state", null, 0, 5) : d)), complete_share: null, meets_target: false };
    const pv = { target: 1, meets_target: false, tables_total: 3, tables_meeting_target: 1, empty_tables: ["corporate_action"], tables: [full("price_bar", 10), noAccess, full("corporate_action", 0)] };
    const s = provenanceScore(pv);
    expect(s.status).toBe("not_met");
    expect(s.measured).toMatch(/1 of 2 non-empty fact tables/);
    expect(s.share).toBe(0.5);
    expect(s.detail).toMatch(/Empty tables \(not judged\): corporate_action/);
    expect(s.detail).toMatch(/no column for some dimension.*peer_set/);
    expect(provenanceScore({ ...pv, tables: [full("price_bar", 0)], empty_tables: ["price_bar"] }).status).toBe("unavailable");
    expect(provenanceScore(null).status).toBe("unavailable");
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
  it("global reach reports stored counts by exchange, country and class and never a global share", () => {
    expect(globalReachScore(null).status).toBe("unavailable");
    expect(globalReachScore(reach({ tested_symbols: 0 })).status).toBe("unavailable");
    const g = globalReachScore(reach({}));
    expect(g.status).toBe("reported");
    expect(g.measured).toBe("4 tested symbols (2 held, 1 on the watchlist) across 1 exchanges, 1 countries and 1 asset classes");
    expect(g.share).toBeNull();
    expect(g.detail).toMatch(/never a share of a global/);
    expect(g.source).toBe("GET /data/reach");
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
