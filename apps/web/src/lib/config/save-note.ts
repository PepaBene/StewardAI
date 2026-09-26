/** The Save bar's "why this change" note, tied to the version it will be saved on top of. */
export type NoteDraft = { note: string; forVersion: number | null };

/**
 * The note to show once the active version is `savedVersionId`: a new id means the save
 * went through, so the note starts empty again; a failed save (same id) keeps it.
 * Returns `draft` itself when nothing changes.
 */
export function noteDraftFor(draft: NoteDraft, savedVersionId: number | null): NoteDraft {
  return draft.forVersion === savedVersionId ? draft : { note: "", forVersion: savedVersionId };
}
