"use client";

import { useCallback, useMemo } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource, type ApiResult, type ResourceState } from "@/lib/hooks/use-api-resource";

import { mergeFacts, parseActualVsConsensus, parseFacts, parseForwardPayload, parseIrReleases, parseIrSources, parsePeerMetrics, parsePeerSet } from "./parse";
import type { ActualVsConsensus, ForwardPayload, IrRelease, IrSource, ParsedFacts, PeerEntry, PeerMetricsPayload, StatementKind } from "./types";

/*
 * Client hooks over the real endpoints. Responses the OpenAPI document leaves untyped
 * (`unknown`) are narrowed by the parsers in ./parse; a body that does not parse is an
 * error state ("unexpected response shape"), never an empty or zero result.
 */

/** A date (YYYY-MM-DD) as the end-of-day instant `as_of` expects; null = the current view. */
export function asOfParam(date: string | null): string | undefined {
  return date ? `${date}T23:59:59Z` : undefined;
}

/** Narrow an untyped resource through a parser; keeps the last good parsed value on error. */
export function parsedState<T>(state: ResourceState<unknown>, parse: (body: unknown) => T | null): ResourceState<T> {
  if (state.kind === "loading") return state;
  if (state.kind === "ok") {
    const value = parse(state.data);
    return value === null
      ? { kind: "error", title: "Unexpected response shape", detail: "The API answered, but not in the shape this view understands; nothing is drawn from it.", previous: null }
      : { kind: "ok", data: value };
  }
  return { ...state, previous: state.previous === null || state.previous === undefined ? null : parse(state.previous) };
}

function useParsed<T>(state: ResourceState<unknown>, parse: (body: unknown) => T | null): ResourceState<T> {
  return useMemo(() => parsedState(state, parse), [state, parse]);
}

const STATEMENTS: readonly StatementKind[] = ["income", "balance_sheet", "cash_flow"];

/** The three statements' facts as known at `asOf`: three requests, merged. */
export function useStatementFacts(ticker: string, asOf: string | null, reconciledOnly: boolean) {
  const fetcher = useCallback(async (): Promise<ApiResult<unknown[]>> => {
    const api = getBrowserApiClient();
    const results = await Promise.all(
      STATEMENTS.map((statement) =>
        api.GET("/holdings/{ticker}/financial-history", {
          params: { path: { ticker }, query: { statement, as_of: asOfParam(asOf), reconciled_only: reconciledOnly || undefined } },
        }),
      ),
    );
    const bad = results.find((r) => !r.response.ok || r.data === undefined);
    if (bad) return { error: bad.error, response: bad.response };
    return { data: results.map((r) => r.data), response: results[0].response };
  }, [ticker, asOf, reconciledOnly]);
  const { state, reload } = useApiResource(fetcher);
  const parse = useCallback((body: unknown): ParsedFacts | null => (Array.isArray(body) ? mergeFacts(body.map(parseFacts)) : null), []);
  return { state: useParsed(state, parse), reload };
}

/** Segment operating-profit facts, for the margin colour of the segment treemap. */
export function useSegmentProfitFacts(ticker: string, asOf: string | null) {
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/financial-history", {
        params: { path: { ticker }, query: { statement: "segment", line_item: "operating_income", as_of: asOfParam(asOf) } },
      }),
    [ticker, asOf],
  );
  const { state } = useApiResource(fetcher);
  return useParsed(state, parseFacts);
}

export function useForwardData(ticker: string, asOf: string | null) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/forward-data/consensus", { params: { path: { ticker }, query: { as_of: asOfParam(asOf) } } }),
    [ticker, asOf],
  );
  const { state, reload } = useApiResource(fetcher);
  return { state: useParsed<ForwardPayload>(state, parseForwardPayload), reload };
}

export function useActualVsConsensus(ticker: string, metric: string | null, fiscalPeriod: string | null) {
  const fetcher = useCallback(async (): Promise<ApiResult<unknown>> => {
    if (!metric || !fiscalPeriod) return { data: { state: "unselected" }, response: new Response(null, { status: 200 }) };
    return getBrowserApiClient().GET("/holdings/{ticker}/actual-vs-consensus", { params: { path: { ticker }, query: { metric, fiscal_period: fiscalPeriod } } });
  }, [ticker, metric, fiscalPeriod]);
  const { state } = useApiResource(fetcher);
  return useParsed<ActualVsConsensus>(state, parseActualVsConsensus);
}

export function usePeerSet(ticker: string) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/peer-set", { params: { path: { ticker } } }), [ticker]);
  const { state, reload } = useApiResource(fetcher);
  return { state: useParsed<PeerEntry[]>(state, parsePeerSet), reload };
}

export function usePeerMetrics(ticker: string, asOf: string | null, reconciledOnly: boolean) {
  const fetcher = useCallback(
    () =>
      getBrowserApiClient().GET("/holdings/{ticker}/peer-metrics", {
        params: { path: { ticker }, query: { as_of: asOfParam(asOf), reconciled_only: reconciledOnly || undefined } },
      }),
    [ticker, asOf, reconciledOnly],
  );
  const { state, reload } = useApiResource(fetcher);
  return { state: useParsed<PeerMetricsPayload>(state, parsePeerMetrics), reload };
}

export function useIrReleases(ticker: string, asOf: string | null) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/issuer-ir-releases", { params: { path: { ticker }, query: { as_of: asOfParam(asOf) } } }),
    [ticker, asOf],
  );
  const { state } = useApiResource(fetcher);
  return useParsed<IrRelease[]>(state, parseIrReleases);
}

export function useIrSources(ticker: string) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/holdings/{ticker}/issuer-ir-sources", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return useParsed<IrSource[]>(state, parseIrSources);
}

export function useHoldingsList() {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/portfolio/holdings"), []);
  return useApiResource(fetcher);
}
