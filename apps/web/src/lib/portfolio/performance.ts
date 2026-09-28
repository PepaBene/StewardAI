import { maxDrawdown } from "@/lib/charts/series";

/*
 * Derivations for the Overview hero and its stat tiles, all from GET /portfolio/history:
 * `points` is the flow-adjusted daily index (deposits and withdrawals excluded), `benchmark`
 * the benchmark's raw closes (trading days only), `values` the absolute daily totals. One
 * long window is fetched once; ranges are cut client-side. Nothing here fills a gap beyond
 * a market holiday or extrapolates: missing input gives null.
 */

export type DatedClose = { date: string; close: number };
export type LinePoint = { date: string; value: number | null };

export const HERO_RANGES = [
  { id: "1m", label: "1M" },
  { id: "3m", label: "3M" },
  { id: "ytd", label: "YTD" },
  { id: "1y", label: "1Y" },
  { id: "2y", label: "2Y" },
  { id: "max", label: "Max" },
] as const;
export type HeroRangeId = (typeof HERO_RANGES)[number]["id"];
export const DEFAULT_HERO_RANGE: HeroRangeId = "1y";

/** Days of history fetched once for every range, the heatmap and the risk figures (the API maximum). */
export const HISTORY_DAYS = 3700;

function shiftMonths(iso: string, months: number): string {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  const day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + months);
  const last = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0)).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.toISOString().slice(0, 10);
}

/**
 * The anchor date of a range ending at `asOf`: returns are measured from the last point on
 * or before it (YTD from the previous year's last day, the usual convention). Null = the
 * whole series.
 */
export function rangeStart(range: HeroRangeId, asOf: string): string | null {
  switch (range) {
    case "1m":
      return shiftMonths(asOf, -1);
    case "3m":
      return shiftMonths(asOf, -3);
    case "ytd":
      return `${Number(asOf.slice(0, 4)) - 1}-12-31`;
    case "1y":
      return shiftMonths(asOf, -12);
    case "2y":
      return shiftMonths(asOf, -24);
    case "max":
      return null;
  }
}

/** Index of the last point dated on or before `start` (0 when none is, or no start). */
export function anchorIndex(dates: readonly string[], start: string | null): number {
  if (start === null) return 0;
  let idx = 0;
  for (let i = 0; i < dates.length; i++) {
    if (dates[i] <= start) idx = i;
    else break;
  }
  return idx;
}

/** Points from the range's anchor on (oldest first; input must be sorted). */
export function sliceFrom<T extends { date: string }>(series: readonly T[], start: string | null): T[] {
  return series.slice(anchorIndex(series.map((p) => p.date), start));
}

/** Compounded return of an index from the range's anchor to its last point; null under two points. */
export function periodReturn(points: readonly DatedClose[], start: string | null): number | null {
  const s = sliceFrom(points, start);
  if (s.length < 2 || !(s[0].close > 0)) return null;
  return s[s.length - 1].close / s[0].close - 1;
}

/**
 * The benchmark's closes carried onto the portfolio's dates: a date with no close takes the
 * previous close when it is at most `maxCarryDays` old (a weekend or market holiday), else null.
 */
export function alignBenchmark(
  dates: readonly string[],
  benchmark: readonly DatedClose[],
  maxCarryDays = 4,
): (number | null)[] {
  const sorted = [...benchmark].sort((a, b) => a.date.localeCompare(b.date));
  const out: (number | null)[] = [];
  let j = -1;
  for (const date of dates) {
    while (j + 1 < sorted.length && sorted[j + 1].date <= date) j++;
    if (j < 0) {
      out.push(null);
      continue;
    }
    const gap = (Date.parse(`${date}T00:00:00Z`) - Date.parse(`${sorted[j].date}T00:00:00Z`)) / 86_400_000;
    out.push(gap <= maxCarryDays ? sorted[j].close : null);
  }
  return out;
}

export type IndexedRange = {
  portfolio: LinePoint[];
  /** Null when the benchmark has no close near the range's first date. */
  benchmark: LinePoint[] | null;
  portfolioReturn: number | null;
  benchmarkReturn: number | null;
};

/**
 * Both series from the range's anchor, rebased to 100 on the anchor day. The benchmark is
 * drawn only when it has a (carried) close on the anchor day.
 */
export function indexedRange(
  points: readonly DatedClose[],
  benchmark: readonly DatedClose[],
  start: string | null,
): IndexedRange {
  const s = sliceFrom(points, start);
  if (s.length === 0) return { portfolio: [], benchmark: null, portfolioReturn: null, benchmarkReturn: null };
  const base = s[0].close;
  const portfolio = s.map((p) => ({ date: p.date, value: base > 0 ? (p.close / base) * 100 : null }));
  const bench = alignBenchmark(
    s.map((p) => p.date),
    benchmark,
  );
  const b0 = bench[0];
  const benchLine = b0 !== null && b0 > 0 ? s.map((p, i) => ({ date: p.date, value: bench[i] === null ? null : (bench[i]! / b0) * 100 })) : null;
  const lastBench = benchLine ? [...benchLine].reverse().find((p) => p.value !== null)?.value ?? null : null;
  const lastPort = portfolio[portfolio.length - 1].value;
  return {
    portfolio,
    benchmark: benchLine,
    portfolioReturn: s.length >= 2 && lastPort !== null ? lastPort / 100 - 1 : null,
    benchmarkReturn: benchLine && lastBench !== null && s.length >= 2 ? lastBench / 100 - 1 : null,
  };
}

export type DayChange = {
  /** Flow-adjusted return between the last two snapshots. */
  pct: number;
  /** That return in money, on the earlier snapshot's total (so a deposit is not a gain). */
  amount: number | null;
  /** Date of the earlier snapshot. */
  since: string;
  /** Date of the latest snapshot. */
  asOf: string;
};

/** The change between the last two history points; null under two points. */
export function lastChange(
  points: readonly DatedClose[],
  values: readonly { date: string; total: number }[],
): DayChange | null {
  if (points.length < 2) return null;
  const prev = points[points.length - 2];
  const last = points[points.length - 1];
  if (!(prev.close > 0)) return null;
  const pct = last.close / prev.close - 1;
  const prevTotal = values.find((v) => v.date === prev.date)?.total;
  return { pct, amount: prevTotal === undefined ? null : prevTotal * pct, since: prev.date, asOf: last.date };
}

function stdev(xs: readonly number[]): number {
  const mean = xs.reduce((s, x) => s + x, 0) / xs.length;
  return Math.sqrt(xs.reduce((s, x) => s + (x - mean) ** 2, 0) / (xs.length - 1));
}

/** Observations per year of a dated series (calendar-daily ≈ 365, trading-daily ≈ 252). */
function perYear(dates: readonly string[]): number {
  const span = (Date.parse(`${dates[dates.length - 1]}T00:00:00Z`) - Date.parse(`${dates[0]}T00:00:00Z`)) / 86_400_000;
  return span > 0 ? ((dates.length - 1) * 365.25) / span : 0;
}

/** Minimum returns before a volatility or beta is reported. */
export const MIN_RETURNS = 20;

/** Annualised standard deviation of simple period returns; null under MIN_RETURNS returns. */
export function annualisedVolatility(series: readonly DatedClose[]): number | null {
  const rets: number[] = [];
  for (let i = 1; i < series.length; i++) {
    if (series[i - 1].close > 0) rets.push(series[i].close / series[i - 1].close - 1);
  }
  if (rets.length < MIN_RETURNS) return null;
  return stdev(rets) * Math.sqrt(perYear(series.map((p) => p.date)));
}

/**
 * Beta of the portfolio index against the benchmark, from returns between consecutive
 * benchmark trading days on which the portfolio also has a point. Null under MIN_RETURNS.
 */
export function beta(points: readonly DatedClose[], benchmark: readonly DatedClose[]): number | null {
  const port = new Map(points.map((p) => [p.date, p.close]));
  const pairs = [...benchmark]
    .sort((a, b) => a.date.localeCompare(b.date))
    .filter((b) => port.has(b.date))
    .map((b) => [port.get(b.date)!, b.close] as const);
  const rp: number[] = [];
  const rb: number[] = [];
  for (let i = 1; i < pairs.length; i++) {
    if (pairs[i - 1][0] > 0 && pairs[i - 1][1] > 0) {
      rp.push(pairs[i][0] / pairs[i - 1][0] - 1);
      rb.push(pairs[i][1] / pairs[i - 1][1] - 1);
    }
  }
  if (rb.length < MIN_RETURNS) return null;
  const mp = rp.reduce((s, x) => s + x, 0) / rp.length;
  const mb = rb.reduce((s, x) => s + x, 0) / rb.length;
  let cov = 0;
  let varB = 0;
  for (let i = 0; i < rb.length; i++) {
    cov += (rp[i] - mp) * (rb[i] - mb);
    varB += (rb[i] - mb) ** 2;
  }
  return varB > 0 ? cov / varB : null;
}

export type RiskFigures = {
  /** Start of the window actually covered (may be later than a year ago). */
  from: string;
  to: string;
  /** True when the history covers the full year. */
  fullYear: boolean;
  return: number | null;
  volatility: number | null;
  maxDrawdown: number | null;
  beta: number | null;
  benchmarkReturn: number | null;
  benchmarkVolatility: number | null;
  benchmarkMaxDrawdown: number | null;
};

/** Risk figures over the trailing year of the flow-adjusted index (or all of it when shorter). */
export function trailingRisk(points: readonly DatedClose[], benchmark: readonly DatedClose[]): RiskFigures | null {
  if (points.length < 2) return null;
  const asOf = points[points.length - 1].date;
  const start = shiftMonths(asOf, -12);
  const window = sliceFrom(points, start);
  const from = window[0].date;
  const benchWindow = benchmark.filter((b) => b.date >= from && b.date <= asOf);
  const dd = maxDrawdown(window.map((p) => p.close));
  const bdd = maxDrawdown(benchWindow.map((b) => b.close));
  return {
    from,
    to: asOf,
    fullYear: from <= start,
    return: periodReturn(window, null),
    volatility: annualisedVolatility(window),
    maxDrawdown: dd ? dd.value : null,
    beta: beta(window, benchWindow),
    benchmarkReturn: periodReturn(benchWindow, null),
    benchmarkVolatility: annualisedVolatility(benchWindow),
    benchmarkMaxDrawdown: bdd ? bdd.value : null,
  };
}
