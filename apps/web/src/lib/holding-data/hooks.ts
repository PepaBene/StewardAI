"use client";

import { useCallback } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";

import { fetchMacroContext } from "@/lib/macro/api";

import { asOfQuery } from "./logic";

/*
 * One request per endpoint, ticker and as-of day: the holding page calls each hook once and
 * hands the state to every section that needs it (no section fetches the same endpoint again).
 * `asOfDay` is a YYYY-MM-DD day or "" for the current view.
 */

export function useHoldingDataContext(ticker: string, asOfDay: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/data-context", { params: { path: { ticker }, query: { as_of: asOfQuery(asOfDay) } } }),
    [ticker, asOfDay],
  );
  return useApiResource(fetcher);
}

export function useEvidenceSummary(ticker: string, asOfDay: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/evidence-summary", { params: { path: { ticker }, query: { as_of: asOfQuery(asOfDay) } } }),
    [ticker, asOfDay],
  );
  return useApiResource(fetcher);
}

/** Country/macro context of the holding (GET /macro/context/{ticker}), parsed by lib/macro. */
export function useHoldingMacro(ticker: string, asOfDay: string) {
  const fetcher = useCallback(() => fetchMacroContext(ticker, asOfQuery(asOfDay) ?? null), [ticker, asOfDay]);
  return useApiResource(fetcher);
}
