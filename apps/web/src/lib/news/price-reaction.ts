/*
 * A story's price reaction, from the daily closes of `GET /portfolio/holdings/prices`
 * (one batch call for every holding): the close-to-close move from the last close before
 * the story's day to the first close on or after it. Daily bars only, so a story published
 * after the market closed shows the next session's move; nothing intraday is implied.
 */

export type ClosePoint = { date: string; close: number };

export type PriceReaction = {
  /** Fraction: 0.012 = +1.2 %. */
  pct: number;
  /** Close dates compared. */
  from: string;
  to: string;
  /** Closes around the story for a sparkline (up to `before` before and `after` after). */
  window: number[];
};

/** The story's calendar day (YYYY-MM-DD) in `timeZone` (default: the browser's). */
export function storyDay(publishedAt: string, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    new Date(publishedAt),
  );
}

/**
 * The move across the story's day, or null when there is no close before it or none yet
 * on or after it (e.g. a story from today before the close).
 */
export function priceReaction(
  bars: readonly ClosePoint[],
  day: string,
  { before = 6, after = 4 }: { before?: number; after?: number } = {},
): PriceReaction | null {
  const sorted = bars.filter((b) => Number.isFinite(b.close) && b.close > 0).sort((a, b) => a.date.localeCompare(b.date));
  let baseIndex = -1;
  for (let i = 0; i < sorted.length; i++) if (sorted[i].date < day) baseIndex = i;
  const hitIndex = baseIndex + 1;
  if (baseIndex < 0 || hitIndex >= sorted.length) return null;
  const base = sorted[baseIndex];
  const hit = sorted[hitIndex];
  return {
    pct: hit.close / base.close - 1,
    from: base.date,
    to: hit.date,
    window: sorted.slice(Math.max(0, baseIndex - before + 1), hitIndex + after).map((b) => b.close),
  };
}
