import { describe, expect, it } from "vitest";

import { buildRequest, cellFor, describeFilter, funnel, nextMetric, PRESETS, type FilterDraft, type ScreenResult, type ScreenRow } from "./screens";

const draft = (o: Partial<FilterDraft>): FilterDraft => ({ id: 1, metric: "gross_margin", op: "gte", value: "40", ...o });

describe("buildRequest", () => {
  it("converts typed percentages to fractions and leaves multiples as typed", () => {
    const { request, errors } = buildRequest({
      universe: "covered",
      drafts: [draft({ value: "15" }), draft({ id: 2, metric: "net_debt_to_equity", op: "lte", value: "1,5" }), draft({ id: 3, metric: "roic", value: "0.1" })],
      missing: "exclude",
      sortBy: null,
    });
    expect(errors).toEqual([]);
    expect(request!.filters!.map((f) => [f.metric, f.value])).toEqual([
      ["gross_margin", "0.15"],
      ["net_debt_to_equity", "1.5"],
      ["roic", "0.001"],
    ]);
    expect(request).toMatchObject({ universe: "covered", missing: "exclude", limit: 100, reconciled_only: false });
  });

  it("rejects blanks, non-numbers and duplicate metric and operator pairs, with the metric named", () => {
    const { request, errors } = buildRequest({
      universe: "watchlist",
      drafts: [draft({ value: "" }), draft({ id: 2, value: "abc", metric: "roic" }), draft({ id: 3 }), draft({ id: 4 })],
      missing: "keep_unknown",
      sortBy: "roic",
    });
    expect(request).toBeNull();
    expect(errors).toEqual(["Gross margin: enter a number.", "Return on invested capital: enter a number.", 'Gross margin: only one "at least" filter per metric.']);
  });

  it("allows both bounds of one metric and a screen with no filters", () => {
    expect(buildRequest({ universe: "holdings", drafts: [draft({}), draft({ id: 2, op: "lte", value: "60" })], missing: "exclude", sortBy: null }).errors).toEqual([]);
    expect(buildRequest({ universe: "holdings", drafts: [], missing: "exclude", sortBy: null }).request!.filters).toEqual([]);
  });

  it("starts a new filter on an unused metric and ships presets that build cleanly", () => {
    expect(nextMetric([draft({ metric: "revenue_growth" })])).toBe("gross_margin");
    for (const p of PRESETS) {
      const drafts = p.filters.map((f, i) => ({ ...f, id: i }));
      expect(buildRequest({ universe: "covered", drafts, missing: "exclude", sortBy: null }).errors).toEqual([]);
    }
  });
});

describe("results", () => {
  const result = {
    universe_size: 10,
    evaluated: 8,
    matched: 3,
    filtered_out: 4,
    no_data: 1,
    filters: [{ metric: "roic", op: "gte", value: "0.15" }],
  } as unknown as ScreenResult;

  it("accounts for every issuer in the universe", () => {
    const f = funnel(result);
    expect(f.map((s) => s.value)).toEqual([3, 4, 1, 2]);
    expect(f.reduce((s, x) => s + x.value, 0)).toBe(result.universe_size);
  });

  it("shows an unknown metric as Unknown and marks the filtered one", () => {
    const row = { ticker: "A", basis: "us-gaap", period_end: "2025-12-31", unknown: ["roic"], metrics: { roic: null, gross_margin: "0.456", net_debt_to_equity: 1.234 } } as unknown as ScreenRow;
    expect(cellFor(row, "roic", result)).toEqual({ text: "Unknown", unknown: true, filtered: true });
    expect(cellFor(row, "gross_margin", result)).toEqual({ text: "45.6%", unknown: false, filtered: false });
    expect(cellFor(row, "net_debt_to_equity", result).text).toBe("1.23×");
    expect(cellFor(row, "revenue_growth", result).unknown).toBe(true);
  });

  it("describes a filter in words", () => {
    expect(describeFilter({ metric: "gross_margin", op: "gte", value: "0.4" } as never)).toBe("Gross margin at least 40%");
    expect(describeFilter({ metric: "net_debt_to_equity", op: "lte", value: "1.5" } as never)).toBe("Net debt to equity at most 1.5×");
  });
});
