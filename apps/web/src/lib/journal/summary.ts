import type { JournalEntry } from "./journal";

/*
 * The Journal answer strip (spec C.4): how many decisions are written down, how many reviews
 * are due, and how many of the last 90 days' decisions said why.
 */

export type JournalSummary = {
  decisions: number;
  /** Decisions past their review date with no review entry yet. */
  reviewsDue: number;
  /** Decisions in the last 90 days. */
  recent: number;
  /** Share of recent decisions with a conviction score, 0..1; null with none recent. */
  recentWithConviction: number | null;
};

export function journalSummary(entries: readonly JournalEntry[], today: string): JournalSummary {
  const reviewed = new Set(entries.filter((e) => e.parent_id !== null).map((e) => e.parent_id));
  const decisions = entries.filter((e) => e.decision !== "review");
  const cutoff = new Date(Date.parse(`${today}T00:00:00Z`) - 90 * 86_400_000).toISOString().slice(0, 10);
  const recent = decisions.filter((e) => e.decided_on >= cutoff);
  return {
    decisions: decisions.length,
    reviewsDue: decisions.filter((e) => e.review_date !== null && e.review_date <= today && !reviewed.has(e.id)).length,
    recent: recent.length,
    recentWithConviction: recent.length === 0 ? null : recent.filter((e) => e.conviction !== null).length / recent.length,
  };
}
