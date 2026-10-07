import { describe, expect, it } from "vitest";

import { breakReasons, choices, figure, gateWords, lanesOf, lineWarnings, revisionPairs, SERIES, statisticOf, unitLabel, visibleAt } from "@/lib/disclosure/estimates";
import { sampleEstimates } from "@/lib/disclosure/fixtures-estimates";

const at = (iso: string) => Date.parse(iso);
const view = (iso: string) => sampleEstimates(at(iso));

describe("lanes", () => {
  it("keeps five series apart and in a fixed order", () => {
    const v = view("2026-08-20T23:59:59Z");
    expect(Object.keys(v.lanes)).toEqual([...SERIES]);
    expect(v.lanes.company_guidance.every((l) => l.series === "company_guidance")).toBe(true);
    expect(v.lanes.company_guidance[0].mean).toBeNull();
    expect(v.lanes.named_individual_estimate).toHaveLength(0);
    expect(v.coverage.some((c) => c.kind === "named_estimates" && /never derived/.test(c.detail))).toBe(true);
  });
  it("never offers an empty guidance lane as zero", () => {
    expect(figure(null, "amount")).toBe("not stated");
    expect(figure("0", "amount")).toBe("0.00");
  });
});

describe("comparable snapshots", () => {
  const v = view("2026-08-01T00:00:00Z");
  const pairs = v.pairs.filter((p) => p.metric === "Revenue" && p.fiscal_period === "FY2026Q3" && !p.segment);
  it("breaks at a panel replacement and gives no number", () => {
    const b = pairs.find((p) => !p.comparable);
    expect(b?.break_reasons).toEqual(["panel_method_changed"]);
    expect(b?.change).toBeNull();
    expect(b?.change_pct).toBeNull();
    expect(b?.direction).toBeNull();
  });
  it("computes a revision between compatible snapshots", () => {
    const p = pairs.find((x) => x.earlier_id === 3 && x.later_id === 4);
    expect(p?.comparable).toBe(true);
    expect(Number(p?.change)).toBeCloseTo(9.5);
    expect(p?.elapsed_days).toBe(44);
    expect(p?.direction).toBe("up");
  });
  it("says analyst count is not stated instead of inventing a change", () => {
    const eps = v.pairs.find((p) => p.metric === "Adjusted EPS");
    expect(eps?.comparable).toBe(false);
    expect(eps?.break_reasons).toContain("basis_changed");
    const a = v.lanes.published_analyst_consensus.find((l) => l.id === 3)!;
    const b = { ...a, id: 99, snapshot_id: 99, known_at: "2026-06-20T00:00:00Z", analyst_count: null };
    const p = revisionPairs([a, b])[0];
    expect(p.analyst_count_change).toBeNull();
    expect(p.analyst_count_note).toMatch(/not stated/);
  });
  it("types every break reason", () => {
    const a = v.lanes.published_analyst_consensus.find((l) => l.id === 4)!;
    expect(breakReasons(a, { ...a, currency: "EUR" })).toEqual(["currency_changed"]);
    expect(breakReasons(a, { ...a, unit: "per_share" })).toEqual(["unit_changed"]);
    expect(breakReasons(a, { ...a, period_end: "2026-07-04" })).toEqual(["period_redefined"]);
    expect(breakReasons(a, { ...a, basis: "adjusted" })).toEqual(["basis_changed"]);
    expect(breakReasons({ ...a, segment_taxonomy: "x" }, { ...a, segment_taxonomy: "y" })).toEqual(["restated"]);
  });
  it("percent is undefined when the earlier estimate is zero", () => {
    const a = { ...v.lanes.published_analyst_consensus.find((l) => l.id === 3)!, mean: "0" };
    const b = { ...a, id: 98, snapshot_id: 98, known_at: "2026-06-20T00:00:00Z", mean: "2" };
    const p = revisionPairs([a, b])[0];
    expect(p.change_pct).toBeNull();
    expect(p.pct_note).toMatch(/undefined/);
  });
});

describe("no lookahead", () => {
  it("replaces a file by its correction only once the correction is known", () => {
    const merged = new Map([...Object.values(view("2026-08-01T00:00:00Z").lanes).flat(), ...Object.values(view("2026-08-20T23:59:59Z").lanes).flat()].map((l) => [l.id, l]));
    const all = [...merged.values()];
    const ids = (iso: string) => visibleAt(all.filter((l) => l.series === "published_analyst_consensus" && l.metric === "Revenue" && l.fiscal_period === "FY2026Q3" && !l.segment), at(iso)).map((l) => l.id).sort((a, b) => a - b);
    expect(ids("2026-04-01T00:00:00Z")).toEqual([]);
    expect(ids("2026-08-01T00:00:00Z")).toEqual([1, 2, 3, 4]);
    expect(ids("2026-08-20T00:00:00Z")).toEqual([1, 2, 3, 104]);
  });
  it("scores the surprise with the file known before the release, never the later correction", () => {
    const e = view("2026-08-20T23:59:59Z").events[0];
    expect(e.status).toBe("scored");
    expect(e.estimate_value_id).toBe(4);
    expect(Number(e.surprise)).toBeCloseTo(11.5e6);
    expect(e.formula).toMatch(/strictly before/);
  });
  it("shows no surprise before the actual is loaded", () => {
    const e = view("2026-07-20T00:00:00Z").events[0];
    expect(e.status).toBe("actual_not_loaded");
    expect(e.surprise).toBeNull();
  });
  it("hides the estimate lanes entirely before the first file", () => {
    const v = view("2026-04-01T00:00:00Z");
    expect(SERIES.flatMap((s) => v.lanes[s])).toHaveLength(0);
    expect(v.pairs).toHaveLength(0);
  });
});

describe("segments", () => {
  const b = view("2026-08-20T23:59:59Z").segment_bridges[0];
  it("leaves a missing estimate missing and keeps eliminations flagged", () => {
    const services = b.lines.find((l) => l.segment === "Services")!;
    expect(services.status).toBe("estimate_missing");
    expect(services.estimate).toBeNull();
    expect(services.delta).toBeNull();
    expect(b.lines.find((l) => l.segment === "Eliminations")?.non_additive).toBe(true);
    expect(Number(b.additive_estimate_total)).toBeCloseTo(1260e6);
  });
});

describe("words", () => {
  it("prints units and the gate in plain language", () => {
    expect(unitLabel({ unit: "amount", currency: "USD", scale: 6 })).toBe("USD millions");
    expect(unitLabel({ unit: "amount", currency: null, scale: 0 })).toMatch(/not stated/);
    expect(gateWords(view("2026-08-20T23:59:59Z").revision_gate)).toBe("Revision alerts and screens are off: 12 of 20 reviewed pairs.");
  });
  it("falls back from mean to median to midpoint", () => {
    const l = view("2026-08-20T23:59:59Z").lanes.company_guidance[0];
    expect(statisticOf(l)).toEqual({ stat: "midpoint", value: 4975 });
  });
  it("warns about an undefined adjusted basis and an unstated count", () => {
    const l = lanesOf(Object.values(view("2026-08-01T00:00:00Z").lanes).flat()).published_analyst_consensus.find((x) => x.id === 9)!;
    expect(lineWarnings(l).join(" ")).toMatch(/analyst count not stated/);
    expect(choices([l]).map((c) => c.label)).toEqual(["Adjusted EPS, FY2026Q3"]);
  });
});
