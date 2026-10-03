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
import type { CorrelationRow } from "./logic";
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

export function useHoldingReturns(ticker: string, start: string, end: string) {
  const fetcher = useCallback(
    () => parsed(api().GET("/holdings/{ticker}/returns", { params: { path: { ticker }, query: { start, end } } }), parseReturns),
    [ticker, start, end],
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

export type CorrelationLoad = { rows: CorrelationRow[]; failed: string[]; asOf: string | null; source: string | null };

/**
 * One GET /holdings/{ticker}/prices per holding: each answers with the backend's correlation of
 * that holding with every other (last 252 days). No endpoint returns the whole matrix, so the
 * matrix is assembled from these vectors; nothing is computed here.
 */
export function useCorrelationRows(tickers: readonly string[]) {
  const key = tickers.join("\u0000");
  // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the stable identity of `tickers`
  const list = useCallback(() => tickers, [key]);
  const fetcher = useCallback(async (): Promise<ApiResult<CorrelationLoad>> => {
    const names = list();
    const results = await Promise.all(
      names.map(async (ticker) => {
        try {
          return { ticker, r: await api().GET("/holdings/{ticker}/prices", { params: { path: { ticker }, query: { days: 365 } } }) };
        } catch {
          return { ticker, r: null };
        }
      }),
    );
    const good = results.filter((x) => x.r && x.r.response.ok && x.r.data !== undefined);
    if (good.length === 0) {
      const first = results.find((x) => x.r)?.r;
      if (first) return { error: first.error, response: first.response };
      throw new Error("No holding price request reached the API.");
    }
    const rows: CorrelationRow[] = results.map(({ ticker, r }) => {
      const body = r && r.response.ok ? r.data : undefined;
      return { ticker, correlations: body && body.bars.length > 0 ? body.correlations.map((c) => ({ ticker: c.ticker, correlation: c.correlation })) : null };
    });
    const asOfs = good.map((x) => x.r!.data!.as_of).filter((d): d is string => d !== null);
    return {
      data: {
        rows,
        failed: results.filter((x) => !(x.r && x.r.response.ok)).map((x) => x.ticker),
        asOf: asOfs.length ? asOfs.reduce((a, b) => (a > b ? a : b)) : null,
        source: good[0].r!.data!.source,
      },
      response: good[0].r!.response,
    };
  }, [list]);
  return useApiResource(fetcher);
}
