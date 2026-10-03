"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSyncStatus } from "@/components/sync/sync-status-provider";

export type ApiResult<T> = { data?: T; error?: unknown; response: Response };

export type ResourceState<T> =
  | { kind: "loading" }
  | { kind: "ok"; data: T }
  | { kind: "error"; title: string; detail: string; previous: T | null; /** HTTP status; absent when unreachable. */ status?: number };

function describeError(result: ApiResult<unknown>): { title: string; detail: string; status: number } {
  const status = result.response.status;
  const error = result.error;
  const detail =
    error && typeof error === "object" && "detail" in error ? String(error.detail) : "";
  if (status === 401 || status === 403) {
    return { title: "Not authorised", detail: detail || "Sign in again (with MFA) and retry.", status };
  }
  if (status === 503) {
    return { title: "Backend not ready", detail: detail || "The API cannot reach its database.", status };
  }
  return { title: `API responded with HTTP ${status}`, detail, status };
}

async function settle<T>(fetcher: () => Promise<ApiResult<T>>): Promise<ResourceState<T>> {
  try {
    const result = await fetcher();
    if (result.response.ok && result.data !== undefined) return { kind: "ok", data: result.data };
    return { kind: "error", ...describeError(result), previous: null };
  } catch (err) {
    return {
      kind: "error",
      title: "Backend unreachable",
      detail: `${err instanceof Error ? err.message : String(err)}. Check that the API is running and NEXT_PUBLIC_API_BASE_URL is correct.`,
      previous: null,
    };
  }
}

/**
 * The state after a settled request: a failure keeps the last good data beside the error, and
 * a second failure in a row keeps it too. Pure, so the rule is unit-tested.
 */
export function mergeSettled<T>(prev: ResourceState<T>, next: ResourceState<T>): ResourceState<T> {
  if (next.kind !== "error") return next;
  if (prev.kind === "ok") return { ...next, previous: prev.data };
  if (prev.kind === "error") return { ...next, previous: prev.previous };
  return next;
}

/**
 * Fetches an API resource once, and again whenever a portfolio sync succeeds, so data on
 * screen follows the latest snapshot without a reload. If a refetch fails, the previous
 * data stays on screen next to the error.
 *
 * `fetcher` identifies the query: when it changes (another ticker, as-of date or filter) the
 * state returns to "loading" at once and the old query's data is never shown, not even as
 * `previous` beside an error — data for one query must not appear under another's label.
 * Memoise the fetcher with `useCallback` over the query inputs (or define it at module level).
 */
export function useApiResource<T>(fetcher: () => Promise<ApiResult<T>>) {
  const { lastSuccessJobId } = useSyncStatus();
  // The state is tagged with the fetcher that produced it; a different fetcher reads as loading.
  const [held, setHeld] = useState<{ fetcher: typeof fetcher; state: ResourceState<T> }>({
    fetcher,
    state: { kind: "loading" },
  });
  // Only the newest request's result is applied.
  const sequence = useRef(0);

  const fetchAndApply = useCallback(async () => {
    const mine = ++sequence.current;
    const next = await settle(fetcher);
    if (mine !== sequence.current) return;
    setHeld((prev) => ({
      fetcher,
      state: mergeSettled(prev.fetcher === fetcher ? prev.state : { kind: "loading" }, next),
    }));
  }, [fetcher]);

  useEffect(() => {
    void fetchAndApply();
  }, [fetchAndApply, lastSuccessJobId]);

  const state: ResourceState<T> = held.fetcher === fetcher ? held.state : { kind: "loading" };

  const reload = useCallback(() => {
    setHeld((prev) => (prev.fetcher === fetcher && prev.state.kind === "ok" ? prev : { fetcher, state: { kind: "loading" } }));
    void fetchAndApply();
  }, [fetcher, fetchAndApply]);

  return { state, reload };
}
