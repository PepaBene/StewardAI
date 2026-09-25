import { describe, expect, it } from "vitest";

import {
  appendUnique,
  EMPTY_TRANSACTION_FILTERS,
  isoDate,
  parseTransactionFilters,
  periodPresets,
  transactionFiltersToSearch,
  transactionQuery,
} from "./transactions";

describe("transaction filters", () => {
  it("round-trips through the URL in a stable order", () => {
    const filters = { types: ["dividend" as const, "order" as const], ticker: "AAPL_US_EQ", from: "2026-01-01", to: "2026-06-30" };
    const search = transactionFiltersToSearch(filters);
    expect(search).toBe("?type=order&type=dividend&ticker=AAPL_US_EQ&from=2026-01-01&to=2026-06-30");
    expect(parseTransactionFilters(new URLSearchParams(search))).toEqual({ ...filters, types: ["order", "dividend"] });
    expect(transactionFiltersToSearch(EMPTY_TRANSACTION_FILTERS)).toBe("");
  });

  it("ignores invalid values", () => {
    const parsed = parseTransactionFilters(new URLSearchParams("type=fee&type=cash&ticker=a%20b&from=2026-02-30&to=26-01-01"));
    expect(parsed).toEqual({ types: ["cash"], ticker: null, from: null, to: null });
    expect(isoDate("2024-02-29")).toBe("2024-02-29");
  });

  it("leaves empty filters out of the API query", () => {
    expect(transactionQuery(EMPTY_TRANSACTION_FILTERS, null, 100)).toEqual({
      type: undefined,
      ticker: undefined,
      from: undefined,
      to: undefined,
      cursor: undefined,
      limit: 100,
    });
    expect(transactionQuery({ ...EMPTY_TRANSACTION_FILTERS, types: ["dividend"] }, "abc", 50)).toMatchObject({ type: ["dividend"], cursor: "abc" });
  });
});

describe("paging", () => {
  it("never shows a row twice", () => {
    const first = [{ id: "a" }, { id: "b" }];
    const next = [{ id: "b" }, { id: "c" }, { id: "c" }];
    expect(appendUnique(first, next).map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("offers calendar periods", () => {
    const presets = periodPresets(new Date("2026-09-25T12:00:00Z"));
    expect(presets.map((p) => [p.id, p.from, p.to])).toEqual([
      ["ytd", "2026-01-01", "2026-09-25"],
      ["last-year", "2025-01-01", "2025-12-31"],
      ["12m", "2025-09-26", "2026-09-25"],
      ["all", null, null],
    ]);
  });
});
