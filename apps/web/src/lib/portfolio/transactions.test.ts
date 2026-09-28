import { describe, expect, it } from "vitest";

import {
  activeTab,
  appendUnique,
  collectPages,
  csvFileName,
  EMPTY_TRANSACTION_FILTERS,
  isoDate,
  parseTransactionFilters,
  periodPresets,
  transactionFiltersToSearch,
  tabCounts,
  transactionQuery,
  transactionsCsv,
  withTab,
} from "./transactions";

describe("transaction filters", () => {
  it("round-trips through the URL in a stable order", () => {
    const filters = { types: ["dividend" as const, "order" as const], subtypes: [], ticker: "AAPL_US_EQ", from: "2026-01-01", to: "2026-06-30" };
    const search = transactionFiltersToSearch(filters);
    expect(search).toBe("?type=order&type=dividend&ticker=AAPL_US_EQ&from=2026-01-01&to=2026-06-30");
    expect(parseTransactionFilters(new URLSearchParams(search))).toEqual({ ...filters, types: ["order", "dividend"] });
    expect(transactionFiltersToSearch(EMPTY_TRANSACTION_FILTERS)).toBe("");
  });

  it("ignores invalid values", () => {
    const parsed = parseTransactionFilters(new URLSearchParams("type=fee&type=cash&ticker=a%20b&from=2026-02-30&to=26-01-01"));
    expect(parsed).toEqual({ types: ["cash"], subtypes: [], ticker: null, from: null, to: null });
    expect(isoDate("2024-02-29")).toBe("2024-02-29");
  });

  it("leaves empty filters out of the API query", () => {
    expect(transactionQuery(EMPTY_TRANSACTION_FILTERS, null, 100)).toEqual({
      type: undefined,
      subtype: undefined,
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

describe("subtype tabs", () => {
  it("round-trips subtypes and maps them to tabs", () => {
    const buys = withTab(EMPTY_TRANSACTION_FILTERS, "buys");
    const search = transactionFiltersToSearch({ ...buys, ticker: "NVDA_US_EQ" });
    expect(search).toBe("?type=order&subtype=BUY&ticker=NVDA_US_EQ");
    const parsed = parseTransactionFilters(new URLSearchParams(search));
    expect(activeTab(parsed)).toBe("buys");
    expect(transactionQuery(parsed, null, 10)).toMatchObject({ type: ["order"], subtype: ["BUY"] });
    expect(activeTab(EMPTY_TRANSACTION_FILTERS)).toBe("all");
    expect(activeTab({ ...EMPTY_TRANSACTION_FILTERS, types: ["order", "cash"] })).toBeNull();
    expect(parseTransactionFilters(new URLSearchParams("subtype=sell&subtype=bad!")).subtypes).toEqual(["SELL"]);
  });

  it("counts each tab from all-type totals", () => {
    const t = (type: "order" | "dividend" | "cash", subtype: string | null, count: number) => ({ type, subtype, currency: "CZK", count, amount: 0 });
    expect(
      tabCounts([t("order", "BUY", 5), t("order", "SELL", 2), t("dividend", "ORDINARY", 3), t("cash", "DEPOSIT", 4), t("cash", "INTEREST", 1)]),
    ).toEqual({ all: 15, buys: 5, sells: 2, dividends: 3, cash: 5 });
  });
});

describe("export", () => {
  it("follows the cursor and reports an early stop", async () => {
    const pages: Record<string, { items: { id: string }[]; next_cursor: string | null }> = {
      start: { items: [{ id: "a" }, { id: "b" }], next_cursor: "c1" },
      c1: { items: [{ id: "b" }, { id: "c" }], next_cursor: null },
    };
    const fetch = async (cursor: string | null) => pages[cursor ?? "start"];
    expect(await collectPages(fetch)).toEqual({ items: [{ id: "a" }, { id: "b" }, { id: "c" }], complete: true });
    expect((await collectPages(fetch, 1)).complete).toBe(false);
  });

  it("writes escaped CSV and neutralises formulas", () => {
    const csv = transactionsCsv([
      {
        id: "fill:1",
        type: "order",
        subtype: "BUY",
        ticker: "SAPd_EQ",
        name: 'SAP, "the" company',
        quantity: 2,
        price: 241.3,
        amount: -1000.5,
        currency: "CZK",
        occurred_at: "2026-09-01T10:00:00Z",
      },
      {
        id: "cash:2",
        type: "cash",
        subtype: "DEPOSIT",
        ticker: null,
        name: "=HYPERLINK()",
        quantity: null,
        price: null,
        amount: 60000,
        currency: "CZK",
        occurred_at: "2026-09-02T00:00:00Z",
      },
    ]);
    const lines = csv.split("\r\n");
    expect(lines[0]).toBe("occurred_at,type,subtype,ticker,name,quantity,price,amount,currency,id");
    expect(lines[1]).toBe('2026-09-01T10:00:00Z,order,BUY,SAPd_EQ,"SAP, ""the"" company",2,241.3,-1000.5,CZK,fill:1');
    expect(lines[2]).toBe("2026-09-02T00:00:00Z,cash,DEPOSIT,,'=HYPERLINK(),,,60000,CZK,cash:2");
    expect(lines[3]).toBe("");
    expect(csvFileName(withTab(EMPTY_TRANSACTION_FILTERS, "sells"), "2026-09-28")).toBe("transactions_sells_start_2026-09-28.csv");
  });
});
