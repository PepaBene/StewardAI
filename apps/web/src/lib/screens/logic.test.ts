import { describe, expect, it } from "vitest";

import { buildUniverse, evaluateEntity, readMetric, runScreen, summarize, thresholdValue } from "./logic";
import { metricDef } from "./metrics";
import type { Criterion, Entity, ScreenInputs, ValuationInput } from "./types";
import { ledgerGaps } from "./parse";
import { parseCriteria, parseScreenParams, screenQuery } from "./url";

function ok(over: Partial<Extract<ValuationInput, { kind: "ok" }>> = {}): ValuationInput {
  return { kind: "ok", ratios: {}, priceStale: false, epsStale: false, fundStale: null, bvpsStale: null, dpsStale: null, priceDate: "2026-10-01", checkedAt: null, sourceLabel: null, errors: [], hasAnyData: true, ...over };
}

function inputs(valuations: Record<string, ValuationInput>, extra: Partial<ScreenInputs> = {}): ScreenInputs {
  return { held: [], watchlist: [], prices: [], valuations: new Map(Object.entries(valuations)), ledger: new Map(), ...extra };
}

const ent = (ticker: string, held = true): Entity => ({ ticker, name: null, held, watchlist: !held });
const peLte20: Criterion = { metric: "pe", op: "lte", value: "20" };

describe("universe", () => {
  it("merges held and watchlist into one entity per ticker", () => {
    const u = buildUniverse([{ ticker: "AAA", name: "A", weight: 0.1, pplPct: 0 }], [{ ticker: "aaa" }, { ticker: "BBB" }]);
    expect(u.map((e) => [e.ticker, e.held, e.watchlist])).toEqual([["AAA", true, true], ["BBB", false, true]]);
  });
});

describe("readMetric", () => {
  const pe = metricDef("pe")!;
  it("never turns a missing value into zero", () => {
    const r = readMetric(ent("A"), pe, inputs({ A: ok({ ratios: { pe: { value: null, reason: "n/m: negative earnings" } } }) }));
    expect(r).toMatchObject({ status: "unknown", reason: "missing", detail: "n/m: negative earnings" });
  });
  it("keeps a real zero as a value", () => {
    expect(readMetric(ent("A"), pe, inputs({ A: ok({ ratios: { pe: { value: 0, reason: null } } }) }))).toEqual({ status: "value", value: 0 });
  });
  it("flags stale values and keeps them for display only", () => {
    const r = readMetric(ent("A"), pe, inputs({ A: ok({ priceStale: true, ratios: { pe: { value: 12, reason: null } } }) }));
    expect(r).toMatchObject({ status: "unknown", reason: "stale", staleValue: 12 });
  });
  it("does not call P/B stale because only the EPS is stale", () => {
    const r = readMetric(ent("A"), metricDef("pb")!, inputs({ A: ok({ epsStale: true, ratios: { pb: { value: 3, reason: null } } }) }));
    expect(r).toEqual({ status: "value", value: 3 });
  });
  it("reports not_covered for a 404 and for an empty response", () => {
    expect(readMetric(ent("A"), pe, inputs({ A: { kind: "not_found" } }))).toMatchObject({ reason: "not_covered" });
    expect(readMetric(ent("A"), pe, inputs({ A: ok({ hasAnyData: false }) }))).toMatchObject({ reason: "not_covered" });
  });
  it("uses the coverage ledger for paid_only", () => {
    const ledger = new Map([["A", [{ provider: "fmp", dataset: "profile", rawState: "paid_only", reason: null }]]]);
    expect(readMetric(ent("A"), pe, inputs({ A: { kind: "not_found" } }, { ledger }))).toMatchObject({ reason: "paid_only" });
    expect(readMetric(ent("A"), pe, inputs({ A: ok({ ratios: { pe: { value: null, reason: null } } }) }, { ledger }))).toMatchObject({ reason: "paid_only" });
  });
  it("detects a paid-tier source error", () => {
    const r = readMetric(ent("A"), pe, inputs({ A: ok({ ratios: { pe: { value: null, reason: null } }, errors: ["fmp: premium endpoint"] }) }));
    expect(r).toMatchObject({ reason: "paid_only" });
  });
  it("held-only metrics are not covered for watchlist entities", () => {
    expect(readMetric(ent("W", false), metricDef("weight")!, inputs({}))).toMatchObject({ status: "unknown", reason: "not_covered" });
  });
  it("reads weight and day change from the held and price responses", () => {
    const i = inputs({}, { held: [{ ticker: "A", name: null, weight: 0.25, pplPct: null }], prices: [{ ticker: "A", dayChangePct: -0.01, asOf: null }] });
    expect(readMetric(ent("A"), metricDef("weight")!, i)).toEqual({ status: "value", value: 0.25 });
    expect(readMetric(ent("A"), metricDef("ppl_pct")!, i)).toMatchObject({ reason: "missing" });
    expect(readMetric(ent("A"), metricDef("day_change_pct")!, i)).toEqual({ status: "value", value: -0.01 });
  });
});

describe("thresholds", () => {
  it("converts a percent entry to the API fraction and rejects non-numbers", () => {
    expect(thresholdValue({ metric: "dividend_yield", op: "gte", value: "3" })).toBeCloseTo(0.03);
    expect(thresholdValue({ metric: "pe", op: "lte", value: "20" })).toBe(20);
    expect(thresholdValue({ metric: "pe", op: "lte", value: "" })).toBeNull();
    expect(thresholdValue({ metric: "pe", op: "lte", value: "abc" })).toBeNull();
  });
});

describe("evaluateEntity", () => {
  const i = inputs({
    P: ok({ ratios: { pe: { value: 15, reason: null }, pb: { value: 1, reason: null } } }),
    F: ok({ ratios: { pe: { value: 30, reason: null }, pb: { value: null, reason: "no book value" } } }),
    U: ok({ ratios: { pe: { value: 10, reason: null }, pb: { value: null, reason: "no book value" } } }),
  });
  const pbLte2: Criterion = { metric: "pb", op: "lte", value: "2" };
  it("matches only on known passing values; boundary is inclusive", () => {
    expect(evaluateEntity(ent("P"), [peLte20, pbLte2], i, "exclude").kind).toBe("matched");
    const edge = inputs({ E: ok({ ratios: { pe: { value: 20, reason: null } } }) });
    expect(evaluateEntity(ent("E"), [peLte20], edge, "exclude").kind).toBe("matched");
  });
  it("a definite failure outranks an unknown", () => {
    expect(evaluateEntity(ent("F"), [peLte20, pbLte2], i, "exclude").kind).toBe("failed");
  });
  it("an unknown is excluded or shown as unknown by the treatment, never matched", () => {
    expect(evaluateEntity(ent("U"), [peLte20, pbLte2], i, "exclude").kind).toBe("excluded");
    expect(evaluateEntity(ent("U"), [peLte20, pbLte2], i, "unknown").kind).toBe("unknown");
  });
  it("ignores incomplete criteria and is pending while valuations load", () => {
    expect(evaluateEntity(ent("P"), [{ metric: "pe", op: "lte", value: "" }], i, "exclude").results).toHaveLength(0);
    expect(evaluateEntity(ent("Z"), [peLte20], i, "exclude").kind).toBe("pending");
    expect(evaluateEntity(ent("Z"), [peLte20], inputs({ Z: { kind: "loading" } }), "exclude").kind).toBe("pending");
  });
  it("a failed request is excluded as unavailable, not matched", () => {
    const o = evaluateEntity(ent("X"), [peLte20], inputs({ X: { kind: "error", message: "HTTP 500" } }), "exclude");
    expect(o.kind).toBe("excluded");
    expect(o.reasons).toEqual(["unavailable"]);
  });
});

describe("summarize", () => {
  it("counts universe, matched, failed and excluded with reasons", () => {
    const i = inputs({
      A: ok({ ratios: { pe: { value: 10, reason: null } } }),
      B: ok({ ratios: { pe: { value: 50, reason: null } } }),
      C: ok({ ratios: { pe: { value: null, reason: "x" } } }),
      D: { kind: "not_found" },
    });
    const outcomes = runScreen([ent("A"), ent("B"), ent("C"), ent("D")], [peLte20], i, "exclude");
    const s = summarize(outcomes);
    expect(s).toMatchObject({ universe: 4, matched: 1, failed: 1, excluded: 2, unknown: 0, pending: 0 });
    expect(s.byReason).toMatchObject({ missing: 1, not_covered: 1 });
    expect(summarize(runScreen([ent("C")], [peLte20], i, "unknown"))).toMatchObject({ excluded: 0, unknown: 1 });
  });
});

describe("url state", () => {
  it("round-trips criteria, treatment, scope and show", () => {
    const state = { criteria: [peLte20, { metric: "roe" as const, op: "gte" as const, value: "12.5" }], missing: "unknown" as const, scope: "held" as const, show: "excluded" as const };
    const q = screenQuery(state);
    expect(q).toBe("c=pe:lte:20,roe:gte:12.5&missing=unknown&scope=held&show=excluded");
    expect(parseScreenParams(new URLSearchParams(q))).toEqual(state);
  });
  it("drops malformed parts and defaults to exclude", () => {
    expect(parseCriteria("pe:lte:20,bogus:gte:1,pe:eq:3,roe")).toEqual([peLte20]);
    expect(parseScreenParams(new URLSearchParams("missing=zero&scope=x"))).toMatchObject({ missing: "exclude", scope: "all", show: "all" });
    expect(screenQuery({ criteria: [], missing: "exclude", scope: "all", show: "all" })).toBe("");
  });
});

describe("fix pass: stale inputs, reasons, boundary, ledger aliases", () => {
  it("F1-08: a stale fund figure (ETF P/E, P/B, yield) never passes a criterion", () => {
    const stale = ok({ fundStale: true, ratios: { pe: { value: 14, reason: null }, pb: { value: 2, reason: null }, dividend_yield: { value: 0.02, reason: null } } });
    for (const id of ["pe", "pb", "dividend_yield"] as const) {
      expect(readMetric(ent("F"), metricDef(id)!, inputs({ F: stale }))).toMatchObject({ status: "unknown", reason: "stale" });
    }
    expect(evaluateEntity(ent("F"), [{ metric: "pe", op: "lte", value: "15" }], inputs({ F: stale }), "exclude").kind).toBe("excluded");
  });
  it("F1-08: stale book value per share stales P/B, stale dividend per share stales the yield", () => {
    const v = ok({ bvpsStale: true, dpsStale: true, ratios: { pb: { value: 3, reason: null }, dividend_yield: { value: 0.03, reason: null }, pe: { value: 9, reason: null } } });
    expect(readMetric(ent("A"), metricDef("pb")!, inputs({ A: v }))).toMatchObject({ reason: "stale", detail: expect.stringContaining("book value per share") });
    expect(readMetric(ent("A"), metricDef("dividend_yield")!, inputs({ A: v }))).toMatchObject({ reason: "stale" });
    expect(readMetric(ent("A"), metricDef("pe")!, inputs({ A: v }))).toEqual({ status: "value", value: 9 });
  });
  it("F1-10: the ratio's own reason beats an unrelated source's premium error", () => {
    const r = readMetric(ent("A"), metricDef("pe")!, inputs({ A: ok({ ratios: { pe: { value: null, reason: "n/m: no EPS in the figures" } }, errors: ["finnhub: 403 premium endpoint"] }) }));
    expect(r).toMatchObject({ reason: "missing", detail: "n/m: no EPS in the figures" });
  });
  it("F1-11: an exact boundary value passes at-most / at-least in percent space", () => {
    const yieldLte = { metric: "dividend_yield" as const, op: "lte" as const, value: "0.7" };
    const i = inputs({ A: ok({ ratios: { dividend_yield: { value: 0.007, reason: null } } }) });
    expect(evaluateEntity(ent("A"), [yieldLte], i, "exclude").kind).toBe("matched");
    const gte = { metric: "dividend_yield" as const, op: "gte" as const, value: "1.1" };
    expect(evaluateEntity(ent("A"), [gte], inputs({ A: ok({ ratios: { dividend_yield: { value: 0.011, reason: null } } }) }), "exclude").kind).toBe("matched");
  });
  it("F1-09: ledger gaps keyed by the price symbol are filed under the ticker", () => {
    const entries = [{ id: "x", provider: "stooq", dataset: "prices", entityKey: "aapl.us", entityKind: "security", state: "paid_only", rawState: "paid_only", reason: null }] as never;
    expect(ledgerGaps(entries).get("AAPL_US_EQ")).toBeUndefined();
    const filed = ledgerGaps(entries, new Map([["AAPL.US", "AAPL_US_EQ"]]));
    expect(filed.get("AAPL_US_EQ")).toHaveLength(1);
    const r = readMetric(ent("AAPL_US_EQ"), metricDef("pe")!, inputs({ AAPL_US_EQ: { kind: "not_found" } }, { ledger: filed }));
    expect(r).toMatchObject({ reason: "paid_only" });
  });
});
