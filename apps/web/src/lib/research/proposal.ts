import type { ResearchBody, Source } from "@/lib/api/client";

/*
 * The thesis proposal of a research report: a field-by-field diff against the active
 * thesis, evidence per proposed line, and the answers of the accept route.
 */

export type ThesisProposal = NonNullable<ResearchBody["thesis_proposal"]>;
export type FieldChange = ThesisProposal["changes"][number];
export type ProposalField = FieldChange["field"];

export const FIELD_LABELS: Record<ProposalField, string> = {
  why_owned: "Why owned",
  exit_conditions: "Exit conditions",
  pre_mortem: "Pre-mortem",
};

export function fieldLabel(field: string): string {
  return FIELD_LABELS[field as ProposalField] ?? field.replaceAll("_", " ");
}

export const MODE_LABELS: Record<ThesisProposal["mode"], string> = {
  research_only: "Research only",
  draft_thesis: "Draft a thesis",
  propose_update: "Propose an update",
  review_thesis: "Review the thesis",
};

export function modeLabel(mode: string): string {
  return MODE_LABELS[mode as ThesisProposal["mode"]] ?? mode.replaceAll("_", " ");
}

export type DiffLine = { kind: "removed" | "kept" | "added"; text: string; evidenceIds: string[] };

/** One field as lines: the current thesis lines no longer present, those kept, and the added ones with their evidence. */
export function diffLines(change: Pick<FieldChange, "before" | "after">): DiffLine[] {
  const after = new Set(change.after.map((l) => l.text));
  const before = new Set(change.before);
  return [
    ...change.before.filter((t) => !after.has(t)).map((text): DiffLine => ({ kind: "removed", text, evidenceIds: [] })),
    ...change.after.map(
      (l): DiffLine => ({ kind: before.has(l.text) ? "kept" : "added", text: l.text, evidenceIds: l.evidence_ids }),
    ),
  ];
}

/** Evidence ids resolved to the report's sources; an id the report does not list keeps the id as its title. */
export function resolveEvidence(ids: readonly string[], sources: readonly Pick<Source, "id" | "title">[]): { id: string; title: string; listed: boolean }[] {
  return ids.map((id) => {
    const source = sources.find((s) => s.id === id);
    return { id, title: source?.title ?? id, listed: source !== undefined };
  });
}

export type AcceptOutcome = { kind: "applied" | "stale" | "error"; message: string };

/**
 * What an accept answer means for the owner. A 409 whose detail asks to run the research
 * again is a stale proposal: the theses were edited after the report was made, nothing was applied.
 */
export function acceptOutcome(status: number, detail: string | null | undefined, fields: readonly string[] = []): AcceptOutcome {
  if (status >= 200 && status < 300) {
    return { kind: "applied", message: `Saved ${fields.map(fieldLabel).join(", ") || "the change"} as a new thesis version.` };
  }
  if (status === 409 && /run the research again/i.test(detail ?? "")) {
    return {
      kind: "stale",
      message: "Your theses changed after this report was made, so nothing was applied. Run the research again to get a proposal against the current thesis.",
    };
  }
  if (status === 409) return { kind: "error", message: detail || "This report proposes no thesis changes." };
  return { kind: "error", message: detail || `The change was not applied (HTTP ${status}).` };
}
