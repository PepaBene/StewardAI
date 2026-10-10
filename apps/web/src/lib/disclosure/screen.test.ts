import { describe, expect, it } from "vitest";

import { SAMPLE_LOOK, SAMPLE_UNIVERSE } from "@/lib/disclosure/fixtures-analytics";
import { activeCount, lookRows, parseForm, runScreen, summary, weightPct } from "@/lib/disclosure/screen";

describe("screen form", () => {
  it("treats a blank box as no criterion and a bad number as an error, never zero", () => {
    expect(parseForm({ min_cash_conversion: "  " }).query).toEqual({});
    const bad = parseForm({ min_cash_conversion: "abc", min_discretionary_buys: "0", min_customer_concentration: "1.5", insider_window_days: "999" });
    expect(bad.errors).toHaveLength(4);
    expect(bad.query).toEqual({});
  });
  it("builds the query from valid values and the flag", () => {
    const p = parseForm({ min_cash_conversion: "1", min_discretionary_buys: "2", deal_exposure: true, insider_window_days: "30" });
    expect(p.errors).toEqual([]);
    expect(p.query).toEqual({ min_cash_conversion: "1", min_discretionary_buys: 2, deal_exposure: true, insider_window_days: 30 });
    expect(activeCount(p.query)).toBe(3);
  });
});

describe("sample screen", () => {
  it("excludes missing data and failures separately", () => {
    const r = runScreen(SAMPLE_UNIVERSE, { min_cash_conversion: "1" });
    expect(r.matched).toEqual(["SAMPLEA"]);
    expect(r.failed).toEqual(["SAMPLEB"]);
    expect(r.missing).toEqual(["SAMPLEC", "SAMPLEUK"]);
    expect(r.universe).toBe(4);
  });
  it("does not admit a holding with no value for a zero bar", () => {
    const r = runScreen(SAMPLE_UNIVERSE, { min_discretionary_buys: 1 });
    expect(r.matched).toEqual(["SAMPLEA"]);
    expect(r.missing).toContain("SAMPLEC");
  });
  it("uses the absolute size for a change, and a flag needs a known deal", () => {
    expect(runScreen(SAMPLE_UNIVERSE, { min_stake_change_pct: "3" }).matched).toEqual(["SAMPLEC"]);
    const d = runScreen(SAMPLE_UNIVERSE, { deal_exposure: true });
    expect(d.matched).toEqual(["SAMPLEB"]);
    expect(d.missing).toHaveLength(3);
  });
  it("matches nothing with no criteria and says to choose one", () => {
    const r = runScreen(SAMPLE_UNIVERSE, {});
    expect(r.matched).toEqual([]);
    expect(summary({ universe_size: 4, matched_count: 0, excluded_missing: 0, excluded_failed: 0, active_criteria: [] })).toContain("Choose a criterion");
    expect(summary({ universe_size: 4, matched_count: 1, excluded_missing: 2, excluded_failed: 1, active_criteria: ["min_cash_conversion"] })).toBe("1 of 4 holdings matched; 2 excluded for missing data, 1 excluded for failing a bar.");
  });
});

describe("look-through rows", () => {
  it("keeps the unknown weight beside the known and shows fund lag", () => {
    const rows = lookRows(SAMPLE_LOOK);
    expect(rows.find((r) => r.label.startsWith("Relationship share unknown"))!.holdings).toEqual(["SAMPLEB"]);
    expect(rows.find((r) => r.label.startsWith("Reached through held funds"))!.note).toContain("99 days");
    expect(weightPct("0.43")).toBe("43.0%");
    expect(weightPct(null)).toBe("unknown");
  });
});
