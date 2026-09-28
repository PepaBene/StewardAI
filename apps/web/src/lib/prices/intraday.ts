/*
 * Intraday prices (Yahoo Finance, unofficial) and the market strip's source chain: labels,
 * the "is the intraday quote fresher than the daily close" rule, and chart helpers.
 *
 * Every label names its source and time: "14:35 exchange time · ~15 min delayed · Yahoo
 * (unofficial)", "close of 25.09". Nothing here estimates a figure; a missing quote is null.
 */

import type { components } from "@/lib/api/schema";
import type { OhlcBar } from "@/lib/charts/candles";
import type { TimeTick } from "@/lib/charts/time";

type Schemas = components["schemas"];
export type HoldingIntraday = Schemas["HoldingIntraday"];
export type IntradayQuote = Schemas["IntradayQuote"];
export type IndexQuote = Schemas["IndexQuote"];
export type HoldingPrices = Schemas["HoldingPrices"];
export type PortfolioIntraday = Schemas["PortfolioIntraday"];
export type IntradayBar = Schemas["IntradayBarOut"];

export const YAHOO_LABEL = "Yahoo (unofficial)";

/** "2026-09-25" -> "25.09" (the strip's compact day). */
export function shortDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}.${m[2]}` : iso;
}

/** "HH:MM" of an ISO timestamp in an IANA time zone (the exchange's), 24-hour. */
export function timeIn(iso: string, timeZone: string | null | undefined): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: timeZone ?? "UTC",
    }).format(d);
  } catch {
    return "";
  }
}

/** The short "intraday HH:MM" / "close of DD.MM" label of a quote. */
export function kindLabel(kind: "intraday" | "close" | null | undefined, day: string | null, time: string | null): string {
  if (kind === "intraday" && time) return `intraday ${time}`;
  if (day) return `close of ${shortDay(day)}`;
  return "";
}

/** The market strip item's tooltip: what the value is, when, and whose it is. */
export function stripTooltip(i: IndexQuote): string {
  const time = i.as_of_time ? timeIn(i.as_of_time, i.exchange_tz) : null;
  const what = kindLabel(i.kind ?? (i.as_of ? "close" : null), i.as_of, time);
  const zone = i.kind === "intraday" && i.exchange_tz ? ` (${i.exchange_tz.split("/").pop()?.replace("_", " ")} time)` : "";
  const source =
    i.source === "Yahoo"
      ? `Yahoo Finance (unofficial${i.yahoo_symbol ? `, ${i.yahoo_symbol}` : ""})`
      : i.source === "Stooq"
        ? `Stooq (${i.symbol})`
        : (i.source ?? "");
  const against =
    i.previous_close != null
      ? ` vs ${i.previous_date ? `close of ${shortDay(i.previous_date)}` : "previous close"}`
      : "";
  return [`${i.label}: ${what}${zone}${against}`, source].filter(Boolean).join(" · ");
}

/**
 * The day change a holdings row shows: the intraday one when its session is newer than the
 * latest daily close (labelled), else the close-to-close one.
 */
export type DayMove = {
  value: number | null;
  kind: "intraday" | "close" | "none";
  /** Shown next to the change for an intraday value: "14:35". */
  stamp: string | null;
  title: string;
};

export function dayMove(p: HoldingPrices | undefined, formatDay: (iso: string) => string): DayMove {
  const q = p?.intraday ?? null;
  const fresher = q != null && q.change_pct != null && (p?.as_of == null || q.session_date > p.as_of);
  if (q && fresher) {
    const label = q.kind === "intraday" ? `${q.time_local} exchange time, ${q.delay_note}` : `close of ${formatDay(q.session_date)}`;
    return {
      value: q.change_pct ?? null,
      kind: q.kind === "intraday" ? "intraday" : "close",
      stamp: q.kind === "intraday" ? q.time_local : shortDay(q.session_date),
      title: `Intraday: ${label}, vs previous close · Yahoo Finance (unofficial)`,
    };
  }
  if (p?.day_change_pct != null && p.as_of) {
    return {
      value: p.day_change_pct,
      kind: "close",
      stamp: null,
      title: `Close-to-close, ${formatDay(p.as_of)} (${p.source ?? "Stooq"})`,
    };
  }
  return { value: null, kind: "none", stamp: null, title: "No stored closes" };
}

/** The holding header's price: intraday when a quote is stored and its session is newer
 * than the latest daily close; else null (the page shows the daily close). */
export type HeaderPrice = {
  price: number;
  change: number | null;
  pct: number | null;
  /** "14:35 exchange time · ~15 min delayed · Yahoo (unofficial)" or "close of 25 Sep 2026 · Yahoo (unofficial)". */
  label: string;
};

export function headerPrice(
  intraday: HoldingIntraday | null | undefined,
  dailyAsOf: string | null,
  formatDay: (iso: string) => string,
): HeaderPrice | null {
  if (!intraday?.available || intraday.last_price == null || !intraday.session_date) return null;
  // A daily close for the same day (collected after that session) stays the figure of record.
  if (dailyAsOf && intraday.session_date <= dailyAsOf) return null;
  const label =
    intraday.kind === "intraday" && intraday.last_trade_time
      ? `${intraday.last_trade_time} exchange time · ${intraday.delay_note ?? "delayed"} · ${YAHOO_LABEL}`
      : `close of ${formatDay(intraday.session_date)} · ${YAHOO_LABEL}`;
  return { price: intraday.last_price, change: intraday.change ?? null, pct: intraday.change_pct ?? null, label };
}

/** 5-minute bars as the candle chart's bars, keyed by their exchange time. */
export function intradayOhlc(bars: readonly IntradayBar[]): OhlcBar[] {
  return bars.map((b) => ({ date: b.time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));
}

/** X-axis ticks on the full hours of a session ("10:00"), thinned to at most `maxTicks`. */
export function hourTicks(times: readonly string[], maxTicks = 8): TimeTick[] {
  const ticks: TimeTick[] = [];
  let lastHour: string | null = null;
  times.forEach((t, index) => {
    const hour = t.slice(0, 2);
    if (hour !== lastHour && t.endsWith(":00")) ticks.push({ index, label: t });
    if (/^\d{2}:\d{2}$/.test(t)) lastHour = hour;
  });
  if (ticks.length <= maxTicks) return ticks;
  const every = Math.ceil(ticks.length / maxTicks);
  return ticks.filter((_, i) => i % every === 0);
}

/** The portfolio's intraday path as sparkline values, and its one-line caption. */
export function portfolioSpark(p: PortfolioIntraday | null | undefined): { values: number[]; caption: string } | null {
  if (!p || !p.day || p.points.length < 2) return null;
  const { covered, holdings } = p.coverage;
  return {
    values: p.points.map((pt) => pt.value),
    caption: `Intraday ${shortDay(p.day)} · ${covered} of ${holdings} holdings · ${YAHOO_LABEL}`,
  };
}
