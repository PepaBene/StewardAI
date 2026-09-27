import type { ActionOut, ChatSentence, ConversationDetail, JobStatus, MessageOut, Source } from "@/lib/api/client";
import { primarySourceLink, sourceLinks, type SourceLinks } from "@/lib/sources/sources";

/*
 * The chat panel (`/chat/conversations...`). An answer is written by a job: posting a
 * message returns 202 at once, and the conversation is polled every ~2 s while a message
 * is pending. Answers are cited sentence by sentence; their sources open the same pages as
 * the rest of the app (app_path: /evidence/<id>, /holdings/<t>, /reports/<id>,
 * /research/<id>, /reviews/<id>) or the source's own URL. Proposed actions start nothing
 * until confirmed.
 */

/** Dispatched on window to open the slide-over chat panel (palette, header button). */
export const OPEN_CHAT_EVENT = "steward:open-chat";

export const MESSAGE_MAX = 100_000;
export const POLL_MS = 2000;
/** Stop polling a pending answer after this long (its job page says what happened). */
export const POLL_LIMIT_MS = 5 * 60 * 1000;
/** After this long, also read the job's status, so a failed run stops the wait at once. */
export const JOB_CHECK_AFTER_MS = 10 * 1000;

export function chatHref(conversationId?: number | null): string {
  return conversationId ? `/chat?c=${conversationId}` : "/chat";
}

export function validateMessage(content: string): string | null {
  const text = content.trim();
  if (!text) return "Type a question.";
  if (text.length > MESSAGE_MAX) return `At most ${MESSAGE_MAX} characters (${text.length} now).`;
  return null;
}

// ---------------------------------------------------------------------------
// Polling
// ---------------------------------------------------------------------------

/** The newest message still waiting for its answer, if any. */
export function pendingMessage(messages: readonly MessageOut[]): MessageOut | null {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].status === "pending") return messages[i];
  return null;
}

export type PollDecision =
  | { kind: "idle" }
  | { kind: "poll"; delayMs: number; checkJob: boolean }
  | { kind: "failed"; jobId: number | null; reason: string }
  | { kind: "timed-out"; jobId: number | null };

/**
 * Whether to poll the conversation again. `waitingSince` is when this client started
 * waiting for the pending message (ms); `jobStatus` the answer job's status when known.
 */
export function pollDecision(
  detail: Pick<ConversationDetail, "messages"> | null,
  { waitingSince, now, jobStatus, jobError }: { waitingSince: number | null; now: number; jobStatus?: JobStatus | null; jobError?: string | null },
): PollDecision {
  const pending = detail ? pendingMessage(detail.messages) : null;
  if (!pending) return { kind: "idle" };
  const jobId = pending.job_id;
  if (jobStatus === "failed") return { kind: "failed", jobId, reason: jobError?.trim() || "the answer job failed" };
  const since = waitingSince ?? now;
  if (now - since > POLL_LIMIT_MS) return { kind: "timed-out", jobId };
  return { kind: "poll", delayMs: POLL_MS, checkJob: jobId !== null && now - since >= JOB_CHECK_AFTER_MS };
}

// ---------------------------------------------------------------------------
// Answers
// ---------------------------------------------------------------------------

export type SentenceView =
  | { kind: "cited"; sources: CitedSource[] }
  | { kind: "no_source" }
  | { kind: "unsupported"; reason: string; sources: CitedSource[] };

export type CitedSource = {
  id: string;
  number: number | null;
  source: Source | null;
  /** Where the citation goes: the app page, else the external page, else nothing. */
  href: string | null;
  external: boolean;
  links: SourceLinks;
};

/** Number the answer's sources 1..n in their stored order. */
export function sourceNumbering(sources: readonly Source[]): Map<string, { number: number; source: Source }> {
  const out = new Map<string, { number: number; source: Source }>();
  for (const s of sources) if (!out.has(s.id)) out.set(s.id, { number: out.size + 1, source: s });
  return out;
}

export function citedSources(ids: readonly string[], numbering: Map<string, { number: number; source: Source }>): CitedSource[] {
  return [...new Set(ids)].map((id) => {
    const hit = numbering.get(id) ?? null;
    const link = hit ? primarySourceLink(hit.source) : null;
    return {
      id,
      number: hit?.number ?? null,
      source: hit?.source ?? null,
      href: link?.href ?? null,
      external: link?.external ?? false,
      links: hit ? sourceLinks(hit.source) : { internal: null, external: null },
    };
  });
}

/**
 * How to show one sentence: its citations; or "no source" when nothing checkable cites it;
 * or unsupported with the reason (a figure in it is not in its sources).
 */
export function sentenceView(sentence: ChatSentence, numbering: Map<string, { number: number; source: Source }>): SentenceView {
  const sources = citedSources(sentence.source_ids, numbering);
  if (sentence.unsupported && sentence.unsupported.trim()) return { kind: "unsupported", reason: sentence.unsupported.trim(), sources };
  if (sentence.no_source || sources.length === 0) return { kind: "no_source" };
  return { kind: "cited", sources };
}

// ---------------------------------------------------------------------------
// Proposed actions
// ---------------------------------------------------------------------------

/** The actions an assistant message proposed (its answer lists their ids). */
export function actionsFor(message: MessageOut, actions: readonly ActionOut[]): ActionOut[] {
  const ids = new Set(message.answer?.actions ?? []);
  return actions.filter((a) => a.message_id === message.id || ids.has(a.id));
}

/** Actions not attached to any message shown (still listed, so none is lost). */
export function orphanActions(messages: readonly MessageOut[], actions: readonly ActionOut[]): ActionOut[] {
  const shown = new Set(messages.flatMap((m) => actionsFor(m, actions).map((a) => a.id)));
  return actions.filter((a) => !shown.has(a.id));
}

export function actionLabel(action: Pick<ActionOut, "kind" | "params">): string {
  const ticker = typeof action.params.ticker === "string" ? action.params.ticker : null;
  const profile = typeof action.params.profile === "string" ? action.params.profile : null;
  switch (action.kind) {
    case "earnings_check":
      return `Run an earnings check${ticker ? ` for ${ticker}` : ""}`;
    case "research":
      return `Research ${ticker ?? "a ticker"}${profile ? ` (profile ${profile})` : ""}`;
    case "portfolio_sync":
      return "Sync the portfolio from Trading212";
    default:
      return String(action.kind).replaceAll("_", " ");
  }
}

export const ACTION_STATUS: Record<ActionOut["status"], string> = {
  proposed: "Proposed: nothing starts until you confirm",
  confirmed: "Confirmed",
  cancelled: "Cancelled",
};
