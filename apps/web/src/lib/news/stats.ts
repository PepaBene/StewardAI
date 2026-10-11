import type { SourceTrustOut } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { EMPTY_FILTERS, type FeedFilters } from "@/lib/news/feed-filters";
import { eventLabel } from "@/lib/news/labels";

export type NewsStats = components["schemas"]["NewsStats"];
export type SourceHealth = components["schemas"]["SourceHealth"];
export type SourcesHealth = components["schemas"]["SourcesHealth"];

/*
 * The News page's summary panels, from `GET /news/stats`, `GET /news/sources/health` and
 * `GET /sources/trust`: the severity timeline, the categories donut, the source trust bars,
 * and the quick filter presets. Pure rules; the page only renders them.
 */

// ---------------------------------------------------------------------------
// Severity timeline
// ---------------------------------------------------------------------------

/** One day's stories by severity band, stacked bottom-up: alert, highlight, digest, the rest. */
export type SeverityColumn = {
  date: string;
  /** Day of month, for the axis ("14"). */
  label: string;
  alert: number;
  highlight: number;
  digest: number;
  /** Severity 0 (ignored) plus stories not yet classified. */
  other: number;
  total: number;
};

function count(bySeverity: Record<string, number>, key: string): number {
  const n = bySeverity[key];
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? n : 0;
}

export function severityColumns(stats: Pick<NewsStats, "per_day">): SeverityColumn[] {
  return stats.per_day.map((d) => {
    const alert = count(d.by_severity, "3");
    const highlight = count(d.by_severity, "2");
    const digest = count(d.by_severity, "1");
    const other = count(d.by_severity, "0") + Math.max(0, d.unclassified || 0);
    return {
      date: d.date,
      label: String(Number(d.date.slice(8, 10))),
      alert,
      highlight,
      digest,
      other,
      total: alert + highlight + digest + other,
    };
  });
}

/** "18 stories, 14 days" — the timeline's title. */
export function timelineTitle(stats: Pick<NewsStats, "total" | "days">): string {
  return `${stats.total} ${stats.total === 1 ? "story" : "stories"}, ${stats.days} ${stats.days === 1 ? "day" : "days"}`;
}

// ---------------------------------------------------------------------------
// Categories
// ---------------------------------------------------------------------------

/**
 * Classified stories per event type, most first, as donut segments with readable labels.
 * Past `limit` types the tail folds into one "N more types" segment (the event type
 * "other" keeps its own slice, so the two never read alike).
 */
export function categorySegments(stats: Pick<NewsStats, "by_event_type">, limit = 6): { label: string; value: number }[] {
  const all = stats.by_event_type
    .filter((c) => c.count > 0)
    .sort((a, b) => b.count - a.count)
    .map((c) => ({ label: eventLabel(c.event_type), value: c.count }));
  if (all.length <= limit) return all;
  const tail = all.slice(limit - 1);
  return [...all.slice(0, limit - 1), { label: `${tail.length} more types`, value: tail.reduce((s, c) => s + c.value, 0) }];
}

// ---------------------------------------------------------------------------
// Sources: trust (from feedback) and feed health
// ---------------------------------------------------------------------------

export type TrustBar = { host: string; score: number; demoted: boolean; wrong: number; useful: number };

/**
 * Sources your feedback has scored, most trusted first (the design's order), capped at
 * `limit`. Hosts never judged are not listed: they sit at the default of 1.
 */
export function trustBars(rows: readonly SourceTrustOut[], limit = 6): TrustBar[] {
  return [...rows]
    .sort((a, b) => b.score - a.score || b.useful_count - a.useful_count || a.host.localeCompare(b.host))
    .slice(0, limit)
    .map((r) => ({ host: r.host, score: r.score, demoted: r.demoted, wrong: r.wrong_count, useful: r.useful_count }));
}

export type HealthSummary = { healthy: number; enabled: number; total: number; windowHours: number; failing: SourceHealth[] };

/** How many enabled feeds are healthy, and which enabled ones are not. */
export function healthSummary(health: Pick<SourcesHealth, "sources" | "window_hours">): HealthSummary {
  const enabled = health.sources.filter((s) => s.enabled);
  return {
    healthy: enabled.filter((s) => s.healthy).length,
    enabled: enabled.length,
    total: health.sources.length,
    windowHours: health.window_hours,
    failing: enabled.filter((s) => !s.healthy),
  };
}

const RUNG_NAMES: Record<string, string> = { "1": "feed text", "2": "page fetch", "3": "browser", "4": "official API" };

/** "12 feed text, 3 page fetch" for the texts a source stored in a week; "none" when there are none. */
export function fetchMethods(byRung: Record<string, number> | undefined): string {
  const parts = Object.entries(byRung ?? {})
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => Number(a) - Number(b))
    .map(([rung, n]) => `${n} ${RUNG_NAMES[rung] ?? `rung ${rung}`}`);
  return parts.length > 0 ? parts.join(", ") : "none";
}

function minutes(value: number): string {
  if (value < 90) return `${value} min`;
  const hours = value / 60;
  return hours < 48 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}

/** "5 min (95%: 40 min)": how long after publication the app first saw an article; "no data" when unknown. */
export function delayLabel(p50: number | null | undefined, p95: number | null | undefined): string {
  if (p50 === null || p50 === undefined) return "no data";
  return p95 === null || p95 === undefined ? minutes(p50) : `${minutes(p50)} (95%: ${minutes(p95)})`;
}

/** The feed's average answer time: "820 ms" or "1.4 s"; "no data" when unknown. */
export function responseTimeLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "no data";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// ---------------------------------------------------------------------------
// Story rows
// ---------------------------------------------------------------------------

/** How many of the three severity blocks are filled: 3 = alert, 2 = highlight, 1 = digest, 0 otherwise. */
export function severityBlocks(severity: number | null): number {
  if (severity === null || !Number.isFinite(severity)) return 0;
  return Math.max(0, Math.min(3, Math.round(severity)));
}

/** Outlet count as five little bars (1, 2, 3, 4, 5+ outlets). */
export function sourceBars(sourceCount: number, slots = 5): boolean[] {
  const n = Math.max(0, Math.min(slots, Math.floor(sourceCount)));
  return Array.from({ length: slots }, (_, i) => i < n);
}

function dayKey(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/** The row's time column: "17:42" today, "Yday" yesterday, "26 Sep" before. */
export function storyTime(published: Date, now: Date, timeZone?: string): string {
  const today = dayKey(now, timeZone);
  const day = dayKey(published, timeZone);
  if (day === today) {
    return new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hour12: false }).format(published);
  }
  const yesterday = dayKey(new Date(now.getTime() - 86_400_000), timeZone);
  if (day === yesterday) return "Yday";
  return shortDay(published, timeZone);
}

/** "20 Sep" (en-US month abbreviations, so it never reads "Sept"). */
export function shortDay(date: Date, timeZone?: string): string {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone, day: "numeric", month: "short" }).formatToParts(date);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("day")} ${get("month")}`;
}

// ---------------------------------------------------------------------------
// Quick filter presets (the chips in the page header)
// ---------------------------------------------------------------------------

export type FeedPreset = "all" | "high" | "confirmed";

export const PRESETS: { id: FeedPreset; label: string; title: string }[] = [
  { id: "all", label: "All holdings", title: "Every story, no filter" },
  { id: "high", label: "Alert + highlight", title: "Severity 3 (alert) and 2 (highlight) only" },
  { id: "confirmed", label: "Confirmed only", title: "Verified and corroborated stories only" },
];

const sameSet = <T,>(a: readonly T[], b: readonly T[]) => a.length === b.length && a.every((x) => b.includes(x));

export function presetFilters(preset: FeedPreset): FeedFilters {
  switch (preset) {
    case "all":
      return EMPTY_FILTERS;
    case "high":
      return { ...EMPTY_FILTERS, severities: [2, 3] };
    case "confirmed":
      return { ...EMPTY_FILTERS, confidences: ["verified", "corroborated"] };
  }
}

/** The preset these filters are exactly equal to, or null for a custom combination. */
export function activePreset(filters: FeedFilters): FeedPreset | null {
  for (const p of PRESETS) {
    const f = presetFilters(p.id);
    if (
      f.ticker === filters.ticker &&
      f.story === filters.story &&
      sameSet(f.severities, filters.severities) &&
      sameSet(f.confidences, filters.confidences) &&
      sameSet(f.tiers, filters.tiers)
    ) {
      return p.id;
    }
  }
  return null;
}
