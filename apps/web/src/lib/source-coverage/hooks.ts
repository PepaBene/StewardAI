"use client";

import { useCallback, useMemo } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/error-message";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";

import { adaptAccess, adaptCoverage, adaptStatus, adaptUsage, parseZeroCostGate } from "./parse";
import { tickerSet, usageSince } from "./logic";
import type {
  AccessRow,
  CoverageEntry,
  CoverageFilters,
  EntityScopes,
  SourceStatusRow,
  UsageWindow,
  ZeroCostGate,
} from "./types";
import type { FailedJob } from "./logic";

/** Maps the data of a resource state (ok data, or the last-good data kept beside an error). */
export function mapResource<T, U>(state: ResourceState<T>, fn: (data: T) => U): ResourceState<U> {
  if (state.kind === "ok") return { kind: "ok", data: fn(state.data) };
  if (state.kind === "error") return { ...state, previous: state.previous === null ? null : fn(state.previous) };
  return state;
}

/**
 * `GET /data/coverage` as adapted rows (`state` is already a DataState). The backend filters
 * provider, dataset, entity key, ledger state, period and entity kind; nothing is filtered or
 * defaulted client-side. A refetch failure keeps the previous rows beside the error.
 */
export function useDataCoverage(filters: CoverageFilters = {}) {
  const { provider, dataset, entityKey, state, period, entityKind } = filters;
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/data/coverage", {
        params: { query: { provider, dataset, entity_key: entityKey, state, period, entity_kind: entityKind } },
      }),
    [provider, dataset, entityKey, state, period, entityKind],
  );
  const { state: raw, reload } = useApiResource(fetcher);
  const result = useMemo(() => mapResource(raw, (rows): CoverageEntry[] => rows.map(adaptCoverage)), [raw]);
  return { state: result, reload };
}

const fetchAccess = () => getBrowserApiClient().GET("/sources/access");

/** `GET /sources/access`: one adapted row per (provider, endpoint) with state, freshness and quota. */
export function useSourceAccess() {
  const { state: raw, reload } = useApiResource(fetchAccess);
  const result = useMemo(() => mapResource(raw, (rows): AccessRow[] => rows.map(adaptAccess)), [raw]);
  return { state: result, reload };
}

/** Usage windows of the last `days` days and the current month (`GET /sources/usage?since=`). */
export function useSourceUsage(days = 15) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/sources/usage", { params: { query: { since: usageSince(new Date(), days) } } }),
    [days],
  );
  const { state: raw, reload } = useApiResource(fetcher);
  const result = useMemo(() => mapResource(raw, (rows): UsageWindow[] => rows.map(adaptUsage)), [raw]);
  return { state: result, reload };
}

const fetchStatus = () => getBrowserApiClient().GET("/sources/status");

/** `GET /sources/status`: day and month credits per endpoint. */
export function useSourceStatus() {
  const { state: raw, reload } = useApiResource(fetchStatus);
  const result = useMemo(() => mapResource(raw, (rows): SourceStatusRow[] => rows.map(adaptStatus)), [raw]);
  return { state: result, reload };
}

const fetchGate = () => getBrowserApiClient().GET("/verification/zero-cost-gate");

/** `GET /verification/zero-cost-gate`, parsed; a body of an unexpected shape is an error, never a pass. */
export function useZeroCostGate() {
  const { state: raw, reload } = useApiResource(fetchGate);
  const result = useMemo((): ResourceState<ZeroCostGate> => {
    const parse = (body: unknown) => parseZeroCostGate(body);
    if (raw.kind === "ok") {
      const gate = parse(raw.data);
      return gate ? { kind: "ok", data: gate } : { kind: "error", title: "Unexpected zero-cost gate response", detail: "The body did not match the audit contract, so no pass or fail is shown.", previous: null };
    }
    if (raw.kind === "error") return { ...raw, previous: raw.previous === null ? null : parse(raw.previous) };
    return raw;
  }, [raw]);
  return { state: result, reload };
}

const fetchHoldings = () => getBrowserApiClient().GET("/activity/coverage");
const fetchWatchlist = () => getBrowserApiClient().GET("/watchlist");

/**
 * Held and watchlist ticker sets (upper-cased; held positions (`GET /activity/coverage`) contribute their Trading212 ticker and price symbol) used to classify coverage entity keys. Errors if either list failed to load.
 */
export function useEntityScopes() {
  const { state: holdingsState, reload: reloadHoldings } = useApiResource(fetchHoldings);
  const { state: watchlistState, reload: reloadWatchlist } = useApiResource(fetchWatchlist);
  const state = useMemo((): ResourceState<EntityScopes> => {
    const build = (h: typeof holdingsState, w: typeof watchlistState): EntityScopes | null => {
      const hd = h.kind === "ok" ? h.data : h.kind === "error" ? h.previous : null;
      const wd = w.kind === "ok" ? w.data : w.kind === "error" ? w.previous : null;
      if (!hd || !wd) return null;
      return {
        held: tickerSet(hd.holdings.flatMap((x) => [x.ticker, x.price_symbol])),
        watchlist: tickerSet(wd.map((x) => x.ticker)),
      };
    };
    const built = build(holdingsState, watchlistState);
    const failed = holdingsState.kind === "error" ? holdingsState : watchlistState.kind === "error" ? watchlistState : null;
    if (failed && failed.kind === "error") return { ...failed, previous: built };
    if (built) return { kind: "ok", data: built };
    return { kind: "loading" };
  }, [holdingsState, watchlistState]);
  const reload = useCallback(() => {
    reloadHoldings();
    reloadWatchlist();
  }, [reloadHoldings, reloadWatchlist]);
  return { state, reload };
}

const fetchFailedJobs = () => getBrowserApiClient().GET("/jobs", { params: { query: { status: ["failed"], limit: 50 } } });

/** The newest failed jobs (for the drill-through from a failing provider). */
export function useFailedJobs() {
  const { state: raw, reload } = useApiResource(fetchFailedJobs);
  const result = useMemo(
    () => mapResource(raw, (page): FailedJob[] => page.jobs.map((j) => ({ id: j.id, job_type: j.job_type, error: j.error, params: j.params }))),
    [raw],
  );
  return { state: result, reload };
}

/** Owner action: lift a hard block after the credential or plan was fixed. Resolves to an error message or null. */
export async function resetProviderBlock(provider: string, endpoint: string): Promise<string | null> {
  try {
    const r = await getBrowserApiClient().POST("/provider-access/{provider}/{endpoint}/reset-block", {
      params: { path: { provider, endpoint } },
    });
    if (r.response.ok) return null;
    const detail = errorMessage(r.error);
    return `HTTP ${r.response.status}${detail ? `: ${detail}` : ""}`;
  } catch (err) {
    return err instanceof Error ? err.message : String(err);
  }
}
