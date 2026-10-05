import { describe, expect, it } from "vitest";

import type { MacroContext } from "./context";
import { change, contextView, formatUnitValue, parseExposureInputs, rangePosition, windowChange } from "./context";

const point = (o: Record<string, unknown>) => ({
  series_id: "CPI",
  source: "oecd",
  geography: "DE",
  value: "2.5",
  unit: "percent",
  period: "2026-08-01",
  release_at: null,
  known_at: "2026-09-10T00:00:00Z",
  source_url: "https://example.test/cpi",
  five_year_low: "0",
  five_year_high: "10",
  ten_year_low: null,
  ten_year_high: null,
  access_state: "available",
  ...o,
});

describe("macro context", () => {
  const ctx = {
    as_of: "2026-10-01T00:00:00Z",
    observations: [point({ geography: "US", series_id: "GDP" }), point({ previous_value: "2.0" }), point({ geography: "DE", series_id: "AAA", value: null })],
    coverage: [
      { provider: "oecd", dataset: "cpi", geography: "DE", state: "available", checked_at: "x", reason: null },
      { provider: "bis", dataset: "credit", geography: "DE", state: "paid_only", checked_at: "x", reason: "premium" },
    ],
    effects: [{ ticker: "T", issuer_id: 1, segment: "Cloud", basis: "user_assigned", weight: "0.4", geography: "DE", hs_code: null, unknown_weight: "0.6", related_series: ["CPI"], explanation: "x" }],
    comparisons: [],
    exposure_unknown: false,
  } as unknown as MacroContext;

  it("orders by geography and series, and keeps a missing value null", () => {
    const v = contextView(ctx);
    expect(v.cards.map((c) => `${c.geography}:${c.seriesId}`)).toEqual(["DE:AAA", "DE:CPI", "US:GDP"]);
    expect(v.cards[0].value).toBeNull();
    expect(v.cards[1].fiveYear).toMatchObject({ pos: 25 });
    expect(v.cards[1].tenYear).toBeNull();
    expect(change(v.cards[1])).toBeCloseTo(0.5);
    expect(change(v.cards[0])).toBeNull();
  });

  it("lists only coverage gaps and keeps known and unknown exposure apart", () => {
    const v = contextView(ctx);
    expect(v.gaps).toEqual([expect.objectContaining({ provider: "bis", state: "paid_only", reason: "premium" })]);
    expect(v.effects[0]).toMatchObject({ weight: 0.4, unknownWeight: 0.6, basis: "user_assigned" });
  });

  it("clamps the range marker and rejects a flat or unknown range", () => {
    expect(rangePosition(15, 0, 10)!.pos).toBe(100);
    expect(rangePosition(5, 5, 5)).toBeNull();
    expect(rangePosition(null, 0, 10)).toBeNull();
    expect(formatUnitValue(null, "percent")).toBe("No value");
    expect(formatUnitValue(2.5, "percent")).toBe("2.50%");
  });
});

describe("commodity and FX inputs", () => {
  const obs = { id: 1, provider: "av", dataset: "d", source_url: "https://x", unit: "usd/tonne", currency: "USD" };
  const payload = {
    ticker: "X",
    exposure_assumptions: [{ kind: "commodity_input", name: "Copper", share: 0.2 }, { kind: "revenue_currency", name: "USD" }],
    observations: [
      { ...obs, metric: "COPPER", period_end: "2026-09-01", value: 9000, known_at: "2026-09-02" },
      { ...obs, metric: "COPPER", period_end: "2026-08-01", value: 8000, known_at: "2026-08-02" },
      { ...obs, metric: "COPPER", period_end: "2026-08-01", value: 8100, known_at: "2026-08-05" },
      { ...obs, metric: "EURUSD", period_end: "2026-09-01", value: null, known_at: "2026-09-02" },
    ],
    limitations: ["Price series are contextual inputs."],
  };

  it("builds one ordered series per metric and drops value-less rows", () => {
    const r = parseExposureInputs(payload);
    expect(r.series).toHaveLength(1);
    expect(r.series[0].points).toEqual([{ date: "2026-08-01", value: 8100 }, { date: "2026-09-01", value: 9000 }]);
    expect(windowChange(r.series[0])).toBeCloseTo(900 / 8100);
    expect(r.assumptions[1].share).toBeNull();
    expect(r.limitations).toEqual(["Price series are contextual inputs."]);
  });
});
