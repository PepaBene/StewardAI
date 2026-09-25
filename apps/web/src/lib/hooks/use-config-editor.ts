"use client";

import { useCallback, useEffect, useState } from "react";

import { fieldErrors, type FieldErrors } from "@/lib/api/validation-errors";

type Result<T> = { data?: T; error?: unknown; response: Response };
type Version<B> = { id: number; created_at: string; note: string | null; body: B };

export type EditorStatus =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; version: Version<unknown> | null };

/**
 * Load, validate and save one versioned config domain. The active version is loaded once
 * (a 404 means none saved yet), so background refreshes never overwrite unsaved edits.
 * Validation errors come from the backend's Pydantic model via the 422 answer.
 */
export function useConfigEditor<B>(options: {
  load: () => Promise<Result<Version<B>>>;
  save: (body: B, note: string | null) => Promise<Result<Version<B>>>;
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
        const result = kind === "saving" ? await save(body, note) : await validate(body);
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
    [save, validate],
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
