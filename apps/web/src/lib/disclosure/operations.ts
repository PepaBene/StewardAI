import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type IngestResult = S["IngestOut"];
export type FilingResult = S["FilingResultOut"];
export type IdentifierSuggestion = S["IdentifierSuggestionOut"];
export type IdentifierJob = S["IdentifierJobOut"];

/** The server's own words for a failed call, shown plainly with the status; never replaced by a vague line. */
export function serverMessage(error: unknown, status: number, what: string): string {
  const detail = typeof error === "object" && error !== null && "detail" in error ? (error as { detail: unknown }).detail : null;
  let text = "";
  if (typeof detail === "string") text = detail;
  else if (Array.isArray(detail))
    text = detail.map((d) => (typeof d === "object" && d !== null && "msg" in d && typeof (d as { msg: unknown }).msg === "string" ? (d as { msg: string }).msg : String(d))).join("; ");
  if (text) return `${what} failed (HTTP ${status}): ${text}`;
  if (status === 401 || status === 403) return "Sign in again (with MFA) and retry.";
  return `${what} failed (HTTP ${status}) and the server gave no reason.`;
}

export function ingestSummary(r: IngestResult): string {
  return `${r.discovered} found, ${r.ingested} stored, ${r.unchanged} already stored unchanged, ${r.failed} failed.`;
}

/** Failed and quarantined filings first, then those with gaps, then the rest; original order kept within each. */
export function orderResults(results: readonly FilingResult[]): FilingResult[] {
  const rank = (f: FilingResult) => (f.status === "failed" ? 0 : f.quarantined ? 1 : f.gaps.length ? 2 : 3);
  return results
    .map((f, i) => ({ f, i }))
    .sort((a, b) => rank(a.f) - rank(b.f) || a.i - b.i)
    .map((x) => x.f);
}

const IDENTIFIER_TYPE_LABEL: Record<string, string> = { lei: "LEI", figi: "FIGI", share_class_figi: "Share-class FIGI" };
export const identifierTypeLabel = (t: string): string => IDENTIFIER_TYPE_LABEL[t.toLowerCase()] ?? t;

export type StoredSuggestion = S["StoredSuggestionOut"];
export type MatchStatus = StoredSuggestion["match_status"];

export const MATCH_STATUS_LABEL: Record<MatchStatus, string> = { suggested: "Awaiting a decision", confirmed: "Confirmed", rejected: "Rejected" };
export const NOTE_MIN = 10;

/** A name-only or ambiguous match cannot be confirmed without a written reason. */
export const confirmNeedsNote = (s: { name_only: boolean; ambiguous: boolean }): boolean => s.name_only || s.ambiguous;

/** True when the note is long enough (trimmed) to confirm this row; rows without risk flags need none. */
export const confirmAllowed = (s: { name_only: boolean; ambiguous: boolean }, note: string): boolean => !confirmNeedsNote(s) || note.trim().length >= NOTE_MIN;

/** Flags a reviewer must see before trusting a suggestion, in the order of risk. */
export function suggestionFlags(s: { name_only: boolean; ambiguous: boolean }): string[] {
  const flags: string[] = [];
  if (s.ambiguous) flags.push("Ambiguous: more than one candidate fits");
  if (s.name_only) flags.push("Matched on name only");
  return flags;
}

/** Flagged suggestions first, then by score descending. */
export function orderSuggestions<T extends { name_only: boolean; ambiguous: boolean; score: number | null }>(list: readonly T[]): T[] {
  return [...list].sort((a, b) => suggestionFlags(b).length - suggestionFlags(a).length || (b.score ?? -1) - (a.score ?? -1));
}

export const formatScore = (score: number | null): string => (score == null ? "not scored" : score <= 1 ? `${Math.round(score * 100)}%` : score.toFixed(1));
