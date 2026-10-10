"use client";

import { useEffect, useRef, useState } from "react";

import { networkFailure, resolveLoaded, type Keyed, type Loaded, type Outcome } from "@/lib/disclosure/keyed";

/**
 * Loads data for `key` (null means do not load). The result is stored with its key, so inputs that change
 * never show the old answer; bumping `reload` fetches again for unchanged inputs; a failure is returned as
 * an error state. The previous answer for the same key stays visible while a reload is in flight.
 */
export function useKeyedLoad<T>(key: string | null, what: string, load: () => Promise<Outcome<T>>, reload = 0, delayMs = 0): Keyed<T> | null {
  const [result, setResult] = useState<Loaded<T> | null>(null);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });
  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      let next: Loaded<T>;
      try {
        const o = await loadRef.current();
        next = "data" in o ? { key, data: o.data, error: null } : { key, data: null, error: o.error };
      } catch (e) {
        next = { key, data: null, error: networkFailure(what, e) };
      }
      if (!cancelled) setResult(next);
    }, delayMs);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [key, what, reload, delayMs]);
  return resolveLoaded(result, key);
}
