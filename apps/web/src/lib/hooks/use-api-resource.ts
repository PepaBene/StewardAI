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
 * Fetches an API resource once, and again whenever a portfolio sync succeeds, so data on
 * screen follows the latest snapshot without a reload. If a refetch fails, the previous
 * data stays on screen next to the error.
 *
 * `fetcher` must be stable (define it at module level).
 */
export function useApiResource<T>(fetcher: () => Promise<ApiResult<T>>) {
  const { lastSuccessJobId } = useSyncStatus();
  // The state belongs to the fetcher that produced it: a new fetcher (a changed query) starts at
  // "loading", so the old query's data is never shown as the answer to the new one. Only a
  // refetch of the same fetcher (a sync, a reload) keeps the previous data next to an error.
  const [held, setHeld] = useState<{ fetcher: typeof fetcher; state: ResourceState<T> }>({
    fetcher,
    state: { kind: "loading" },
  });
  const state: ResourceState<T> = held.fetcher === fetcher ? held.state : { kind: "loading" };
  // Only the newest request's result is applied.
  const sequence = useRef(0);

  const fetchAndApply = useCallback(async () => {
    const mine = ++sequence.current;
    const next = await settle(fetcher);
    if (mine !== sequence.current) return;
    setHeld((prev) => {
      const before = prev.fetcher === fetcher ? prev.state : null;
      return {
        fetcher,
        state: next.kind === "error" && before?.kind === "ok" ? { ...next, previous: before.data } : next,
      };
    });
  }, [fetcher]);

  useEffect(() => {
    void fetchAndApply();
  }, [fetchAndApply, lastSuccessJobId]);

  const reload = useCallback(() => {
    setHeld((prev) =>
      prev.fetcher === fetcher && prev.state.kind === "ok" ? prev : { fetcher, state: { kind: "loading" } },
    );
    void fetchAndApply();
  }, [fetcher, fetchAndApply]);

  return { state, reload };
}
