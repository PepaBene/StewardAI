/*
 * Activity calendar layout: dated counts as a grid of weeks (columns, Monday first) by weekday
 * (rows), ending on the week that holds `end`. Pure, so the grid is tested without rendering.
 */

export type CalendarDay = { date: string; count: number; inRange: boolean };
export type CalendarWeek = { start: string; days: CalendarDay[] };

const DAY = 86_400_000;

function iso(t: number): string {
  return new Date(t).toISOString().slice(0, 10);
}

function utc(date: string): number {
  return Date.parse(`${date}T00:00:00Z`);
}

/** Counts per ISO day from any list of dates (or timestamps; only the day part counts). */
export function countByDay(dates: readonly (string | null | undefined)[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const d of dates) {
    if (!d) continue;
    const day = d.slice(0, 10);
    out.set(day, (out.get(day) ?? 0) + 1);
  }
  return out;
}

/** `weeks` columns ending with the week of `end`; days after `end` are out of range. */
export function calendarWeeks(counts: ReadonlyMap<string, number>, end: string, weeks = 26): CalendarWeek[] {
  const endT = utc(end);
  const mondayOffset = (new Date(endT).getUTCDay() + 6) % 7;
  const firstMonday = endT - mondayOffset * DAY - (weeks - 1) * 7 * DAY;
  return Array.from({ length: weeks }, (_, w) => {
    const start = firstMonday + w * 7 * DAY;
    return {
      start: iso(start),
      days: Array.from({ length: 7 }, (_, d) => {
        const t = start + d * DAY;
        const date = iso(t);
        return { date, count: counts.get(date) ?? 0, inRange: t <= endT };
      }),
    };
  });
}

/** 0 for no activity, then 1 to 4 by share of the busiest day: the step of the one-hue ramp. */
export function activityLevel(count: number, max: number): 0 | 1 | 2 | 3 | 4 {
  if (count <= 0 || max <= 0) return 0;
  const r = count / max;
  return r > 0.75 ? 4 : r > 0.5 ? 3 : r > 0.25 ? 2 : 1;
}
