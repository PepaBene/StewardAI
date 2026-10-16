/*
 * Pure helpers for the T3 feed list: day grouping, folding repeated headlines into
 * "n similar", and counting entries per type for the filter chips.
 */

export type FeedEntry = {
  id: string;
  /** When it happened, ISO 8601. Entries should arrive newest first. */
  at: string;
  /** The headline; entries with the same normalised text fold together. */
  title: string;
  /** The type the filter chips count, e.g. "holding" or "open". */
  kind: string;
  unread?: boolean;
};

export type FeedGroup<T extends FeedEntry> = {
  /** The newest entry of the group. */
  head: T;
  /** The older entries with the same headline, newest first. */
  similar: T[];
};

export type FeedDay<T extends FeedEntry> = { key: string; label: string; groups: FeedGroup<T>[] };

/** Lower-case, no punctuation, single spaces: "Fed holds rates!" and "fed holds  rates" match. */
export function normaliseTitle(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** The local calendar day, "2026-03-04". */
export function dayKey(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown";
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

export function dayLabel(key: string, now: Date): string {
  if (key === "unknown") return "Date unknown";
  if (key === dayKey(now.toISOString())) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (key === dayKey(yesterday.toISOString())) return "Yesterday";
  const [y, m, d] = key.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: y === now.getFullYear() ? undefined : "numeric" }).format(new Date(y, m - 1, d));
}

/** Entries per type, in first-seen order. */
export function countKinds(entries: readonly FeedEntry[]): { kind: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const e of entries) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
  return [...counts].map(([kind, count]) => ({ kind, count }));
}

/**
 * Groups entries by day and folds repeated headlines within a day: the newest stays as the
 * head and the rest are listed as "similar". Day order follows the order entries arrive in.
 */
export function buildDays<T extends FeedEntry>(entries: readonly T[], now: Date, fold = true): FeedDay<T>[] {
  const days: FeedDay<T>[] = [];
  const byDay = new Map<string, { day: FeedDay<T>; seen: Map<string, FeedGroup<T>> }>();
  for (const e of entries) {
    const key = dayKey(e.at);
    let slot = byDay.get(key);
    if (!slot) {
      slot = { day: { key, label: dayLabel(key, now), groups: [] }, seen: new Map() };
      byDay.set(key, slot);
      days.push(slot.day);
    }
    const norm = normaliseTitle(e.title);
    const existing = fold && norm ? slot.seen.get(norm) : undefined;
    if (existing) {
      existing.similar.push(e);
    } else {
      const group: FeedGroup<T> = { head: e, similar: [] };
      slot.day.groups.push(group);
      if (norm) slot.seen.set(norm, group);
    }
  }
  return days;
}

/** "3 similar" / "1 similar". */
export function similarLabel(n: number): string {
  return `${n} similar`;
}
