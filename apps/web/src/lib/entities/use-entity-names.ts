"use client";

import { useEffect, useState } from "react";

import { getBrowserApiClient } from "@/lib/api/client";
import { shortTicker } from "@/lib/portfolio/ticker";

/*
 * ticker -> company name, from GET /portfolio/holdings, fetched once per page load and
 * cached at module level so every `<EntityName>` on a page shares one request.
 */

type Names = ReadonlyMap<string, string>;

let cache: Names | null = null;
let inflight: Promise<Names> | null = null;
const listeners = new Set<(names: Names) => void>();

function load(): Promise<Names> {
  if (cache) return Promise.resolve(cache);
  inflight ??= getBrowserApiClient()
    .GET("/portfolio/holdings")
    .then((r) => {
      const map = new Map<string, string>();
      for (const h of r.data?.holdings ?? []) if (h.name) map.set(h.ticker, h.name);
      cache = map;
      listeners.forEach((fn) => fn(map));
      return map;
    })
    .catch(() => {
      inflight = null; // allow a later retry; show ids meanwhile
      return new Map<string, string>() as Names;
    });
  return inflight;
}

const EMPTY: Names = new Map();

/** The ticker -> name map (empty until loaded or if the request fails). */
export function useEntityNames(): Names {
  const [names, setNames] = useState<Names>(cache ?? EMPTY);
  useEffect(() => {
    // Initial state already holds the cache when there is one.
    if (cache) return;
    listeners.add(setNames);
    void load();
    return () => {
      listeners.delete(setNames);
    };
  }, []);
  return names;
}

/** A holding's display name: the known company name, else the short ticker. */
export function useEntityName(id: string, name?: string | null): string {
  const names = useEntityNames();
  return name || names.get(id) || shortTicker(id);
}

/** Test seam: forget the cache. */
export function resetEntityNames(): void {
  cache = null;
  inflight = null;
}
