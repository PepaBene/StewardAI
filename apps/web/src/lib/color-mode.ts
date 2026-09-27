"use client";

import { useCallback, useLayoutEffect, useState } from "react";

/*
 * The colourblind-alternate mode toggle (Ledger phase 10): independent of the light/dark
 * theme, so it needs its own storage key and its own class on <html>, applied by a
 * blocking inline script (see `colorModeInitScript`) so there's no flash of the wrong mode.
 */
export const COLOR_MODE_STORAGE_KEY = "cvd-mode";
export const COLOR_MODE_CLASS = "cvd";

/** Inlined into <head> before hydration, mirroring how next-themes avoids a flash. */
export const colorModeInitScript = `(function(){try{if(localStorage.getItem(${JSON.stringify(COLOR_MODE_STORAGE_KEY)})==="on")document.documentElement.classList.add(${JSON.stringify(COLOR_MODE_CLASS)});}catch(e){}})();`;

function readStored(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(COLOR_MODE_STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

/** Reads/writes the colourblind-alternate mode: on <html class="cvd">, off otherwise. */
export function useColorMode(): [boolean, (next: boolean) => void] {
  // Lazy initializer: reads the same source as the blocking script, so hydration agrees
  // with the DOM it already set (see Next's "preventing flash before hydration" guide).
  const [enabled, setEnabled] = useState(readStored);

  // React's Strict Mode dev remount clears a class the blocking script set (it isn't part
  // of the JSX tree); re-apply it here on every mount and toggle. A no-op in production.
  useLayoutEffect(() => {
    document.documentElement.classList.toggle(COLOR_MODE_CLASS, enabled);
  }, [enabled]);

  const toggle = useCallback((next: boolean) => {
    setEnabled(next);
    try {
      localStorage.setItem(COLOR_MODE_STORAGE_KEY, next ? "on" : "off");
    } catch {
      // Private browsing or storage disabled: the class still applies for this load.
    }
  }, []);

  return [enabled, toggle];
}
