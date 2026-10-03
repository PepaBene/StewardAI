import { isFiniteNumber } from "@/lib/charts/scale";
import { isDataState, type DataState } from "@/lib/data-state/states";

import type {
  CorporateAction,
  ExchangeCalendar,
  ExchangeSessionRow,
  ExpectedSessions,
  HoldingPrices,
  PriceDiagnostic,
  ReturnsReport,
  SessionCell,
  SessionStatus,
} from "./types";

// ---------------------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------------------

const DAY_MS = 86_400_000;

function toMs(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}

export function addDays(iso: string, days: number): string {
  return new Date(toMs(iso) + days * DAY_MS).toISOString().slice(0, 10);
}

/** The local calendar date as YYYY-MM-DD (the user's "today", used for windows). */
export function todayIso(now: Date = new Date()): string {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/** Monday-to-Friday dates in [start, end], inclusive. */
export function weekdaysBetween(start: string, end: string): string[] {
  const out: string[] = [];
  for (let t = toMs(start); t <= toMs(end); t += DAY_MS) {
    const dow = new Date(t).getUTCDay();
    if (dow !== 0 && dow !== 6) out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// Quote units
// ---------------------------------------------------------------------------------------

/** Minor-unit quote codes: hundredths of the major unit. */
const MINOR_UNITS: Record<string, { major: string; divisor: number }> = {
  GBX: { major: "GBP", divisor: 100 },
  GBp: { major: "GBP", divisor: 100 },
  ZAc: { major: "ZAR", divisor: 100 },
  ZAC: { major: "ZAR", divisor: 100 },
  ILa: { major: "ILS", divisor: 100 },
  ILA: { major: "ILS", divisor: 100 },
};

export function majorUnit(quote: string): { currency: string; divisor: number } {
  const m = MINOR_UNITS[quote];
  return m ? { currency: m.major, divisor: m.divisor } : { currency: quote, divisor: 1 };
}

/** "1,240.50 GBX (12.41 GBP)": the quote as stored, plus the major-unit value for a minor-unit quote. */
export function describeQuote(value: number, quote: string): string {
  const fmt = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  const { currency, divisor } = majorUnit(quote);
  return divisor === 1 ? `${fmt(value)} ${quote}` : `${fmt(value)} ${quote} (${fmt(value / divisor)} ${currency})`;
}

// ---------------------------------------------------------------------------------------
// Price series diagnostics
// ---------------------------------------------------------------------------------------

/** The adjusted series every reader serves (`chosen`), or null when none is stored. */
export function chosenSeries(rows: readonly PriceDiagnostic[]): PriceDiagnostic | null {
  return rows.find((r) => r.chosen && r.bar_type === "adjusted") ?? rows.find((r) => r.chosen) ?? null;
}

/** Stored raw-bar series of the same listing (not served by the price endpoint). */
export function rawSeries(rows: readonly PriceDiagnostic[], chosen: PriceDiagnostic | null): PriceDiagnostic | null {
  if (!chosen) return null;
  return rows.find((r) => r.bar_type === "raw" && r.symbol === chosen.symbol && r.source === chosen.source) ?? null;
}

/** The quote code the series is printed in: the unit when present ("GBX"), else the currency. */
export function quoteOf(series: PriceDiagnostic | null): string | null {
  return series ? (series.quote_unit ?? series.currency ?? null) : null;
}

/** Held share of the sessions the exchange held, for one series; null without a denominator. */
export function seriesCompleteness(s: Pick<PriceDiagnostic, "expected_sessions" | "missing_days">): number | null {
  if (!(s.expected_sessions > 0)) return null;
  return Math.max(0, (s.expected_sessions - s.missing_days) / s.expected_sessions);
}

// ---------------------------------------------------------------------------------------
// Corporate-action markers
// ---------------------------------------------------------------------------------------

/** The amount of an action as text, or null when the ledger does not record one. */
export function describeAction(a: CorporateAction): string | null {
  if ((a.type === "split" || a.type === "stock_dividend") && a.ratio !== null) return `ratio ${a.ratio}`;
  if (a.type === "cash_dividend" && a.cashAmount !== null) {
    const per = a.per === "ordinary_share" ? " per ordinary share" : "";
    return `${a.cashAmount} ${a.currency ?? "(currency not recorded)"}${per}`;
  }
  return null;
}

export type PlacedAction = {
  action: CorporateAction;
  /** Index of the first bar on or after the ex-date; null when the date is outside the series. */
  index: number | null;
  /** The ex-date is not itself a bar (weekend/holiday): drawn at the next session. */
  shifted: boolean;
};

export function placeActions(bars: readonly { date: string }[], actions: readonly CorporateAction[]): PlacedAction[] {
  if (bars.length === 0) return actions.map((action) => ({ action, index: null, shifted: false }));
  const first = bars[0].date;
  const last = bars[bars.length - 1].date;
  return actions.map((action) => {
    if (action.exDate < first || action.exDate > last) return { action, index: null, shifted: false };
    const index = bars.findIndex((b) => b.date >= action.exDate);
    return { action, index, shifted: index >= 0 && bars[index].date !== action.exDate };
  });
}

/**
 * Close-to-close moves beyond `threshold` that no recorded action (of any status but rejected)
 * explains. A warning that an action may be missing or a bar is bad; never evidence of an action.
 */
export function unexplainedJumps(
  bars: readonly { date: string; close: number | null }[],
  actions: readonly CorporateAction[],
  threshold = 0.3,
): { date: string; change: number }[] {
  const explained = new Set(actions.filter((a) => a.status !== "rejected").map((a) => a.exDate));
  const out: { date: string; change: number }[] = [];
  for (let i = 1; i < bars.length; i++) {
    const prev = bars[i - 1].close;
    const cur = bars[i].close;
    if (!isFiniteNumber(prev) || !isFiniteNumber(cur) || !(prev > 0)) continue;
    const change = cur / prev - 1;
    if (Math.abs(change) > threshold && !explained.has(bars[i].date)) out.push({ date: bars[i].date, change });
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// Event study
// ---------------------------------------------------------------------------------------

export type EventWindow = { eventDate: string; label: string; points: { offset: number; value: number }[] };

/**
 * Indexed closes (100 at the last session before the event) for sessions -before..+after around
 * each event date. Windows that run off the series are omitted rather than padded.
 */
export function eventWindows(
  bars: readonly { date: string; close: number | null }[],
  events: readonly { date: string; label: string }[],
  before: number,
  after: number,
): EventWindow[] {
  const out: EventWindow[] = [];
  for (const e of events) {
    const idx = bars.findIndex((b) => b.date >= e.date);
    if (idx < 1 || idx - before < 0 || idx + after >= bars.length) continue;
    const base = bars[idx - 1].close;
    if (!isFiniteNumber(base) || !(base > 0)) continue;
    const points: { offset: number; value: number }[] = [];
    let complete = true;
    for (let k = -before; k <= after; k++) {
      const c = bars[idx + k].close;
      if (!isFiniteNumber(c)) {
        complete = false;
        break;
      }
      points.push({ offset: k, value: (c / base) * 100 });
    }
    if (complete) out.push({ eventDate: e.date, label: e.label, points });
  }
  return out;
}

export function meanWindow(windows: readonly EventWindow[]): { offset: number; value: number }[] | null {
  if (windows.length === 0) return null;
  return windows[0].points.map((p, i) => ({ offset: p.offset, value: windows.reduce((s, w) => s + w.points[i].value, 0) / windows.length }));
}

export type EventStudyGate = { allowed: true } | { allowed: false; reason: string };

/**
 * An event study is shown only where the backend says the adjusted series is reconciled: the
 * owner verified corporate-action coverage through the window end (`action_coverage`), the
 * adjusted return has no missing-action flag, and no split is left unconfirmed. Otherwise a split
 * would read as a -75% "event reaction" and the reason replaces the panel.
 */
export function eventStudyGate(input: { hasAdjustedBars: boolean; returns: ReturnsReport | null; actions: readonly CorporateAction[] }): EventStudyGate {
  if (!input.hasAdjustedBars) return { allowed: false, reason: "No adjusted series is stored for this holding." };
  const r = input.returns?.result;
  if (!r) return { allowed: false, reason: input.returns?.reason ?? "The return window could not be read, so reconciliation cannot be judged." };
  if (r.actionCoverage !== "available") {
    return { allowed: false, reason: "Corporate-action coverage is not verified through the window end, so the adjusted series is not reconciled." };
  }
  if (r.missingActions.priceAdjusted.length > 0) return { allowed: false, reason: `The backend flags the adjusted series: ${r.missingActions.priceAdjusted.join("; ")}.` };
  const open = input.actions.filter((a) => (a.type === "split" || a.type === "stock_dividend") && (a.status === "suggested" || a.status === "disputed"));
  if (open.length > 0) return { allowed: false, reason: `${open.length} split or stock dividend ${open.length === 1 ? "is" : "are"} unconfirmed: the adjustment cannot be trusted yet.` };
  return { allowed: true };
}

// ---------------------------------------------------------------------------------------
// Correlation (numbers computed by the backend; this only arranges them)
// ---------------------------------------------------------------------------------------

export type CorrelationRow = {
  ticker: string;
  /** Correlations with each other holding as returned by GET /holdings/{ticker}/prices; null when that call had no series. */
  correlations: readonly { ticker: string; correlation: number | null }[] | null;
};

export type CorrelationGrid = {
  tickers: string[];
  /** matrix[i][j]: backend correlation of tickers[i] with tickers[j]; null when the backend returned none. */
  matrix: (number | null)[][];
  /** Pairs where the two rows' backend values differ by more than 0.01 (should be symmetric). */
  asymmetricPairs: number;
  /** Holdings whose own call returned no series, so they have no row. */
  withoutSeries: string[];
};

/**
 * Arranges the per-holding correlation vectors into a matrix. A cell takes row i's value, else
 * the mirrored value from row j; nothing is computed or imputed. The diagonal is 1 only for a
 * holding that has a series (a series' correlation with itself).
 */
export function assembleCorrelation(rows: readonly CorrelationRow[]): CorrelationGrid {
  const withSeries = rows.filter((r) => r.correlations !== null);
  const tickers = withSeries.map((r) => r.ticker);
  const lookup = new Map(withSeries.map((r) => [r.ticker, new Map(r.correlations!.map((c) => [c.ticker, c.correlation]))]));
  const matrix: (number | null)[][] = tickers.map((a) => tickers.map((b) => (a === b ? 1 : null)));
  let asymmetricPairs = 0;
  for (let i = 0; i < tickers.length; i++) {
    for (let j = i + 1; j < tickers.length; j++) {
      const ij = lookup.get(tickers[i])?.get(tickers[j]) ?? null;
      const ji = lookup.get(tickers[j])?.get(tickers[i]) ?? null;
      const v = ij ?? ji;
      matrix[i][j] = v;
      matrix[j][i] = v;
      if (ij !== null && ji !== null && Math.abs(ij - ji) > 0.01) asymmetricPairs += 1;
    }
  }
  return { tickers, matrix, asymmetricPairs, withoutSeries: rows.filter((r) => r.correlations === null).map((r) => r.ticker) };
}

/**
 * Leaf order of an average-linkage clustering on 1 - r so similar holdings sit together. A
 * missing correlation counts as distance 1 (unrelated), never 0. Presentation only: it reorders
 * backend numbers. Deterministic: ties break toward the lower index.
 */
export function clusterOrder(matrix: readonly (readonly (number | null)[])[]): number[] {
  const n = matrix.length;
  if (n <= 2) return Array.from({ length: n }, (_, i) => i);
  const dist = (i: number, j: number) => (isFiniteNumber(matrix[i][j]) ? 1 - matrix[i][j]! : 1);
  let clusters: { members: number[] }[] = Array.from({ length: n }, (_, i) => ({ members: [i] }));
  const avg = (a: number[], b: number[]) => {
    let s = 0;
    for (const i of a) for (const j of b) s += dist(i, j);
    return s / (a.length * b.length);
  };
  while (clusters.length > 1) {
    let best: [number, number] = [0, 1];
    let bestD = Infinity;
    for (let i = 0; i < clusters.length; i++) {
      for (let j = i + 1; j < clusters.length; j++) {
        const d = avg(clusters[i].members, clusters[j].members);
        if (d < bestD - 1e-12) {
          bestD = d;
          best = [i, j];
        }
      }
    }
    const [i, j] = best;
    const merged = { members: [...clusters[i].members, ...clusters[j].members] };
    clusters = clusters.filter((_, k) => k !== i && k !== j);
    clusters.push(merged);
  }
  return clusters[0].members;
}

// ---------------------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------------------

export type SessionInputs = {
  mic: string;
  start: string;
  end: string;
  today: string;
  /** Sessions the backend says the exchange held (GET /exchange-sessions/{mic}/expected). */
  expected: ExpectedSessions;
  /** Stored exchange_session rows (confirmed notices, half days, ad hoc closures). */
  sessions: readonly ExchangeSessionRow[];
  /** Calendar from GET /calendar/exchanges. */
  calendar: Pick<ExchangeCalendar, "closures" | "suggestions">;
  /** Dates of the bars we hold for the served series. */
  barDates: Iterable<string>;
  /** First bar date; days before it are outside the stored history, not missing bars. */
  firstBar: string | null;
};

/**
 * Per weekday: what the venue did and whether we hold a bar. A day the backend expects with no
 * bar is `missing_bar`; a national holiday (suggestion) on a day the venue is expected open never
 * excuses it. Closures are explained by a confirmed config closure (holiday) or a confirmed
 * session row (ad hoc closure). Dates after today are `upcoming`, and days before the first stored
 * bar are `before_history`: neither is counted as missing.
 */
export function buildSessionCells(i: SessionInputs): SessionCell[] {
  const have = new Set(i.barDates);
  const expected = new Set(i.expected.days);
  const closed = new Map(i.calendar.closures.filter((c) => c.mic === i.mic).map((c) => [c.date, c.name]));
  const national = new Map(i.calendar.suggestions.filter((c) => c.mic === i.mic).map((c) => [c.date, c.name]));
  const rowFor = new Map<string, ExchangeSessionRow>();
  for (const r of i.sessions) {
    const prev = rowFor.get(r.sessionDate);
    if (!prev || (r.confirmed && !prev.confirmed)) rowFor.set(r.sessionDate, r);
  }
  return weekdaysBetween(i.start, i.end).map((date): SessionCell => {
    const row = rowFor.get(date);
    const holiday = closed.get(date);
    if (!expected.has(date)) {
      if (holiday) return { date, status: "exchange_holiday", note: `${holiday} (confirmed closure)` };
      if (row?.state === "closed") {
        return /holiday/i.test(row.reason ?? "")
          ? { date, status: "exchange_holiday", note: row.reason }
          : { date, status: "exchange_closure", note: row.reason ?? "Closed per exchange notice" };
      }
      return { date, status: "exchange_closure", note: "Not an expected session" };
    }
    if (i.firstBar && date < i.firstBar) return { date, status: "before_history", note: "Before the first stored bar" };
    if (date > i.today || (date === i.today && !have.has(date))) return { date, status: "upcoming", note: null };
    const nat = national.get(date);
    if (!have.has(date)) {
      return { date, status: "missing_bar", note: nat ? `${nat} is a national holiday, but the exchange is expected open: still a gap` : "Venue expected open, no bar held" };
    }
    if (row?.state === "half_day") return { date, status: "half_day", note: row.reason };
    return nat ? { date, status: "national_holiday_open", note: `${nat}: national holiday, exchange open` } : { date, status: "trading", note: null };
  });
}

export function sessionCounts(cells: readonly SessionCell[]): Record<SessionStatus, number> {
  const c: Record<SessionStatus, number> = {
    trading: 0,
    half_day: 0,
    exchange_holiday: 0,
    national_holiday_open: 0,
    exchange_closure: 0,
    missing_bar: 0,
    upcoming: 0,
    before_history: 0,
  };
  for (const cell of cells) c[cell.status] += 1;
  return c;
}

/** Held bars over expected past sessions in stored history; null when none were expected. */
export function sessionCompleteness(cells: readonly SessionCell[]): { held: number; expected: number; share: number | null } {
  const c = sessionCounts(cells);
  const held = c.trading + c.half_day + c.national_holiday_open;
  const expected = held + c.missing_bar;
  return { held, expected, share: expected > 0 ? held / expected : null };
}

// ---------------------------------------------------------------------------------------
// Portfolio completeness
// ---------------------------------------------------------------------------------------

export type PortfolioPriceCoverage = {
  total: number;
  withBars: number;
  withoutBars: string[];
  /** Holdings whose latest bar is older than `staleDays` before the newest as-of among them. */
  stale: string[];
  newestAsOf: string | null;
};

export function portfolioPriceCoverage(holdings: readonly HoldingPrices[], staleDays = 5): PortfolioPriceCoverage {
  const withBars = holdings.filter((h) => h.bars.length > 0);
  const newest = withBars.reduce<string | null>((m, h) => (h.as_of && (!m || h.as_of > m) ? h.as_of : m), null);
  const cutoff = newest ? addDays(newest, -staleDays) : null;
  return {
    total: holdings.length,
    withBars: withBars.length,
    withoutBars: holdings.filter((h) => h.bars.length === 0).map((h) => h.ticker),
    stale: cutoff ? withBars.filter((h) => h.as_of !== null && h.as_of < cutoff).map((h) => h.ticker) : [],
    newestAsOf: newest,
  };
}

// ---------------------------------------------------------------------------------------
// Data state of the served series
// ---------------------------------------------------------------------------------------

export type SeriesState = { state: DataState; reason: string | null };

/**
 * The availability state of the series the chart draws, from the backend's own evidence: stored
 * bars, the Trading212 plausibility check, cross-source disagreements, sessions behind the last
 * expected one and missing days against the exchange calendar. Without diagnostics the series is
 * "partial" (completeness unknown), never "verified".
 */
export function seriesState(input: {
  barCount: number;
  gap: string | null;
  plausibility: { flagged: boolean; difference_pct: number; date: string } | null;
  chosen: PriceDiagnostic | null;
}): SeriesState {
  const { chosen } = input;
  if (input.barCount === 0) return { state: "not_covered", reason: input.gap ?? "No prices are stored for this holding." };
  if (chosen && chosen.access_state !== "available") {
    return { state: isDataState(chosen.access_state) ? chosen.access_state : "unavailable", reason: `Access state of the stored series: ${chosen.access_state}.` };
  }
  if (input.plausibility?.flagged) {
    return { state: "disputed", reason: `The last stored close differs from the Trading212 price by ${input.plausibility.difference_pct.toFixed(1)}% on ${input.plausibility.date}.` };
  }
  if (chosen && chosen.disagreements > 0) {
    return { state: "disputed", reason: `${chosen.disagreements} recent session${chosen.disagreements === 1 ? "" : "s"} where another source's close differs by more than 2%.` };
  }
  if (chosen && (chosen.stale_sessions ?? 0) > 0) {
    return { state: "stale", reason: `${chosen.stale_sessions} expected session${chosen.stale_sessions === 1 ? "" : "s"} after the last stored bar.` };
  }
  if (!chosen) return { state: "partial", reason: "Series diagnostics are not loaded, so completeness against the exchange calendar is unknown." };
  if (chosen.missing_days > 0) {
    return { state: "partial", reason: `${chosen.missing_days} of ${chosen.expected_sessions} expected sessions have no bar.` };
  }
  return { state: "verified", reason: null };
}
