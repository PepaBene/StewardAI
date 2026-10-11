import { ShieldAlertIcon } from "lucide-react";

import type { Source } from "@/lib/api/client";
import { INJECTION_FLAG, sourceRoleNote } from "@/lib/research/snapshot";

import { Tag } from "./report-bits";

/** The role (owner-trusted, forum, ...) of a web source with its caveat, and the low-confidence injection flag. */
export function SourceRoleNote({ source, flaggedIds }: { source: Source; flaggedIds: readonly string[] | undefined }) {
  const note = sourceRoleNote(source, flaggedIds);
  if (!note.roleLabel && !note.caveat && !note.flagged) return null;
  return (
    <div className="space-y-0.5 text-xs text-text-muted">
      {note.roleLabel || note.caveat ? (
        <p>
          {note.roleLabel ? <Tag className="mr-1">{note.roleLabel}</Tag> : null}
          {note.caveat}
        </p>
      ) : null}
      {note.flagged ? (
        <p className="flex items-start gap-1 text-text">
          <ShieldAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning-signal" />
          {INJECTION_FLAG}
        </p>
      ) : null}
    </div>
  );
}
