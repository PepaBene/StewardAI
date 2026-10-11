/**
 * Pure helpers for the research plan editor: the owner reviews a drafted plan (model-written or
 * code-built), reorders, removes, rewords and toggles source groups, then approves it. The
 * server applies the same checks; the local ones only save a round trip. Types are structural so
 * the editor works before the API client types are regenerated.
 */

export const SOURCE_GROUP_LABELS: Record<string, string> = {
  portfolio: "Portfolio and strategy",
  thesis: "Thesis and earlier reports",
  financials: "Financials and prices",
  filings: "Filings and company facts",
  news: "Stored news",
  macro: "Macro and country",
  freshness: "Data freshness",
  web: "Web search",
};

export const SOURCE_GROUPS = Object.keys(SOURCE_GROUP_LABELS);

export type DraftQuestion = {
  id: string;
  section: string | null;
  question: string;
  internal_tools: string[];
  web_intent: string | null;
  source_groups: string[];
  label?: string | null;
};

export type DraftPlan = {
  questions: DraftQuestion[];
  authored_by?: "code" | "model";
  fallback_reason?: string | null;
  edited_by_owner?: boolean;
  notes?: string[];
  stop?: { max_searches?: number };
};

export type PlanDraft = {
  id: number;
  ticker: string;
  profile: string;
  status: "draft" | "approved" | "started" | "discarded";
  edited: boolean;
  planner_note: string | null;
  plan: DraftPlan;
  job_id: number | null;
};

export const MIN_QUESTION = 10;
export const MAX_QUESTION = 400;
export const MAX_QUESTIONS = 16;

export function moveQuestion(list: readonly DraftQuestion[], id: string, delta: -1 | 1): DraftQuestion[] {
  const from = list.findIndex((q) => q.id === id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= list.length) return [...list];
  const next = [...list];
  [next[from], next[to]] = [next[to]!, next[from]!];
  return next;
}

export function removeQuestion(list: readonly DraftQuestion[], id: string): DraftQuestion[] {
  return list.filter((q) => q.id !== id);
}

export function rewordQuestion(list: readonly DraftQuestion[], id: string, question: string): DraftQuestion[] {
  return list.map((q) => (q.id === id ? { ...q, question } : q));
}

/** Turning "web" off also clears the search intent; the server derives the stored-data tools. */
export function toggleGroup(list: readonly DraftQuestion[], id: string, group: string): DraftQuestion[] {
  return list.map((q) => {
    if (q.id !== id) return q;
    const on = q.source_groups.includes(group);
    const source_groups = on ? q.source_groups.filter((g) => g !== group) : [...q.source_groups, group];
    return { ...q, source_groups, web_intent: group === "web" && on ? null : q.web_intent };
  });
}

/** Problems the owner can fix before sending; empty when the list looks acceptable. */
export function localProblems(list: readonly DraftQuestion[], webAllowed: boolean): string[] {
  const problems: string[] = [];
  if (list.length === 0) problems.push("A plan needs at least one question.");
  if (list.length > MAX_QUESTIONS) problems.push(`A plan has at most ${MAX_QUESTIONS} questions.`);
  for (const q of list) {
    const n = q.question.trim().length;
    if (n < MIN_QUESTION || n > MAX_QUESTION) problems.push(`"${q.id}": the question must be ${MIN_QUESTION}-${MAX_QUESTION} characters.`);
    if (!webAllowed && q.source_groups.includes("web")) problems.push(`"${q.id}": this profile does not search the web.`);
  }
  return problems;
}

/** The PATCH body's question list: order is the order wanted. */
export function toEdits(list: readonly DraftQuestion[]) {
  return list.map((q) => ({
    id: q.id,
    section: q.section,
    question: q.question,
    label: q.label ?? null,
    source_groups: q.source_groups,
    web_intent: q.web_intent,
  }));
}

export function authorLine(plan: DraftPlan): string {
  if (plan.fallback_reason) return `Code-built plan. A model plan was asked for but not used: ${plan.fallback_reason}`;
  if (plan.authored_by === "model") return "Written by a model, checked in code. Review it before you approve.";
  return "Built in code from the profile and report layout.";
}

export function statusLabel(status: PlanDraft["status"]): string {
  return { draft: "Draft: not approved", approved: "Approved: ready to run", started: "Run started", discarded: "Discarded" }[status];
}

/** Whether the list differs from the saved plan (an unsaved edit). */
export function isDirty(saved: readonly DraftQuestion[], local: readonly DraftQuestion[]): boolean {
  return JSON.stringify(toEdits(saved)) !== JSON.stringify(toEdits(local));
}
