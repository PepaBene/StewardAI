"use client";

import { useEffect, useState } from "react";

/** The current time, refreshed every `intervalMs`, for "synced 4 min ago" labels. */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
