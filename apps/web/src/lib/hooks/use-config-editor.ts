"use client";

import { useCallback, useEffect, useState } from "react";

import { fieldErrors, type FieldErrors } from "@/lib/api/validation-errors";

type Result<T> = { data?: T; error?: unknown; response: Response };
type Version<B> = { id: number; created_at: string; note: string | null; body: B };

export const CONFLICT_MESSAGE =
  "This config changed elsewhere since you opened it. Reload to see the latest; your edits will be lost.";

/**
 * The query of a config PUT: the note, plus the version the edits started from so the API
 * answers 409 when another save got in first. Typed as the note-only query so it fits the
 * generated schema whether or not it lists `base_version_id` yet.
 */
export function configSaveQuery(note: string | null, baseVersionId: number | null): { note: string | null } {
  return (baseVersionId === null ? { note } : { note, base_version_id: baseVersionId }) as { note: string | null };
}

export type EditorStatus =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; version: Version<unknown> | null };

/**
 * Load, validate and save one versioned config domain. The active version is loaded once
 * (a 404 means none saved yet), so background refreshes never overwrite unsaved edits.
 * Validation errors come from the backend's Pydantic model via the 422 answer. A save sends
 * the loaded version's id; a 409 (someone saved in between) keeps the edits and says so.
 */
export function useConfigEditor<B>(options: {
  load: () => Promise<Result<Version<B>>>;
  /** `baseVersionId`: the version the edits started from (null when none was saved yet). */
  save: (body: B, note: string | null, baseVersionId: number | null) => Promise<Result<Version<B>>>;
  validate: (body: B) => Promise<Result<B>>;
  onLoaded: (body: B | null) => void;
}) {
  const { load, save, validate, onLoaded } = options;
  const [status, setStatus] = useState<EditorStatus>({ kind: "loading" });
  const [version, setVersion] = useState<Version<B> | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState<"saving" | "validating" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await load();
        if (cancelled) return;
        if (result.response.status === 404) {
          onLoaded(null);
          setStatus({ kind: "ready", version: null });
        } else if (result.data) {
          onLoaded(result.data.body);
          setVersion(result.data);
          setStatus({ kind: "ready", version: result.data });
        } else {
          setStatus({ kind: "error", message: `The API answered HTTP ${result.response.status}.` });
        }
      } catch (err) {
        if (!cancelled) setStatus({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
    // Load once per mount; the callbacks are recreated on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = useCallback(
    async (kind: "saving" | "validating", body: B, note: string | null): Promise<Version<B> | null> => {
      setBusy(kind);
      setMessage(null);
      try {
        const result = kind === "saving" ? await save(body, note, version?.id ?? null) : await validate(body);
        if (result.response.status === 409) {
          setMessage(CONFLICT_MESSAGE);
          return null;
        }
        if (result.response.status === 422 || !result.data) {
          setErrors(fieldErrors(result.error));
          if (result.response.status !== 422) {
            setMessage(`The API answered HTTP ${result.response.status}.`);
          }
          return null;
        }
        setErrors({});
        if (kind === "saving") {
          const saved = result.data as Version<B>;
          setVersion(saved);
          setMessage(`Saved as version #${saved.id}.`);
          return saved;
        }
        setMessage("Valid. Nothing saved yet.");
        return null;
      } catch (err) {
        setMessage(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        setBusy(null);
      }
    },
    [save, validate, version],
  );

  return {
    status,
    version,
    errors,
    busy,
    message,
    save: (body: B, note: string | null) => run("saving", body, note),
    validate: (body: B) => run("validating", body, null),
  };
}
