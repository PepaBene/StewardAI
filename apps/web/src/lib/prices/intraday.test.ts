import { describe, expect, it } from "vitest";

import {
  dayMove,
  headerPrice,
  hourTicks,
  intradayOhlc,
  kindLabel,
  portfolioSpark,
  shortDay,
  stripTooltip,
  timeIn,
  type HoldingIntraday,
  type HoldingPrices,
  type IndexQuote,
  type PortfolioIntraday,
} from "./intraday";

const day = (iso: string) => `day ${iso}`;

function index(over: Partial<IndexQuote>): IndexQuote {
  return {
    symbol: "^spx",
    label: "S&P 500",
    yahoo_symbol: "^GSPC",
    last_close: 7743.41,
    as_of: "2026-09-25",
    as_of_time: null,
    exchange_tz: null,
    kind: "close",
    source: "Stooq",
    previous_close: 7704.13,
    previous_date: "2026-09-24",
    change_pct: 0.0051,
    ...over,
  };
}

function prices(over: Partial<HoldingPrices>): HoldingPrices {
  return {
    ticker: "VWCEd_EQ",
    name: "VWCE",
    symbol: "vwce.de",
    source: "Stooq",
    as_of: "2026-09-25",
    last_close: 169.74,
    previous_close: 169.5,
    day_change_pct: 0.0014,
    bars: [],
    intraday: null,
    ...over,
  };
}

const quote = {
  symbol: "VWCE.DE",
  source: "Yahoo Finance (unofficial)",
  kind: "intraday" as const,
  price: 169.64,
  price_time: "2026-09-28T08:46:12Z",
  time_local: "10:46",
  exchange_tz: "Europe/Berlin",
  session_date: "2026-09-28",
  previous_close: 169.74,
  change_pct: -0.00059,
  currency: "EUR",
  delay_note: "~15 min delayed",
  fetched_at: "2026-09-28T09:00:00Z",
};

describe("labels", () => {
  it("formats days and exchange times", () => {
    expect(shortDay("2026-09-25")).toBe("25.09");
    expect(timeIn("2026-09-28T08:46:12Z", "Europe/Berlin")).toBe("10:46");
    expect(timeIn("2026-09-25T20:00:01Z", "America/New_York")).toBe("16:00");
    expect(timeIn("not a date", "UTC")).toBe("");
    expect(kindLabel("intraday", "2026-09-28", "10:46")).toBe("intraday 10:46");
    expect(kindLabel("close", "2026-09-25", "16:00")).toBe("close of 25.09");
    expect(kindLabel(null, null, null)).toBe("");
  });

  it("says what a strip value is, when, and whose it is", () => {
    expect(stripTooltip(index({}))).toBe("S&P 500: close of 25.09 vs close of 24.09 · Stooq (^spx)");
    const live = index({
      symbol: "^stoxx",
      label: "STOXX Europe 600",
      yahoo_symbol: "^STOXX",
      kind: "intraday",
      source: "Yahoo",
      as_of: "2026-09-28",
      as_of_time: "2026-09-28T09:13:30Z",
      exchange_tz: "Europe/Zurich",
      previous_date: null,
    });
    expect(stripTooltip(live)).toBe(
      "STOXX Europe 600: intraday 11:13 (Zurich time) vs previous close · Yahoo Finance (unofficial, ^STOXX)",
    );
    const px = index({ symbol: "^px", label: "PX", yahoo_symbol: null, source: "Prague Stock Exchange", previous_date: null });
    expect(stripTooltip(px)).toBe("PX: close of 25.09 vs previous close · Prague Stock Exchange");
  });
});

describe("dayMove", () => {
  it("uses a fresher intraday quote, labelled", () => {
    const move = dayMove(prices({ intraday: quote }), day);
    expect(move).toEqual({
      value: -0.00059,
      kind: "intraday",
      stamp: "10:46",
      title: "Intraday: 10:46 exchange time, ~15 min delayed, vs previous close · Yahoo Finance (unofficial)",
    });
  });

  it("keeps the daily close when the intraday quote is not newer", () => {
    const stale = { ...quote, session_date: "2026-09-25", kind: "close" as const };
    const move = dayMove(prices({ intraday: stale, source: "Yahoo" }), day);
    expect(move).toEqual({ value: 0.0014, kind: "close", stamp: null, title: "Close-to-close, day 2026-09-25 (Yahoo)" });
    expect(dayMove(undefined, day)).toEqual({ value: null, kind: "none", stamp: null, title: "No stored closes" });
  });
});

describe("headerPrice", () => {
  const intraday: HoldingIntraday = {
    ticker: "MSFT_US_EQ",
    symbol: "MSFT",
    enabled: true,
    available: true,
    source: "Yahoo Finance (unofficial)",
    kind: "intraday",
    last_price: 516.17,
    last_trade_at: "2026-09-28T15:05:00Z",
    last_trade_time: "11:05",
    exchange_tz: "America/New_York",
    exchange_name: "NasdaqGS",
    currency: "USD",
    session_date: "2026-09-28",
    previous_close: 497.93,
    change: 18.24,
    change_pct: 0.0366,
    delay_note: "delayed up to ~15 min",
    fetched_at: "2026-09-28T15:20:00Z",
    bars: [],
    note: null,
    daily: null,
  };

  it("labels an intraday price with its exchange time, delay and source", () => {
    expect(headerPrice(intraday, "2026-09-25", day)).toEqual({
      price: 516.17,
      change: 18.24,
      pct: 0.0366,
      label: "11:05 exchange time · delayed up to ~15 min · Yahoo (unofficial)",
    });
  });

  it("labels a close and ignores an older session or no data", () => {
    expect(headerPrice({ ...intraday, kind: "close" }, "2026-09-25", day)?.label).toBe(
      "close of day 2026-09-28 · Yahoo (unofficial)",
    );
    // A daily close for the same day: the daily series stays the figure of record.
    expect(headerPrice({ ...intraday, kind: "close" }, "2026-09-28", day)).toBeNull();
    expect(headerPrice(intraday, "2026-09-28", day)).toBeNull();
    expect(headerPrice(intraday, null, day)?.price).toBe(516.17);
    expect(headerPrice({ ...intraday, session_date: "2026-09-24" }, "2026-09-25", day)).toBeNull();
    expect(headerPrice({ ...intraday, available: false }, null, day)).toBeNull();
    expect(headerPrice(null, null, day)).toBeNull();
  });
});

describe("chart helpers", () => {
  it("keys 5-minute bars by exchange time and ticks the full hours", () => {
    const times = ["09:30", "09:35", "09:55", "10:00", "10:05", "11:00", "12:00", "15:55", "16:00"];
    const bars = times.map((time, i) => ({ ts: `t${i}`, time, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }));
    expect(intradayOhlc(bars)[0]).toEqual({ date: "09:30", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 });
    expect(hourTicks(times)).toEqual([
      { index: 3, label: "10:00" },
      { index: 5, label: "11:00" },
      { index: 6, label: "12:00" },
      { index: 8, label: "16:00" },
    ]);
    expect(hourTicks(times, 2)).toEqual([
      { index: 3, label: "10:00" },
      { index: 6, label: "12:00" },
    ]);
  });

  it("gives the portfolio sparkline only with two or more points", () => {
    const base: PortfolioIntraday = {
      day: "2026-09-28",
      base_currency: "CZK",
      fx_source: "cnb",
      fx_rate_date: "2026-09-25",
      positions_as_of: "2026-09-27",
      source: "Yahoo Finance (unofficial)",
      enabled: true,
      points: [
        { ts: "2026-09-28T07:00:00Z", value: 100 },
        { ts: "2026-09-28T07:05:00Z", value: 101 },
      ],
      previous_value: 99,
      last_value: 101,
      change_pct: 0.02,
      coverage: { holdings: 12, covered: 5, covered_weight: 0.55, missing: [] },
      note: null,
    };
    expect(portfolioSpark(base)).toEqual({ values: [100, 101], caption: "Intraday 28.09 · 5 of 12 holdings · Yahoo (unofficial)" });
    expect(portfolioSpark({ ...base, points: base.points.slice(0, 1) })).toBeNull();
    expect(portfolioSpark({ ...base, day: null })).toBeNull();
    expect(portfolioSpark(null)).toBeNull();
  });
});
