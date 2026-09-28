/*
 * Calendar helpers for time-axis charts. Dates are ISO strings ("2026-09-27" or a full
 * timestamp); only the calendar day is read, so labels never shift with the viewer's time
 * zone and server/client renders agree.
 */

export const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/** One-letter month headers for a 12-column heatmap. */
export const MONTH_INITIAL = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"] as const;

export type CalendarDay = { year: number; month: number; day: number };

/** "2026-09-27[T...]" -> { year: 2026, month: 8 (0-based), day: 27 }, or null if unparseable. */
export function parseDay(iso: string): CalendarDay | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]) - 1;
  const day = Number(m[3]);
  if (month < 0 || month > 11 || day < 1 || day > 31) return null;
  return { year, month, day };
}

/** "27 Sep 2026" — for accessible summaries and tooltips. */
export function formatDay(iso: string): string {
  const d = parseDay(iso);
  return d ? `${d.day} ${MONTH_SHORT[d.month]} ${d.year}` : iso;
}

export type TimeTick = { index: number; label: string };

/**
 * X-axis ticks at month boundaries: the index of the first point of each month, labelled
 * "Oct". Series spanning more than two years switch to year ticks ("2025") at January.
 * The first point only gets a tick when it falls in the first week of its month (otherwise
 * it would crowd the next month's tick). Thinned to at most `maxTicks`.
 */
export function timeTicks(dates: readonly string[], maxTicks = 12): TimeTick[] {
  const days = dates.map(parseDay);
  const valid = days.filter((d): d is CalendarDay => d !== null);
  if (valid.length < 2) return [];
  const first = valid[0];
  const last = valid[valid.length - 1];
  const months = (last.year - first.year) * 12 + (last.month - first.month);
  const yearly = months > 24;

  const ticks: TimeTick[] = [];
  let prev: CalendarDay | null = null;
  days.forEach((d, index) => {
    if (!d) return;
    const isFirst = prev === null;
    const boundary = yearly
      ? prev !== null && d.year !== prev.year
      : prev !== null && (d.month !== prev.month || d.year !== prev.year);
    const firstQualifies = isFirst && (yearly ? d.month === 0 && d.day <= 7 : d.day <= 7);
    if (boundary || firstQualifies) {
      ticks.push({ index, label: yearly ? String(d.year) : MONTH_SHORT[d.month] });
    }
    prev = d;
  });

  if (ticks.length <= maxTicks) return ticks;
  const every = Math.ceil(ticks.length / maxTicks);
  return ticks.filter((_, i) => i % every === 0);
}
