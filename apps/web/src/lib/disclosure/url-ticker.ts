"use client";

import { useSearchParams } from "next/navigation";

/** Same rule as the ticker boxes on the disclosure pages. */
const TICKER = /^(?!\.+$)[A-Za-z0-9._-]{1,32}$/;

/**
 * The holding named in `?ticker=` (an alert links here), upper-cased, or null when absent or not a
 * valid ticker. Read once as the initial value of a page's ticker box.
 */
export function useUrlTicker(): string | null {
  const raw = useSearchParams().get("ticker")?.trim() ?? "";
  return TICKER.test(raw) ? raw.toUpperCase() : null;
}
