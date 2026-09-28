/*
 * Series transforms for the chart kit: moving average, drawdown, rebasing and monthly
 * returns. Nothing here fills a gap or extrapolates: missing input yields null output.
 */

import { isFiniteNumber } from "./scale";
import { parseDay } from "./time";

/** A value on a calendar day (ISO date string). */
export type DatedValue = { date: string; value: number | null };

/**
 * Simple moving average over the last `window` values. Null until a full window of finite
 * values is available (no partial-window averages at the start), and null wherever the
 * window contains a gap.
 */
export function movingAverage(values: readonly (number | null)[], window: number): (number | null)[] {
  const w = Math.max(1, Math.floor(window));
  return values.map((_, i) => {
    if (i + 1 < w) return null;
    let sum = 0;
    for (let j = i - w + 1; j <= i; j++) {
      const v = values[j];
      if (!isFiniteNumber(v)) return null;
      sum += v;
    }
    return sum / w;
  });
}

/**
 * Drawdown from the running peak, as a fraction ≤ 0 (−0.184 = 18.4% below the high so far).
 * Gaps stay null and do not reset the peak. Non-positive values are treated as gaps (a
 * drawdown is only defined for a positive price or index level).
 */
export function drawdown(values: readonly (number | null)[]): (number | null)[] {
  let peak = -Infinity;
  return values.map((v) => {
    if (!isFiniteNumber(v) || v <= 0) return null;
    if (v > peak) peak = v;
    return v / peak - 1;
  });
}

/** The deepest point of `drawdown(values)`: its value, index and the index of its peak. */
export function maxDrawdown(
  values: readonly (number | null)[],
): { value: number; index: number; peakIndex: number } | null {
  let peak = -Infinity;
  let peakIndex = -1;
  let best: { value: number; index: number; peakIndex: number } | null = null;
  values.forEach((v, i) => {
    if (!isFiniteNumber(v) || v <= 0) return;
    if (v > peak) {
      peak = v;
      peakIndex = i;
    }
    const dd = v / peak - 1;
    if (!best || dd < best.value) best = { value: dd, index: i, peakIndex };
  });
  return best;
}

/** Rebase a series so its first finite value equals `base` (index to 100). */
export function rebase(values: readonly (number | null)[], base = 100): (number | null)[] {
  const first = values.find((v): v is number => isFiniteNumber(v) && v !== 0);
  if (first === undefined) return values.map(() => null);
  return values.map((v) => (isFiniteNumber(v) ? (v / first) * base : null));
}

/** One calendar year of monthly returns (fractions); null where a month has no data. */
export type MonthlyReturnRow = {
  year: number;
  months: (number | null)[];
  /** Compounded over the months that have a return; null when none do. */
  total: number | null;
};

/**
 * Buckets a daily price/index series into calendar-month returns. Each month's return is
 * its last value over the previous month's last value. The first month in the series is
 * measured from the series' first value (a partial month). A month whose previous calendar
 * month has no data gets null rather than a multi-month return mislabelled as one month.
 * Rows are sorted by year, oldest first.
 */
export function monthlyReturns(points: readonly DatedValue[]): MonthlyReturnRow[] {
  const dated = points
    .map((p) => ({ day: parseDay(p.date), date: p.date, value: p.value }))
    .filter(
      (p): p is { day: NonNullable<ReturnType<typeof parseDay>>; date: string; value: number } =>
        p.day !== null && isFiniteNumber(p.value) && p.value > 0,
    )
    .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  if (dated.length < 2) return [];

  const key = (year: number, month: number) => year * 12 + month;
  const monthEnd = new Map<number, number>();
  for (const p of dated) monthEnd.set(key(p.day.year, p.day.month), p.value);

  const firstKey = key(dated[0].day.year, dated[0].day.month);
  const firstValue = dated[0].value;
  const rows = new Map<number, (number | null)[]>();

  for (const [k, end] of monthEnd) {
    const year = Math.floor(k / 12);
    const month = k % 12;
    const base = k === firstKey ? firstValue : monthEnd.get(k - 1);
    const ret = base === undefined ? null : end / base - 1;
    if (!rows.has(year)) rows.set(year, Array<number | null>(12).fill(null));
    rows.get(year)![month] = ret;
  }

  return [...rows.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([year, months]) => {
      const present = months.filter(isFiniteNumber);
      const total = present.length ? present.reduce((acc, r) => acc * (1 + r), 1) - 1 : null;
      return { year, months, total };
    });
}
