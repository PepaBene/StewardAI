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
  /** One event type (or "unclassified"), as the API names it. */
  eventType?: string | null;
  /** Also stories reached only through a rival, supplier, customer or watch topic. */
  related?: boolean;
  /** Also stories the classifier judged not worth a digest line (severity 0). */
  ignored?: boolean;
};

export const EMPTY_FILTERS: FeedFilters = {
  ticker: null,
  severities: [],
  confidences: [],
  tiers: [],
  story: null,
  eventType: null,
  related: false,
  ignored: false,
};

/**
 * The GET /news scope: by default only stories about a holding itself and worth at least a
 * digest line (or not classified yet); a single story is always shown whole.
 */
export function feedScope(filters: FeedFilters): {
  direct: boolean;
  severity: number[] | undefined;
  minSeverity: number | undefined;
} {
  const severity = filters.severities.length ? filters.severities : undefined;
  if (filters.story) return { direct: false, severity, minSeverity: undefined };
  return { direct: !filters.related, severity, minSeverity: severity || filters.ignored ? undefined : 1 };
}

/** The stories the default scope leaves out are shown by turning both "also show" switches on. */
export function showAll(filters: FeedFilters): FeedFilters {
  return { ...filters, story: null, related: true, ignored: true };
}

function ints(values: string[], min: number, max: number): number[] {
  const parsed = values.map(Number).filter((n) => Number.isInteger(n) && n >= min && n <= max);
  return [...new Set(parsed)].sort((a, b) => a - b);
}

export function parseFilters(params: URLSearchParams): FeedFilters {
  const ticker = params.get("ticker");
  const story = Number(params.get("story"));
  const eventType = params.get("event_type");
  return {
    ticker: ticker && /^[A-Za-z0-9._-]{1,32}$/.test(ticker) ? ticker : null,
    severities: ints(params.getAll("severity"), 0, 3),
    confidences: [...new Set(params.getAll("confidence"))].filter((c): c is Confidence =>
      (CONFIDENCES as readonly string[]).includes(c),
    ),
    tiers: ints(params.getAll("tier"), 1, 3),
    story: Number.isInteger(story) && story > 0 ? story : null,
    eventType: eventType && /^[a-z_]{1,40}$/.test(eventType) ? eventType : null,
    related: params.get("related") === "1",
    ignored: params.get("ignored") === "1",
  };
}

export function filtersToSearch(filters: FeedFilters): string {
  const params = new URLSearchParams();
  if (filters.ticker) params.set("ticker", filters.ticker);
  filters.severities.forEach((s) => params.append("severity", String(s)));
  filters.confidences.forEach((c) => params.append("confidence", c));
  filters.tiers.forEach((t) => params.append("tier", String(t)));
  if (filters.story) params.set("story", String(filters.story));
  if (filters.eventType) params.set("event_type", filters.eventType);
  if (filters.related) params.set("related", "1");
  if (filters.ignored) params.set("ignored", "1");
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
    Boolean(filters.eventType) ||
    filters.severities.length > 0 ||
    filters.confidences.length > 0 ||
    filters.tiers.length > 0
  );
}

type LinkLike = { ticker: string; via: string };

const INDIRECT = new Set(["competitor", "supplier", "customer", "constituent", "topic"]);

/**
 * A story's holdings: those it is about (named, or the source is theirs) and those it only
 * reaches through a rival, supplier, customer or topic, each ticker once, the story's own first.
 */
export function splitLinks<T extends LinkLike>(links: readonly T[], storyTicker: string | null | undefined): { about: T[]; related: T[] } {
  const about = new Map<string, T>();
  const related = new Map<string, T>();
  for (const link of links) {
    if (!INDIRECT.has(link.via)) about.set(link.ticker, link);
  }
  for (const link of links) {
    if (INDIRECT.has(link.via) && !about.has(link.ticker) && !related.has(link.ticker)) related.set(link.ticker, link);
  }
  const first = (a: T, b: T) => Number(b.ticker === storyTicker) - Number(a.ticker === storyTicker);
  return { about: [...about.values()].sort(first), related: [...related.values()].sort(first) };
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
