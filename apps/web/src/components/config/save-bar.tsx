"use client";

import { CheckIcon, CircleDotIcon, LoaderCircleIcon, RotateCcwIcon, SaveIcon } from "lucide-react";
import { useId, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { flattenErrors, type FieldErrors } from "@/lib/api/validation-errors";
import { noteDraftFor, type NoteDraft } from "@/lib/config/save-note";
import { useUnsavedChangesGuard } from "@/lib/hooks/use-unsaved-changes-guard";
import { cn } from "@/lib/utils";

/**
 * Note + Validate + Save, with the API's validation errors summarised. While `dirty`, leaving
 * the page (a link, the palette, closing the tab) asks first. The note is kept until a save
 * succeeds, which shows as a new `savedVersionId`.
 */
export function SaveBar({
  dirty,
  savedVersionId,
  busy,
  errors,
  message,
  onValidate,
  onSave,
  blocked = false,
  saveLabel = "Save new version",
  summary = null,
  onDiscard,
}: {
  dirty: boolean;
  /** The active version's id (the editor's `version?.id`); a change means a save went through. */
  savedVersionId: number | null;
  /** Problems found before sending (shown in `errors`): Save stays disabled. */
  blocked?: boolean;
  /** "previewing": Save first asks for a replay preview (the rule editors). */
  busy: "saving" | "validating" | "previewing" | null;
  saveLabel?: string;
  errors: FieldErrors;
  message: string | null;
  onValidate: () => void;
  onSave: (note: string | null) => void;
  /** What the draft changes ("3 unsaved changes" · "VWCE target 22 % → 20 %, …"), when the editor can tell. */
  summary?: { count: string; detail: string } | null;
  /** Throw the draft away and return to the active version. */
  onDiscard?: () => void;
}) {
  const [draft, setDraft] = useState<NoteDraft>({ note: "", forVersion: savedVersionId });
  const current = noteDraftFor(draft, savedVersionId);
  if (current !== draft) setDraft(current);
  const note = current.note;
  useUnsavedChangesGuard(dirty);
  const noteId = useId();
  const problems = flattenErrors(errors);
  const status = message ?? (dirty ? "Unsaved changes." : "No changes.");
  return (
    <div className={cn("z-10 space-y-2", dirty && "sticky bottom-3")}>
      {problems.length > 0 ? (
        <AlertMessage title={`${problems.length} problem${problems.length === 1 ? "" : "s"} to fix before saving`}>
          <ul className="list-disc space-y-0.5 pl-4">
            {problems.slice(0, 8).map((p) => (
              <li key={`${p.path}:${p.message}`}>
                {p.path ? <span className="font-mono text-xs">{p.path}</span> : null} {p.message}
              </li>
            ))}
          </ul>
        </AlertMessage>
      ) : null}
      <div
        className={cn(
          "space-y-3 border bg-surface px-4 py-3",
          dirty ? "border-accent shadow-lg" : "border-border",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <CircleDotIcon aria-hidden="true" className={cn("size-4 shrink-0", dirty ? "text-accent" : "text-text-faint")} />
          <p className="min-w-0 flex-1 text-sm" role="status">
            {dirty && summary ? (
              <>
                <b className="font-semibold">{summary.count}</b>
                {summary.detail ? <span className="text-text-muted"> · {summary.detail}</span> : null}
                {message ? <span className="block text-text-muted">{message}</span> : null}
              </>
            ) : (
              <span className={dirty ? "font-semibold" : "text-text-muted"}>{status}</span>
            )}
          </p>
          <div className="flex flex-wrap gap-2">
            {onDiscard ? (
              <Button variant="outline" onClick={onDiscard} disabled={busy !== null || !dirty}>
                <RotateCcwIcon aria-hidden="true" />
                Discard
              </Button>
            ) : null}
            <Button variant="outline" onClick={onValidate} disabled={busy !== null}>
              {busy === "validating" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
              Validate
            </Button>
            <Button onClick={() => onSave(note.trim() || null)} disabled={busy !== null || !dirty || blocked}>
              {busy === "saving" || busy === "previewing" ? (
                <LoaderCircleIcon aria-hidden="true" className="animate-spin" />
              ) : (
                <SaveIcon aria-hidden="true" />
              )}
              {saveLabel}
            </Button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Label htmlFor={noteId} className="shrink-0 text-xs text-text-muted">
            Why this change (optional)
          </Label>
          <Input
            id={noteId}
            value={note}
            maxLength={500}
            placeholder="A note saved with the version"
            className="h-8 min-w-60 flex-1"
            onChange={(e) => setDraft({ ...current, note: e.target.value })}
          />
        </div>
      </div>
    </div>
  );
}
