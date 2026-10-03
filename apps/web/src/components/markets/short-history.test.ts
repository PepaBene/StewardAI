import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PriceHistory } from "@/lib/markets/types";

import { PriceChartPanel } from "./price-chart-panel";

const bar = (date: string, close: number) => ({ date, open: close, high: close, low: close, close, volume: null });
const prices = (bars: ReturnType<typeof bar>[]): PriceHistory =>
  ({
    ticker: "ABC",
    symbol: "abc",
    source: "Stooq",
    stooq_symbol: null,
    yahoo_symbol: null,
    fallback_reason: null,
    as_of: bars[bars.length - 1]?.date ?? null,
    fetched_at: null,
    bars,
    benchmark_symbol: null,
    benchmark: [],
    risk: null,
    correlations: [],
    gap: null,
    plausibility: null,
  }) as unknown as PriceHistory;
const html = (p: PriceHistory) => renderToStaticMarkup(createElement(PriceChartPanel, { ticker: "ABC", name: null, prices: p, diagnostics: null, actions: [] }));

describe("price chart with a short history", () => {
  it("says a one-bar series has one stored close, not that no bars are available (regression)", () => {
    const out = html(prices([bar("2026-10-03", 1812.4)]));
    expect(out).toContain("Only one stored close so far");
    expect(out).not.toContain("No priced bars are available");
    expect(out).toContain("Short history: 1 stored session");
    expect(out).toContain("no gap is backfilled or interpolated");
  });
  it("labels a few-day series as short and names its source and first date", () => {
    const out = html(prices([bar("2026-10-01", 100), bar("2026-10-02", 101), bar("2026-10-03", 102)]));
    expect(out).toContain("Short history: 3 stored sessions");
    expect(out).toContain("from Stooq");
  });
  it("shows no short-history note for a long series", () => {
    const many = Array.from({ length: 40 }, (_, i) => bar(`2026-09-${String((i % 28) + 1).padStart(2, "0")}`, 100 + i));
    expect(html(prices(many))).not.toContain("Short history");
  });
});
