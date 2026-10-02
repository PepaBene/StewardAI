"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type DataResourceState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; message: string; /** The last good data stays on screen beside the error. */ previous: T | null };

/**
 * Loads data through an async `loader` once and on `reload()`: the hook the fixture-backed
 * free-data views use, with the same rule as `useApiResource` — a failed reload keeps the
 * previous data. `loader` must be stable (module level). Only the newest request applies.
 */
export function useDataResource<T>(loader: () => Promise<T>) {
  const [state, setState] = useState<DataResourceState<T>>({ kind: "loading" });
  const sequence = useRef(0);

  const run = useCallback(() => {
    const mine = ++sequence.current;
    loader().then(
      (data) => {
        if (mine === sequence.current) setState({ kind: "ok", data });
      },
      (err: unknown) => {
        if (mine !== sequence.current) return;
        const message = err instanceof Error ? err.message : String(err);
        setState((prev) => ({ kind: "error", message, previous: prev.kind === "ok" ? prev.data : prev.kind === "error" ? prev.previous : null }));
      },
    );
  }, [loader]);

  useEffect(() => {
    run();
  }, [run]);

  const reload = useCallback(() => {
    setState((prev) => (prev.kind === "ok" ? prev : { kind: "loading" }));
    run();
  }, [run]);

  return { state, reload };
}

/** The data to draw: the current result, or the previous one beside an error; null otherwise. */
export function resourceData<T>(state: DataResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}
