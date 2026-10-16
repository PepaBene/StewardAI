/*
 * Feed shaping for News (spec C.5): stories under day headers, and event-type chips counted
 * over the stories on the page.
 */

type StoryItem = { published_at: string; event_type?: string | null; story: { id: number; event_type?: string | null } };

/** The story's event type: the story's own, else its lead item's; null when unclassified. */
export function storyEventType(group: readonly StoryItem[]): string | null {
  return group[0]?.story.event_type ?? group[0]?.event_type ?? null;
}

const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** "Today", "Yesterday", else "Mon 12 Oct" (local time). */
export function dayLabel(iso: string, now: Date): string {
  const d = new Date(iso);
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const k = dayKey(d);
  if (k === today) return "Today";
  if (k === yesterday) return "Yesterday";
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short" }).format(d);
}

/** Story groups bucketed by the local day of their newest item, newest day first, order kept. */
export function groupByDay<G extends readonly StoryItem[]>(groups: readonly G[]): { day: string; at: string; groups: G[] }[] {
  const out: { day: string; at: string; groups: G[] }[] = [];
  for (const g of groups) {
    const at = g.reduce((m, i) => (i.published_at > m ? i.published_at : m), g[0]?.published_at ?? "");
    const day = at ? dayKey(new Date(at)) : "";
    const last = out[out.length - 1];
    if (last && last.day === day) last.groups.push(g);
    else out.push({ day, at, groups: [g] });
  }
  return out;
}

/** Event-type counts over story groups, most common first; unclassified as key "". */
export function eventTypeCounts(groups: readonly (readonly StoryItem[])[]): { type: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const g of groups) {
    const t = storyEventType(g) ?? "";
    counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return [...counts.entries()].map(([type, count]) => ({ type, count })).sort((a, b) => b.count - a.count || a.type.localeCompare(b.type));
}
