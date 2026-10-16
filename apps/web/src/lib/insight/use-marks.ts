"use client";

import { useEffect, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";

import { petalsFromMap, type MarkPetal, type Num } from "./insight";

export type MarksState = { petals: Map<string, MarkPetal[]>; aggregate: MarkPetal[] | null; loaded: boolean };

/**
 * Cached five-axis marks for a set of tickers in one request (`POST /checks/marks`). Tickers
 * without a cached check are simply absent: the row shows no mark rather than an empty one.
 */
export function useMarks(tickers: readonly string[]): MarksState {
  const key = [...new Set(tickers)].sort().join(",");
  const [state, setState] = useState<{ key: string; value: MarksState }>({ key: "", value: { petals: new Map(), aggregate: null, loaded: false } });
  useEffect(() => {
    if (!key) return;
    let live = true;
    void getBrowserApiClient()
      .POST("/checks/marks", { body: { tickers: key.split(",") } })
      .then(({ data }) => {
        if (!live || !data) return;
        const petals = new Map<string, MarkPetal[]>();
        for (const [t, axes] of Object.entries(data.marks ?? {})) petals.set(t, petalsFromMap(axes as Record<string, Num>));
        const agg = data.aggregate?.axes ? petalsFromMap(data.aggregate.axes as Record<string, Num>) : null;
        setState({ key, value: { petals, aggregate: agg, loaded: true } });
      })
      .catch(() => {
        if (live) setState({ key, value: { petals: new Map(), aggregate: null, loaded: true } });
      });
    return () => {
      live = false;
    };
  }, [key]);
  return state.key === key ? state.value : { petals: new Map(), aggregate: null, loaded: !key };
}
