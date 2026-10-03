"use client";

import { useCallback } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource, type ApiResult } from "@/lib/hooks/use-api-resource";

import {
  parseCorporateActions,
  parseExchangeSessions,
  parseExpectedSessions,
  parseListing,
  parseReturns,
} from "./parse";
import type { Parsed } from "./types";

/*
 * Typed readers of the Phase 3 endpoints. Untyped bodies go through the parsers in parse.ts and
 * arrive as `Parsed<T>`: a rejected shape is shown as unavailable, never as an empty value.
 */

const api = () => getBrowserApiClient();

async function parsed<T>(call: Promise<{ data?: unknown; error?: unknown; response: Response }>, parse: (body: unknown) => Parsed<T>): Promise<ApiResult<Parsed<T>>> {
  const r = await call;
  if (r.data === undefined) return { error: r.error, response: r.response };
  return { data: parse(r.data), response: r.response };
}

export function usePortfolioHoldings() {
  const fetcher = useCallback(() => api().GET("/portfolio/holdings"), []);
  return useApiResource(fetcher);
}

export function useHoldingPrices(ticker: string, days: number) {
  const fetcher = useCallback(() => api().GET("/holdings/{ticker}/prices", { params: { path: { ticker }, query: { days } } }), [ticker, days]);
  return useApiResource(fetcher);
}

export function usePriceDiagnostics(ticker: string) {
  const fetcher = useCallback(() => api().GET("/holdings/{ticker}/prices/diagnostics", { params: { path: { ticker } } }), [ticker]);
  return useApiResource(fetcher);
}

export function useCorporateActions(ticker: string) {
  const fetcher = useCallback(() => parsed(api().GET("/holdings/{ticker}/corporate-actions", { params: { path: { ticker } } }), parseCorporateActions), [ticker]);
  return useApiResource(fetcher);
}

/** `refreshKey` is bumped after an owner decision so the figures are re-read (it only keys the fetcher). */
export function useHoldingReturns(ticker: string, start: string, end: string, refreshKey = 0) {
  const fetcher = useCallback(
    () => parsed(api().GET("/holdings/{ticker}/returns", { params: { path: { ticker }, query: { start, end } } }), parseReturns),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshKey is deliberately part of the query identity
    [ticker, start, end, refreshKey],
  );
  return useApiResource(fetcher);
}

export function useHoldingListing(ticker: string) {
  const fetcher = useCallback(() => parsed(api().GET("/holdings/{ticker}/listing", { params: { path: { ticker } } }), parseListing), [ticker]);
  return useApiResource(fetcher);
}

export function useExchangeSessions(mic: string, start: string, end: string) {
  const fetcher = useCallback(
    () => parsed(api().GET("/exchange-sessions/{mic}", { params: { path: { mic }, query: { start, end } } }), parseExchangeSessions),
    [mic, start, end],
  );
  return useApiResource(fetcher);
}

export function useExpectedSessions(mic: string, start: string, end: string) {
  const fetcher = useCallback(
    () => parsed(api().GET("/exchange-sessions/{mic}/expected", { params: { path: { mic }, query: { start, end } } }), parseExpectedSessions),
    [mic, start, end],
  );
  return useApiResource(fetcher);
}

export function useExchangeCalendar() {
  const fetcher = useCallback(() => api().GET("/calendar/exchanges"), []);
  return useApiResource(fetcher);
}

export function usePortfolioHoldingsPrices(days: number) {
  const fetcher = useCallback(() => api().GET("/portfolio/holdings/prices", { params: { query: { days } } }), [days]);
  return useApiResource(fetcher);
}

export function usePortfolioRisk(days: number) {
  const fetcher = useCallback(() => api().GET("/portfolio/risk", { params: { query: { days } } }), [days]);
  return useApiResource(fetcher);
}

/** One request for the whole matrix; the window and the minimum shared returns are part of the query identity. */
export function usePortfolioCorrelation(days: number, minShared: number) {
  const fetcher = useCallback(() => api().GET("/portfolio/correlation", { params: { query: { days, min_shared: minShared } } }), [days, minShared]);
  return useApiResource(fetcher);
}
