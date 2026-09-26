import type {
  EventType,
  FeedbackIn,
  FeedbackOut,
  FeedbackRating,
  FeedbackTargetKind,
} from "@/lib/api/client";
import { parseCorrectedValue } from "@/lib/evidence/review";
import { EVENT_TYPES } from "@/lib/news/labels";

/*
 * The owner's feedback (Defence layer 6): *wrong*, *irrelevant* or *useful* on every fact,
 * verdict, alert, news item, story and report sentence (`POST /feedback`). These helpers
 * build and check the request, address report sentences inside a stored report body, pick
 * the current mark of each item from `GET /feedback`, and word what a mark did.
 */

export const NOTE_MAX = 1000;
export const VALUE_AS_WRITTEN_MAX = 40;
export const SNIPPET_MAX = 1000;

export const RATINGS = ["wrong", "irrelevant", "useful"] as const satisfies readonly FeedbackRating[];

export const RATING_LABELS: Record<FeedbackRating, { label: string; marked: string; description: string }> = {
  wrong: {
    label: "Wrong",
    marked: "Marked wrong",
    description: "It is incorrect. A fact is quarantined and its verdicts re-judged; the source's trust drops.",
  },
  irrelevant: {
    label: "Irrelevant",
    marked: "Marked irrelevant",
    description: "Correct, but not useful here. Trust is not changed.",
  },
  useful: {
    label: "Useful",
    marked: "Marked useful",
    description: "Right and useful. The source's trust rises.",
  },
};

export const TARGET_LABELS: Record<FeedbackTargetKind, string> = {
  fact: "figure",
  verdict: "verdict",
  alert: "alert",
  news_item: "news item",
  story: "story",
  report_sentence: "report sentence",
};

export type FeedbackTarget = {
  kind: FeedbackTargetKind;
  /** The fact, verdict, alert, news item, story or report id. */
  id: number;
  /** Report sentences only: where the sentence sits in the stored report body. */
  sentencePath?: string | null;
};

// ---------------------------------------------------------------------------
// Report sentences: paths into the stored body arrays, as the API returns them
// ---------------------------------------------------------------------------

const PATH = /^[a-z_]+(\/[0-9a-z_]+)*$/;

function index(i: number): number {
  if (!Number.isSafeInteger(i) || i < 0) throw new Error(`Not a sentence index: ${i}`);
  return i;
}

/** A digest overview sentence: "overview/<i>". */
export function digestOverviewPath(sentence: number): string {
  return `overview/${index(sentence)}`;
}

/** A sentence of a digest section's summary: "sections/<s>/summary/<i>". */
export function digestSectionPath(section: number, sentence: number): string {
  return `sections/${index(section)}/summary/${index(sentence)}`;
}

/** An impact note sentence: "sentences/<i>". */
export function noteSentencePath(sentence: number): string {
  return `sentences/${index(sentence)}`;
}

export function isSentencePath(path: string): boolean {
  return PATH.test(path);
}

/** Stable key of one target: "fact:12", "report_sentence:7:overview/0". */
export function markKey(target: FeedbackTarget | Pick<FeedbackOut, "target_kind" | "target_id" | "sentence_path">): string {
  if ("kind" in target) return `${target.kind}:${target.id}${target.sentencePath ? `:${target.sentencePath}` : ""}`;
  return `${target.target_kind}:${target.target_id}${target.sentence_path ? `:${target.sentence_path}` : ""}`;
}

/** The newest mark per target (the list may come in any order). */
export function latestMarks(feedback: FeedbackOut[]): Map<string, FeedbackOut> {
  const out = new Map<string, FeedbackOut>();
  for (const f of feedback) {
    const key = markKey(f);
    const seen = out.get(key);
    if (!seen || f.created_at > seen.created_at || (f.created_at === seen.created_at && f.id > seen.id)) out.set(key, f);
  }
  return out;
}

// ---------------------------------------------------------------------------
// The request
// ---------------------------------------------------------------------------

/** Only a wrong fact or news item can go to an evaluation set (golden or labelled). */
export function canAddToEval(kind: FeedbackTargetKind, rating: FeedbackRating | null): boolean {
  return rating === "wrong" && (kind === "fact" || kind === "news_item");
}

export type FeedbackDraft = {
  rating: FeedbackRating | null;
  note: string;
  addToEval: boolean;
  /** Facts: the right value in the printed scale, as typed ("94,036"). */
  correctedValue: string;
  correctedValueAsWritten: string;
  correctedSnippet: string;
  /** News items: the right classification. */
  label: { eventType: EventType | ""; affectedTicker: string; relevant: boolean; rumour: boolean };
};

export type FeedbackErrors = Partial<
  Record<"rating" | "note" | "target" | "correctedValue" | "correctedValueAsWritten" | "correctedSnippet" | "eventType" | "affectedTicker", string>
>;

export function emptyDraft(rating: FeedbackRating | null = null): FeedbackDraft {
  return {
    rating,
    note: "",
    addToEval: false,
    correctedValue: "",
    correctedValueAsWritten: "",
    correctedSnippet: "",
    label: { eventType: "", affectedTicker: "", relevant: true, rumour: false },
  };
}

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;

/** The request body for `POST /feedback`, or the message to show next to each field. */
export function buildFeedback(
  target: FeedbackTarget,
  draft: FeedbackDraft,
): { ok: true; body: FeedbackIn } | { ok: false; errors: FeedbackErrors } {
  const errors: FeedbackErrors = {};
  if (!draft.rating) errors.rating = "Choose wrong, irrelevant or useful.";
  if (!Number.isSafeInteger(target.id) || target.id < 1) errors.target = "Nothing to mark: the item has no id.";
  const path = target.sentencePath ?? null;
  if (target.kind === "report_sentence") {
    if (!path || !isSentencePath(path)) errors.target = "A report sentence needs its place in the report.";
  } else if (path) {
    errors.target = "Only a report sentence has a sentence path.";
  }
  const note = draft.note.trim();
  if (note.length > NOTE_MAX) errors.note = `Keep the note to ${NOTE_MAX} characters (${note.length} now).`;

  const toEval = draft.addToEval && canAddToEval(target.kind, draft.rating);
  const body: FeedbackIn = {
    target_kind: target.kind,
    target_id: target.id,
    rating: draft.rating ?? "useful",
    add_to_eval: toEval,
  };
  if (path && target.kind === "report_sentence") body.sentence_path = path;
  if (note) body.note = note;

  if (toEval && target.kind === "fact") {
    const value = parseCorrectedValue(draft.correctedValue);
    if (!draft.correctedValue.trim()) errors.correctedValue = "Enter the right value, as printed.";
    else if (value === null) errors.correctedValue = "Enter a number, e.g. 94036 or 1.57.";
    else body.corrected_value = value;
    const written = draft.correctedValueAsWritten.trim();
    if (written.length > VALUE_AS_WRITTEN_MAX) {
      errors.correctedValueAsWritten = `At most ${VALUE_AS_WRITTEN_MAX} characters.`;
    } else if (written) body.corrected_value_as_written = written;
    const snippet = draft.correctedSnippet.trim();
    if (snippet.length > SNIPPET_MAX) errors.correctedSnippet = `At most ${SNIPPET_MAX} characters (${snippet.length} now).`;
    else if (snippet) body.corrected_snippet = snippet;
  }
  if (toEval && target.kind === "news_item") {
    const { eventType, affectedTicker, relevant, rumour } = draft.label;
    const ticker = affectedTicker.trim();
    if (!eventType || !(EVENT_TYPES as readonly string[]).includes(eventType)) errors.eventType = "Choose the right event type.";
    if (ticker && !TICKER.test(ticker)) errors.affectedTicker = "A ticker such as AAPL_US_EQ, or empty for none.";
    if (!errors.eventType && !errors.affectedTicker) {
      body.corrected_label = { event_type: eventType as EventType, affected_ticker: ticker || null, relevant, rumour };
    }
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, body };
}

/** What a failed `POST /feedback` means for the owner. */
export function describeFeedbackFailure(status: number, error: unknown): string {
  const detail = error && typeof error === "object" && "detail" in error ? (error as { detail: unknown }).detail : null;
  const text = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d) => d?.msg).filter(Boolean).join("; ") : "";
  if (status === 404) return `Not found: ${text || "the item no longer exists."}`;
  if (status === 409) return text || "The item cannot take this mark.";
  if (status === 422) return `Not accepted: ${text || "the request was invalid."}`;
  if (status === 0) return `Not sent: ${text || "the API could not be reached."}`;
  return `Not saved (HTTP ${status})${text ? `: ${text}` : "."}`;
}

// ---------------------------------------------------------------------------
// What a mark did
// ---------------------------------------------------------------------------

export type EffectPart = { text: string; href?: string };

function score(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/**
 * The `effects` of a mark, in words: "Quarantined", "verdicts re-judged (job #n)",
 * "ir.test trust 0.8", "added to the eval set (candidate #n)". Unknown keys are ignored.
 */
export function effectParts(effects: Record<string, unknown> | null | undefined): EffectPart[] {
  if (!effects) return [];
  const parts: EffectPart[] = [];
  if (effects.quarantined === true) parts.push({ text: "Quarantined" });
  const job = effects.verdict_job_id;
  if (typeof job === "number") parts.push({ text: `verdicts re-judged (job #${job})`, href: `/jobs/${job}` });
  const trust = effects.trust;
  if (trust && typeof trust === "object") {
    for (const [host, value] of Object.entries(trust as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b))) {
      const n = typeof value === "number" ? value : Number(value);
      if (Number.isFinite(n)) parts.push({ text: `${host} trust ${score(n)}` });
    }
  }
  if ("eval_candidate_id" in effects) {
    const id = effects.eval_candidate_id;
    parts.push(
      typeof id === "number"
        ? { text: `added to the eval set (candidate #${id})`, href: "/activity#eval-candidates" }
        : { text: "already in the eval set" },
    );
  }
  return parts;
}

/** "Quarantined; verdicts re-judged (job #9); ir.test trust 0.8", or null when nothing changed. */
export function summarizeEffects(effects: Record<string, unknown> | null | undefined): string | null {
  const parts = effectParts(effects);
  return parts.length ? parts.map((p) => p.text).join("; ") : null;
}
