import type { ClaimSource, DisputedStory, StoryResolution } from "@/lib/api/client";

/*
 * Display rules for disputed stories: the story's own sources (the claim) beside the
 * verified sources that contradict it, each with its tier, and the owner's resolution.
 */

export const DISPUTED_HREF = "/news/disputed";
export const NOTE_MAX = 500;

export function disputedStoryAnchor(id: number): string {
  return `story-${id}`;
}

export function disputedStoryHref(id: number): string {
  return `${DISPUTED_HREF}#${disputedStoryAnchor(id)}`;
}

/** The outlet to show for a source: its stored outlet, else the URL's host. */
export function sourceOutlet(source: Pick<ClaimSource, "outlet" | "url">): string {
  if (source.outlet) return source.outlet;
  try {
    return new URL(source.url).hostname.replace(/^www\./, "");
  } catch {
    return source.url;
  }
}

/** Most trusted first (tier 1), then earliest published; undated sources last. */
export function sortSources<T extends Pick<ClaimSource, "tier" | "published_at" | "url">>(sources: T[]): T[] {
  return [...sources].sort((a, b) => {
    if (a.tier !== b.tier) return a.tier - b.tier;
    if (a.published_at && b.published_at) return a.published_at.localeCompare(b.published_at);
    if (a.published_at) return -1;
    if (b.published_at) return 1;
    return a.url.localeCompare(b.url);
  });
}

/** Open stories first (newest activity first), then resolved ones (newest resolution first). */
export function splitDisputed(stories: DisputedStory[]): { open: DisputedStory[]; resolved: DisputedStory[] } {
  const open = stories.filter((s) => !s.resolved_at).sort((a, b) => b.last_seen.localeCompare(a.last_seen));
  const resolved = stories
    .filter((s) => s.resolved_at)
    .sort((a, b) => (b.resolved_at ?? "").localeCompare(a.resolved_at ?? ""));
  return { open, resolved };
}

export type ResolutionState =
  | { kind: "open"; label: string }
  | { kind: "confirmed" | "false"; label: string; note: string | null; at: string };

export function resolutionState(story: Pick<DisputedStory, "resolution" | "resolution_note" | "resolved_at">): ResolutionState {
  if (!story.resolution || !story.resolved_at) return { kind: "open", label: "Open: waiting for your decision" };
  return {
    kind: story.resolution,
    label: story.resolution === "confirmed" ? "Resolved: confirmed by you" : "Resolved: marked false by you",
    note: story.resolution_note,
    at: story.resolved_at,
  };
}

/** The request body for a resolution; an empty note is sent as null. */
export function resolutionBody(resolution: StoryResolution["resolution"], note: string): StoryResolution {
  const trimmed = note.trim();
  return { resolution, note: trimmed ? trimmed : null };
}

/** Why a note cannot be sent, or null. */
export function noteError(note: string): string | null {
  const length = note.trim().length;
  return length > NOTE_MAX ? `Keep the note to ${NOTE_MAX} characters (now ${length}).` : null;
}

/** Replace a story in the list with the API's updated copy. */
export function applyResolution(stories: DisputedStory[], updated: DisputedStory[]): DisputedStory[] {
  const byId = new Map(updated.map((s) => [s.id, s]));
  return stories.map((s) => byId.get(s.id) ?? s);
}

/** What resolving does, shown next to the buttons. */
export const RESOLUTION_EFFECTS: Record<StoryResolution["resolution"], string> = {
  confirmed: "It happened: the story is marked confirmed and routed on the next news run (it may alert).",
  false: "It did not happen: the story is marked false and never alerts.",
};
