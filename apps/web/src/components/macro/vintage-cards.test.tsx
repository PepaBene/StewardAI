// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { parseMacroContext } from "@/lib/macro/parse";

import { HoldingMacroPanel } from "./holding-macro-panel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const obs = (over: Record<string, unknown>) => ({
  series_id: "CPIAUCSL", source: "fred", geography: "US", value: "310", unit: "index", period: "2026-08-31", release_at: "2026-09-12T08:00:00Z", known_at: "2026-09-12T09:00:00Z", source_url: "https://fred.stlouisfed.org",
  five_year_low: "250", five_year_high: "320", ten_year_low: null, ten_year_high: null, access_state: "available", previous_period: "2026-07-31", previous_value: "309",
  history: [
    { period: "2026-06-30", value_latest: "308.0", value_first_release: "307.4", vintage_unavailable: false, released_at: null, revised_at: "2026-08-01T00:00:00Z", revision_count: 1 },
    { period: "2026-07-31", value_latest: "309.0", value_first_release: "308.8", vintage_unavailable: false, released_at: null, revised_at: null, revision_count: 0 },
  ],
  ...over,
});
const ctx = (observations: unknown[]) => {
  const r = parseMacroContext({ as_of: "2026-10-03T10:00:00Z", observations, coverage: [], effects: [{ ticker: "SPY", issuer_id: 1, segment: "s", basis: "reported", weight: "1", geography: "US", hs_code: null, unknown_weight: "0", related_series: [], explanation: "e", dimension: "geography" }], comparisons: [], exposure_unknown: false, series_effects: [] });
  if (!r.ok) throw new Error(r.reason);
  return r.value;
};

describe("macro cards with vintages", () => {
  it("renders one card for the series, with the first-release history as the FRED card's chart, and no duplicate keys", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { container } = render(<HoldingMacroPanel ctx={ctx([obs({}), obs({ source: "alfred" })])} ticker="SPY" />);
    expect(container.querySelectorAll('[data-series="CPIAUCSL"]')).toHaveLength(1);
    expect(container.querySelectorAll("[data-vintage-chart]")).toHaveLength(1);
    expect(errors.mock.calls.filter((c) => /same key/.test(String(c[0])))).toEqual([]);
  });
  it("shows a vintage-only series as one card with its history", () => {
    const { container } = render(<HoldingMacroPanel ctx={ctx([obs({ source: "alfred" })])} ticker="SPY" />);
    expect(container.querySelectorAll('[data-series="CPIAUCSL"]')).toHaveLength(1);
    expect(container.querySelectorAll("[data-vintage-chart]")).toHaveLength(1);
    expect(screen.getAllByText(/alfred/).length).toBeGreaterThan(0);
  });
});
