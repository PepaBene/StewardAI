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
  const [state, setState] = useState<ResourceState<T>>({ kind: "loading" });
  // Only the newest request's result is applied.
  const sequence = useRef(0);

  const fetchAndApply = useCallback(async () => {
    const mine = ++sequence.current;
    const next = await settle(fetcher);
    if (mine !== sequence.current) return;
    setState((prev) =>
      next.kind === "error" && prev.kind === "ok" ? { ...next, previous: prev.data } : next,
    );
  }, [fetcher]);

  useEffect(() => {
    void fetchAndApply();
  }, [fetchAndApply, lastSuccessJobId]);

  const reload = useCallback(() => {
    setState((prev) => (prev.kind === "ok" ? prev : { kind: "loading" }));
    void fetchAndApply();
  }, [fetchAndApply]);

  return { state, reload };
}
