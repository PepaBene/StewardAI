import { describe, expect, it } from "vitest";

import { parseMacroContext } from "./parse";

const obs = {
  series_id: "CPIAUCSL", source: "fred", geography: "US", value: "310", unit: "index", period: "2026-08-31", release_at: "2026-09-12T08:00:00Z", known_at: "2026-09-12T09:00:00Z", source_url: "https://x.test",
  five_year_low: "250", five_year_high: "320", ten_year_low: null, ten_year_high: null, access_state: "available", previous_period: "2026-07-31", previous_value: "309",
};

describe("one card per series (ALFRED is the FRED card's history)", () => {
  const fredObs = { ...obs, history: [{ period: "2026-07-31", value_latest: "310.0", value_first_release: "309.4", vintage_unavailable: false, released_at: "2026-08-12T12:30:00Z", revised_at: "2026-09-12T12:30:00Z", revision_count: 1 }] };
  const alfredObs = { ...obs, source: "alfred", history: [{ period: "2026-06-30", value_latest: "308.0", value_first_release: "307.9", vintage_unavailable: false, released_at: null, revised_at: null, revision_count: 0 }] };
  const body = (observations: unknown[], extra: Record<string, unknown> = {}) => ({ as_of: "2026-10-03T10:00:00Z", observations, coverage: [], effects: [], comparisons: [], exposure_unknown: false, series_effects: [], ...extra });

  it("never shows a second card when a vintage row accompanies the FRED row (regression: two cards, conflicting dates)", () => {
    const r = parseMacroContext(body([fredObs, alfredObs]));
    expect(r.ok && r.value.observations.map((o) => o.source)).toEqual(["fred"]);
    expect(r.ok && r.value.observations[0].history.map((h) => h.valueFirstRelease)).toEqual([309.4]);
  });
  it("lends the vintage history to a FRED card that has none", () => {
    const r = parseMacroContext(body([{ ...fredObs, history: [] }, alfredObs]));
    expect(r.ok && r.value.observations).toHaveLength(1);
    expect(r.ok && r.value.observations[0].history.map((h) => h.period)).toEqual(["2026-06-30"]);
  });
  it("keeps a vintage-only series as its own card", () => {
    const r = parseMacroContext(body([alfredObs]));
    expect(r.ok && r.value.observations.map((o) => o.source)).toEqual(["alfred"]);
  });
  it("keeps another geography's vintage row and drops vintage duplicates in comparisons and series effects", () => {
    const cmp = { series_id: "CPIAUCSL", geography: "US", value: "1", unit: "percent", period: "2026-08-31", release_at: null, known_at: "2026-09-12T09:00:00Z", source_url: "u" };
    const eff = { series_id: "CPIAUCSL", unit: "percent", weighted_change: "0.1", covered_weight: "0.4", geographies: ["US"] };
    const r = parseMacroContext(body([fredObs, { ...alfredObs, geography: "DE" }], { comparisons: [{ ...cmp, source: "fred" }, { ...cmp, source: "alfred" }], series_effects: [{ ...eff, source: "fred" }, { ...eff, source: "alfred" }] }));
    expect(r.ok && r.value.observations.map((o) => `${o.source}:${o.geography}`)).toEqual(["fred:US", "alfred:DE"]);
    expect(r.ok && r.value.comparisons.map((c) => c.source)).toEqual(["fred"]);
    expect(r.ok && r.value.seriesEffects.map((c) => c.source)).toEqual(["fred"]);
  });
});
