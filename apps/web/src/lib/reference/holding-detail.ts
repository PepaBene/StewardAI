/*
 * Derivations for the holding page's overview panels: price change, the candle ranges,
 * risk figures from daily closes (volatility mirrors the backend's steward/reference/risk.py
 * so a holding and the portfolio are measured alike), KPI headroom for the thesis radar,
 * the verdicts-and-news timeline and the report countdown. Nothing is extrapolated: missing
 * input yields null and the panel says so.
 */

import { maxDrawdown } from "@/lib/charts/series";

export type Close = { date: string; close: number };

// ---------------------------------------------------------------------------- price

/** Chart ranges of the candle panel (daily bars only, so no intraday "1D"). */
export const CANDLE_RANGES = [
  { id: "1w", label: "1W", days: 7 },
  { id: "1m", label: "1M", days: 31 },
  { id: "3m", label: "3M", days: 92 },
  { id: "1y", label: "1Y", days: 366 },
  { id: "5y", label: "5Y", days: 1827 },
] as const;
export type CandleRangeId = (typeof CANDLE_RANGES)[number]["id"];
export const DEFAULT_CANDLE_RANGE: CandleRangeId = "3m";
/** The page reads this many days once; every range up to 1Y is a slice of it. */
export const BASE_PRICE_DAYS = 400;

export function candleRangeDays(id: CandleRangeId): number {
  return CANDLE_RANGES.find((r) => r.id === id)?.days ?? 92;
}

function shiftDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Bars dated after `asOf − days` (the latest bar's date when `asOf` is omitted). */
export function barsInRange<T extends { date: string }>(bars: readonly T[], days: number, asOf?: string | null): T[] {
  const end = asOf ?? bars[bars.length - 1]?.date;
  if (!end) return [];
  const start = shiftDays(end, days);
  return bars.filter((b) => b.date > start && b.date <= end);
}

/** The latest close against the one before it. */
export function lastCloseChange(
  bars: readonly Close[],
): { date: string; close: number; previous: number; change: number; pct: number } | null {
  if (bars.length < 2) return null;
  const last = bars[bars.length - 1];
  const prev = bars[bars.length - 2];
  if (!(prev.close > 0)) return null;
  return { date: last.date, close: last.close, previous: prev.close, change: last.close - prev.close, pct: last.close / prev.close - 1 };
}

// ---------------------------------------------------------------------------- risk

export const TRADING_DAYS = 252;

/**
 * Annualised volatility: sample standard deviation of daily log returns over the last
 * `window` returns, × √252. Null with fewer than 20 returns (as the backend).
 */
export function annualisedVolatility(closes: readonly number[], window = TRADING_DAYS): number | null {
  const tail = closes.slice(-(window + 1)).filter((c) => c > 0);
  const logs: number[] = [];
  for (let i = 1; i < tail.length; i++) logs.push(Math.log(tail[i] / tail[i - 1]));
  if (logs.length < 20) return null;
  const mean = logs.reduce((s, x) => s + x, 0) / logs.length;
  const variance = logs.reduce((s, x) => s + (x - mean) ** 2, 0) / (logs.length - 1);
  return Math.sqrt(variance) * Math.sqrt(TRADING_DAYS);
}

/**
 * One-day historical value at risk: the (1 − confidence) quantile of the last `window`
 * daily simple returns, by nearest rank, as a (negative) fraction. Null with fewer than
 * 20 returns.
 */
export function historicalVar(closes: readonly number[], confidence = 0.95, window = TRADING_DAYS): number | null {
  const tail = closes.slice(-(window + 1)).filter((c) => c > 0);
  const returns: number[] = [];
  for (let i = 1; i < tail.length; i++) returns.push(tail[i] / tail[i - 1] - 1);
  if (returns.length < 20) return null;
  returns.sort((a, b) => a - b);
  // The epsilon keeps 0.05 × 40 at rank 2, not 3 (1 − 0.95 is not exactly 0.05 in binary).
  const rank = Math.max(1, Math.ceil((1 - confidence) * returns.length - 1e-9));
  return returns[rank - 1];
}

/** How far the last close sits below the highest close of the trailing year. */
export function fromYearHigh(bars: readonly Close[]): { high: number; highDate: string; last: number; drawdown: number } | null {
  const year = barsInRange(bars, 365);
  if (year.length < 2) return null;
  let top = year[0];
  for (const b of year) if (b.close > top.close) top = b;
  const last = year[year.length - 1].close;
  return { high: top.close, highDate: top.date, last, drawdown: last / top.close - 1 };
}

/** Worst peak-to-trough fall within the trailing year, with the trough's date. */
export function yearMaxDrawdown(bars: readonly Close[]): { value: number; date: string } | null {
  const year = barsInRange(bars, 365);
  const dd = maxDrawdown(year.map((b) => b.close));
  if (!dd || year.length < 2) return null;
  return { value: dd.value, date: year[dd.index].date };
}

// ---------------------------------------------------------------------------- thesis

type Operator = "<" | "<=" | ">" | ">=" | "==";

/**
 * KPI headroom as a ratio on the passing side of its threshold: 1 is exactly at the
 * threshold, above 1 inside the limit, below 1 outside it. Floors (≥, >) take observed ÷
 * threshold, ceilings (≤, <) threshold ÷ observed, "==" 1 − |observed − threshold| ÷
 * |threshold|. Null when it cannot be measured this way (no observation, a threshold of 0,
 * a floor against a non-positive threshold); a ceiling with a non-positive observation is
 * as far inside as the chart draws (2).
 */
export function kpiHeadroom(operator: string, threshold: number, observed: number | null | undefined): number | null {
  if (typeof observed !== "number" || !Number.isFinite(observed) || !Number.isFinite(threshold)) return null;
  switch (operator as Operator) {
    case ">=":
    case ">":
      return threshold > 0 ? Math.max(0, observed / threshold) : null;
    case "<=":
    case "<":
      if (!(threshold > 0)) return null;
      return observed <= 0 ? 2 : threshold / observed;
    case "==":
      return threshold !== 0 ? Math.max(0, 1 - Math.abs(observed - threshold) / Math.abs(threshold)) : null;
    default:
      return null;
  }
}

/** Radar position of a headroom ratio: the threshold sits on the middle ring (0.5), 2× on the rim. */
export function radarValue(headroom: number): number {
  return Math.min(1, Math.max(0, headroom / 2));
}

export const RADAR_NOTE =
  "Each spoke is a KPI's headroom: the latest observed value against its threshold, on the passing side. The middle ring is the threshold itself; the rim is twice the threshold for a floor (or half of it for a ceiling). Inside the middle ring is a breach.";

type VerdictLike = { period: string | null; observed_value: number | null; status: string };
type KpiLike = {
  kpi: { id: string; metric: string; operator: string; threshold: number };
  current: VerdictLike | null;
  history: VerdictLike[];
};

/**
 * Radar series for the thesis panel: one axis per KPI whose latest verdict has a
 * measurable headroom (needs three), and the previous verdict per KPI as a comparison when
 * every axis has one.
 */
export function thesisRadar(
  kpis: readonly KpiLike[],
  axisLabel: (metric: string) => string,
): { axes: string[]; now: number[]; previous: number[] | null; previousLabel: string | null } | null {
  const usable = kpis
    .map((k) => {
      const now = k.current ? kpiHeadroom(k.kpi.operator, k.kpi.threshold, k.current.observed_value) : null;
      const prevVerdict = k.history.find((v) => v !== k.current && v.period !== k.current?.period) ?? null;
      const prev = prevVerdict ? kpiHeadroom(k.kpi.operator, k.kpi.threshold, prevVerdict.observed_value) : null;
      return { k, now, prev, prevPeriod: prevVerdict?.period ?? null };
    })
    .filter((u): u is typeof u & { now: number } => u.now !== null);
  if (usable.length < 3) return null;
  const withPrev = usable.every((u) => u.prev !== null);
  const periods = [...new Set(usable.map((u) => u.prevPeriod))];
  return {
    axes: usable.map((u) => axisLabel(u.k.kpi.metric)),
    now: usable.map((u) => radarValue(u.now)),
    previous: withPrev ? usable.map((u) => radarValue(u.prev as number)) : null,
    previousLabel: withPrev ? (periods.length === 1 && periods[0] ? `${periods[0]} verdict` : "Previous verdicts") : null,
  };
}

/**
 * The KPI bar: observed value on a track from 0 (or the lowest value, when negative) to
 * 125% of the larger of observed and threshold, the threshold as a tick; plus which side
 * of the limit the observation sits on.
 */
export function kpiBar(
  operator: string,
  threshold: number,
  observed: number | null | undefined,
): { min: number; max: number; value: number | null; target: number; side: "inside" | "below floor" | "above ceiling" | "off target" | null } {
  const has = typeof observed === "number" && Number.isFinite(observed);
  const hi = Math.max(threshold, has ? observed : threshold, 0);
  const lo = Math.min(threshold, has ? observed : threshold, 0);
  const max = hi === 0 && lo === 0 ? 1 : hi * 1.25;
  const min = lo < 0 ? lo * 1.25 : 0;
  let side: "inside" | "below floor" | "above ceiling" | "off target" | null = null;
  if (has) {
    const v = observed as number;
    const ok =
      operator === ">=" ? v >= threshold : operator === ">" ? v > threshold : operator === "<=" ? v <= threshold : operator === "<" ? v < threshold : v === threshold;
    side = ok ? "inside" : operator.startsWith(">") ? "below floor" : operator.startsWith("<") ? "above ceiling" : "off target";
  }
  return { min, max, value: has ? (observed as number) : null, target: threshold, side };
}

/** "FY2026 Q2" -> "Q2·26"; anything else is returned as is. */
export function shortPeriod(period: string | null | undefined): string {
  if (!period) return "—";
  const m = /^FY(\d{4})\s*(Q[1-4]|H[12])$/.exec(period.trim());
  if (m) return `${m[2]}·${m[1].slice(2)}`;
  const y = /^FY(\d{4})$/.exec(period.trim());
  return y ? `FY${y[1].slice(2)}` : period;
}

/**
 * The thesis score against the previous point of its history: the last point when it
 * differs from the current score, otherwise the one before it.
 */
export function scoreChange(
  score: number | null | undefined,
  history: readonly { date: string; score: number }[],
): { prior: number; date: string; direction: "up" | "down" | "same" } | null {
  if (typeof score !== "number" || history.length === 0) return null;
  const last = history[history.length - 1];
  const prior = last.score !== score ? last : history[history.length - 2];
  if (!prior) return null;
  return { prior: prior.score, date: prior.date, direction: score > prior.score ? "up" : score < prior.score ? "down" : "same" };
}

// ---------------------------------------------------------------------------- report

function dayNumber(iso: string): number {
  return Math.floor(Date.parse(`${iso.slice(0, 10)}T00:00:00Z`) / 86_400_000);
}

/**
 * Days until the next report and how far through the reporting interval today is (from
 * the last report date, when known). Null without a next date.
 */
export function reportCountdown(
  next: string | null | undefined,
  last: string | null | undefined,
  today: string,
): { days: number; elapsed: number | null } | null {
  if (!next) return null;
  const days = dayNumber(next) - dayNumber(today);
  if (!last) return { days, elapsed: null };
  const span = dayNumber(next) - dayNumber(last);
  if (span <= 0) return { days, elapsed: null };
  return { days, elapsed: Math.min(1, Math.max(0, (dayNumber(today) - dayNumber(last)) / span)) };
}

// ---------------------------------------------------------------------------- timeline

export type TimelineVerdict = {
  kpiLabel: string;
  period: string | null;
  status: string;
  observed: string;
  target: string;
  created_at: string;
  factCount: number;
};
export type TimelineStory = {
  id: number;
  date: string;
  headline: string;
  source: string;
  severity: number | null;
  href: string | null;
};
export type TimelineEntry =
  | { kind: "verdict"; key: string; date: string; text: string; source: string; tone: "pass" | "breach" | "unknown" }
  | { kind: "news"; key: string; date: string; text: string; source: string; severity: number | null; href: string | null };

/**
 * Verdict runs (one entry per recording day and period: "3 of 4 KPIs pass; China share
 * breached (41% vs ≤ 30%)") merged with the holding's news stories, newest first.
 */
export function buildTimeline(verdicts: readonly TimelineVerdict[], stories: readonly TimelineStory[], limit = 8): TimelineEntry[] {
  const runs = new Map<string, TimelineVerdict[]>();
  for (const v of verdicts) {
    const key = `${v.created_at.slice(0, 10)}|${v.period ?? ""}`;
    runs.set(key, [...(runs.get(key) ?? []), v]);
  }
  const verdictEntries: TimelineEntry[] = [...runs.entries()].map(([key, vs]) => {
    const pass = vs.filter((v) => v.status === "pass").length;
    const breaches = vs.filter((v) => v.status === "breach");
    const period = vs[0].period;
    const lead = `Verdict${period ? ` ${period}` : ""}: ${pass} of ${vs.length} KPI${vs.length === 1 ? "" : "s"} pass`;
    const detail = breaches.length
      ? `; ${breaches.map((b) => `${b.kpiLabel} breached (${b.observed} vs ${b.target})`).join(", ")}`
      : "";
    const facts = vs.reduce((s, v) => s + v.factCount, 0);
    return {
      kind: "verdict",
      key: `v:${key}`,
      date: vs.reduce((d, v) => (v.created_at > d ? v.created_at : d), vs[0].created_at),
      text: `${lead}${detail}.`,
      source: `Verdict · ${facts} fact${facts === 1 ? "" : "s"}`,
      tone: breaches.length ? "breach" : pass === vs.length ? "pass" : "unknown",
    };
  });
  const newsEntries: TimelineEntry[] = stories.map((s) => ({
    kind: "news",
    key: `n:${s.id}`,
    date: s.date,
    text: s.headline,
    source: s.source,
    severity: s.severity,
    href: s.href,
  }));
  return [...verdictEntries, ...newsEntries].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0)).slice(0, limit);
}

// ---------------------------------------------------------------------------- ownership

type HolderLike = {
  cik: string;
  manager_name: string | null;
  shares: number | string;
  prev_shares?: number | string | null;
  shares_change_pct?: number | null;
};

/**
 * The largest 13F holders as bars relative to the largest, with the quarter-on-quarter
 * change in shares (a fraction) — "new" when the manager held none in the previous period
 * loaded, null when there is no earlier period to compare with.
 */
export function holderBars(
  holders: readonly HolderLike[],
  previousPeriod: string | null | undefined,
  limit = 6,
): { key: string; name: string; shares: number; share: number; change: number | "new" | null }[] {
  const rows = holders
    .map((h) => ({ h, shares: Number(h.shares) }))
    .filter((r) => Number.isFinite(r.shares) && r.shares > 0)
    .slice(0, limit);
  const top = Math.max(0, ...rows.map((r) => r.shares));
  return rows.map(({ h, shares }) => ({
    key: h.cik,
    name: h.manager_name ?? `CIK ${h.cik}`,
    shares,
    share: top > 0 ? shares / top : 0,
    change:
      typeof h.shares_change_pct === "number"
        ? h.shares_change_pct
        : previousPeriod && (h.prev_shares === null || h.prev_shares === undefined)
          ? "new"
          : null,
  }));
}

// ---------------------------------------------------------------------------- identity

/** Exchange names for the market identifier codes the header meets most. */
const EXCHANGES: Record<string, string> = {
  XAMS: "Euronext Amsterdam",
  XPAR: "Euronext Paris",
  XBRU: "Euronext Brussels",
  XLIS: "Euronext Lisbon",
  XMIL: "Borsa Italiana",
  XETR: "Xetra",
  XFRA: "Frankfurt",
  XLON: "London Stock Exchange",
  XSWX: "SIX Swiss Exchange",
  XCSE: "Nasdaq Copenhagen",
  XSTO: "Nasdaq Stockholm",
  XHEL: "Nasdaq Helsinki",
  XOSL: "Oslo Børs",
  XPRA: "Prague Stock Exchange",
  XWAR: "Warsaw Stock Exchange",
  XNAS: "Nasdaq",
  XNYS: "NYSE",
  ARCX: "NYSE Arca",
  BATS: "Cboe BZX",
  XTSE: "Toronto Stock Exchange",
  XTKS: "Tokyo Stock Exchange",
  XHKG: "Hong Kong Exchanges",
  XTAI: "Taiwan Stock Exchange",
  XASX: "ASX",
};

/** "XAMS" -> "Euronext Amsterdam"; an unknown code is shown as the code. */
export function exchangeName(mic: string | null | undefined): string | null {
  if (!mic) return null;
  return EXCHANGES[mic.toUpperCase()] ?? mic;
}

export type ThesisState = "intact" | "watch" | "at_risk" | "unjudged";

export const THESIS_STATE_LABEL: Record<ThesisState, string> = {
  intact: "Intact",
  watch: "Watch",
  at_risk: "At risk",
  unjudged: "Unjudged",
};
