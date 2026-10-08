import { formatDate } from "@/lib/format";

export const STALE_AFTER_DAYS = 5; // matches the backend limit: a weekend plus a market holiday

export type DataAge = { label: string; date: string | null | undefined; ageDays: number | null | undefined };

/** Whole days from `date` (YYYY-MM-DD) to `today`; null when the date is missing or not a date. */
export function ageInDays(date: string | null | undefined, today: Date = new Date()): number | null {
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const then = Date.parse(`${date}T00:00:00Z`);
  if (Number.isNaN(then)) return null;
  const now = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.max(0, Math.round((now - then) / 86_400_000));
}

export function ageWords(days: number | null | undefined): string {
  if (days === null || days === undefined) return "unknown date";
  if (days <= 0) return "today";
  return days === 1 ? "1 day old" : `${days} days old`;
}

/** How old the data under a chart is. It turns into a warning past the limit, so an old figure is
 * never read as current. */
export function DataAgeBadge({
  items,
  staleAfterDays = STALE_AFTER_DAYS,
}: {
  items: DataAge[];
  staleAfterDays?: number;
}) {
  if (!items.length) return null;
  const stale = items.some((i) => i.ageDays !== null && i.ageDays !== undefined && i.ageDays > staleAfterDays);
  return (
    <p
      className={`mt-2 inline-flex flex-wrap gap-x-4 gap-y-1 border px-2 py-1 text-xs ${
        stale ? "border-warning-signal bg-warning-signal-subtle text-text" : "border-border text-text-muted"
      }`}
    >
      {items.map((i) => (
        <span key={i.label}>
          {i.label}: {i.date ? formatDate(i.date) : "no date stored"} ({ageWords(i.ageDays)})
        </span>
      ))}
      {stale ? <span className="font-medium">Older than {staleAfterDays} days: figures may lag the market.</span> : null}
    </p>
  );
}
