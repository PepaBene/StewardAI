"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { mapResource, useDataCoverage } from "@/lib/source-coverage/hooks";
import type { Entity, HeldInput, PriceInput, ValuationInput, LedgerGap } from "./types";
import { adaptHeld, adaptPrice, adaptValuation, ledgerGaps } from "./parse";

const fetchHoldings = () => getBrowserApiClient().GET("/portfolio/holdings");
const fetchWatchlist = () => getBrowserApiClient().GET("/watchlist");
const fetchPrices = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 7 } } });

/** Holdings, watchlist and held prices from the API, each with its own load/error/last-good state. */
export function useScreenSources() {
  const holdings = useApiResource(fetchHoldings);
  const watchlist = useApiResource(fetchWatchlist);
  const prices = useApiResource(fetchPrices);
  const held = useMemo(() => mapResource(holdings.state, (r): HeldInput[] => r.holdings.map(adaptHeld)), [holdings.state]);
  const watch = useMemo(() => mapResource(watchlist.state, (r) => r.map((w) => ({ ticker: w.ticker }))), [watchlist.state]);
  const price = useMemo(() => mapResource(prices.state, (r): PriceInput[] => r.holdings.map(adaptPrice)), [prices.state]);
  const reload = useCallback(() => {
    holdings.reload();
    watchlist.reload();
    prices.reload();
  }, [holdings, watchlist, prices]);
  return { held, watch, price, reload };
}

/** Gap-state ledger entries per security (paid_only / not_covered), for the unknown-reason split. */
export function useLedgerGaps(): { gaps: ReadonlyMap<string, readonly LedgerGap[]>; failed: boolean } {
  const { state } = useDataCoverage({ entityKind: "security" });
  const entries = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  return useMemo(() => ({ gaps: entries ? ledgerGaps(entries) : new Map<string, LedgerGap[]>(), failed: entries === null && state.kind === "error" }), [entries, state.kind]);
}

const CONCURRENCY = 4;

/**
 * Loads `GET /holdings/{ticker}/valuation` for each entity (the API has no batch route), a few at a
 * time. A 404 means the ticker is unknown to the valuation read model (not covered); any other
 * failure is `error`. A reload keeps the previous figures until each one is replaced.
 */
export function useValuations(entities: readonly Entity[], enabled: boolean) {
  const [map, setMap] = useState<ReadonlyMap<string, ValuationInput>>(new Map());
  const [tick, setTick] = useState(0);
  const key = entities.map((e) => e.ticker).join("|");
  const run = useRef(0);

  useEffect(() => {
    if (!enabled || key === "") return;
    const mine = ++run.current;
    const tickers = key.split("|");
    let index = 0;
    const worker = async () => {
      while (index < tickers.length && mine === run.current) {
        const ticker = tickers[index++];
        let next: ValuationInput;
        try {
          const r = await getBrowserApiClient().GET("/holdings/{ticker}/valuation", { params: { path: { ticker } } });
          if (r.response.ok && r.data) next = adaptValuation(r.data);
          else if (r.response.status === 404) next = { kind: "not_found" };
          else next = { kind: "error", message: `HTTP ${r.response.status}` };
        } catch (err) {
          next = { kind: "error", message: err instanceof Error ? err.message : String(err) };
        }
        if (mine !== run.current) return;
        setMap((prev) => {
          const old = prev.get(ticker.toUpperCase());
          // Keep the last good figures when a refetch fails.
          if (next.kind === "error" && old?.kind === "ok") return prev;
          return new Map(prev).set(ticker.toUpperCase(), next);
        });
      }
    };
    void Promise.all(Array.from({ length: Math.min(CONCURRENCY, tickers.length) }, worker));
    return () => {
      run.current += 1;
    };
  }, [key, enabled, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { valuations: map, reload };
}
