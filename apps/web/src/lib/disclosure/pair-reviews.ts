import type { components } from "@/lib/api/schema";

/*
 * Reviewing snapshot pairs (plan P7.8): a person checks whether the code treated a pair correctly,
 * either by a number that follows from the two files or by refusing to compare them with a stated
 * reason. Revision alerts stay off until enough distinct pairs are labelled correct.
 */

type S = components["schemas"];
export type PairReviewItem = S["PairReviewItemOut"];
export type PairReviews = S["PairReviewsOut"];

/** Pairs still to review first, then the rest, each group keeping the newest later file first. */
export const reviewOrder = (pairs: readonly PairReviewItem[]): PairReviewItem[] =>
  [...pairs].sort((a, b) => Number(a.reviewed) - Number(b.reviewed) || Date.parse(b.later_known_at) - Date.parse(a.later_known_at) || a.earlier_id - b.earlier_id);

/** How far the gate is: pairs still needed and whether the bar was met. Never negative. */
export function gateProgress(gate: PairReviews["revision_gate"]): { remaining: number; text: string } {
  const remaining = Math.max(0, gate.needed - gate.reviewed);
  return { remaining, text: gate.met ? gate.reason : remaining > 0 ? `${remaining} more ${remaining === 1 ? "pair" : "pairs"} to review. ${gate.reason}.` : gate.reason };
}

/** What the code did with a pair, in words a reviewer can check against the two files. */
export function outcomeText(p: PairReviewItem): string {
  if (!p.comparable) return `Not compared: ${p.break_reasons.map((r) => r.replaceAll("_", " ")).join(", ") || "reason not recorded"}.`;
  if (p.change == null) return p.note ?? "Compared, but no change could be stated.";
  const pct = p.change_pct != null ? ` (${p.change_pct}%)` : p.pct_note ? ` (${p.pct_note})` : "";
  return `${p.metric}${p.segment ? `, ${p.segment}` : ""}: ${p.earlier_value ?? "not stated"} to ${p.later_value ?? "not stated"}, change ${p.change}${pct}.`;
}
