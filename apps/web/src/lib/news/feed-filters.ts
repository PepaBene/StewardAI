import type { Confidence } from "@/lib/api/client";
import { CONFIDENCES } from "@/lib/news/labels";

/*
 * The news feed's filters, kept in the page URL (so a filtered view can be linked and
 * reloaded) and passed to GET /news unchanged. Repeated values mean "any of these".
 */
export type FeedFilters = {
  ticker: string | null;
  severities: number[];
  confidences: Confidence[];
  tiers: number[];
  story: number | null;
};

export const EMPTY_FILTERS: FeedFilters = {
  ticker: null,
  severities: [],
  confidences: [],
  tiers: [],
  story: null,
};

function ints(values: string[], min: number, max: number): number[] {
  const parsed = values.map(Number).filter((n) => Number.isInteger(n) && n >= min && n <= max);
  return [...new Set(parsed)].sort((a, b) => a - b);
}

export function parseFilters(params: URLSearchParams): FeedFilters {
  const ticker = params.get("ticker");
  const story = Number(params.get("story"));
  return {
    ticker: ticker && /^[A-Za-z0-9._-]{1,32}$/.test(ticker) ? ticker : null,
    severities: ints(params.getAll("severity"), 0, 3),
    confidences: [...new Set(params.getAll("confidence"))].filter((c): c is Confidence =>
      (CONFIDENCES as readonly string[]).includes(c),
    ),
    tiers: ints(params.getAll("tier"), 1, 3),
    story: Number.isInteger(story) && story > 0 ? story : null,
  };
}

export function filtersToSearch(filters: FeedFilters): string {
  const params = new URLSearchParams();
  if (filters.ticker) params.set("ticker", filters.ticker);
  filters.severities.forEach((s) => params.append("severity", String(s)));
  filters.confidences.forEach((c) => params.append("confidence", c));
  filters.tiers.forEach((t) => params.append("tier", String(t)));
  if (filters.story) params.set("story", String(filters.story));
  const text = params.toString();
  return text ? `?${text}` : "";
}

export function toggle<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

export function isFiltered(filters: FeedFilters): boolean {
  return (
    filters.ticker !== null ||
    filters.story !== null ||
    filters.severities.length > 0 ||
    filters.confidences.length > 0 ||
    filters.tiers.length > 0
  );
}

/** Consecutive items of one story, shown as one row with its copies. */
export function groupByStory<T extends { story: { id: number } }>(items: T[]): T[][] {
  const groups: T[][] = [];
  const index = new Map<number, T[]>();
  for (const item of items) {
    const existing = index.get(item.story.id);
    if (existing) existing.push(item);
    else {
      const group = [item];
      index.set(item.story.id, group);
      groups.push(group);
    }
  }
  return groups;
}

/** A story's items with its lead first: the most trusted source, then the earliest. */
export function byLead<T extends { tier: number; published_at: string; id: number }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => a.tier - b.tier || a.published_at.localeCompare(b.published_at) || a.id - b.id,
  );
}
