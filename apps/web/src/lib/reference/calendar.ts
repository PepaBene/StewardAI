import type { ClosureSuggestion, ExchangeClosure } from "@/lib/api/client";

/*
 * The exchange-calendar page: public holidays (Nager.Date) in each holding exchange's
 * country are only suggestions, because public holidays are not exchange calendars; the
 * owner confirms each one into the Jobs config.
 */

export const CALENDAR_NOTE =
  "Public holidays are not exchange calendars: NYSE closes on Good Friday, which is not a US federal holiday, and Xetra and the LSE have their own closures. Each suggested day is a public holiday in the exchange's country; confirm only the days the exchange is actually closed.";

export function closureKey(c: Pick<ExchangeClosure, "mic" | "date">): string {
  return `${c.mic}:${c.date}`;
}

/** By date, then MIC. */
export function sortClosures<T extends Pick<ExchangeClosure, "mic" | "date">>(rows: T[]): T[] {
  return [...rows].sort((a, b) => a.date.localeCompare(b.date) || a.mic.localeCompare(b.mic));
}

/** The request body for the selected suggestions (in date order); null when none is selected. */
export function selectedClosures(
  suggestions: ClosureSuggestion[],
  selected: ReadonlySet<string>,
): { closures: ExchangeClosure[] } | null {
  const closures = sortClosures(suggestions)
    .filter((s) => selected.has(closureKey(s)))
    .map((s) => ({ mic: s.mic, date: s.date, name: s.name.trim().slice(0, 100) || "Closed" }));
  return closures.length > 0 ? { closures } : null;
}

/** Whether a day is before `today` (both ISO dates). */
export function isPast(date: string, today: string): boolean {
  return date < today;
}

/** Today's date as YYYY-MM-DD in local time. */
export function isoToday(now: Date = new Date()): string {
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}
