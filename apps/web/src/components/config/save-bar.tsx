"use client";

import { CheckIcon, LoaderCircleIcon, SaveIcon } from "lucide-react";
import { useId, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { flattenErrors, type FieldErrors } from "@/lib/api/validation-errors";

/** Note + Validate + Save, with the API's validation errors summarised. */
export function SaveBar({
  dirty,
  busy,
  errors,
  message,
  onValidate,
  onSave,
  blocked = false,
}: {
  dirty: boolean;
  /** Problems found before sending (shown in `errors`): Save stays disabled. */
  blocked?: boolean;
  busy: "saving" | "validating" | null;
  errors: FieldErrors;
  message: string | null;
  onValidate: () => void;
  onSave: (note: string | null) => void;
}) {
  const [note, setNote] = useState("");
  const noteId = useId();
  const problems = flattenErrors(errors);
  return (
    <div className="sticky bottom-0 z-10 space-y-2 border-t border-border bg-surface/95 py-3 backdrop-blur">
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
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-60 flex-1 space-y-1">
          <Label htmlFor={noteId}>Why this change (optional)</Label>
          <Input id={noteId} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </div>
        <Button variant="outline" onClick={onValidate} disabled={busy !== null}>
          {busy === "validating" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
          Validate
        </Button>
        <Button
          onClick={() => {
            onSave(note.trim() || null);
            setNote("");
          }}
          disabled={busy !== null || !dirty || blocked}
        >
          {busy === "saving" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SaveIcon aria-hidden="true" />}
          Save new version
        </Button>
      </div>
      <p className="text-sm text-text-muted" role="status">
        {message ?? (dirty ? "Unsaved changes." : "No changes.")}
      </p>
    </div>
  );
}
