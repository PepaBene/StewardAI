"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { getBrowserApiClient } from "@/lib/api/client";

/**
 * Tells the backend which page was opened, for the pilot usage count. The backend stores the path
 * only while a pilot window is open and ignores the call otherwise; a failure is not shown.
 */
export function UsageBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (!/^\/[a-z0-9/_-]{0,120}$/.test(pathname)) return;
    void getBrowserApiClient()
      .POST("/release/usage", { body: { path: pathname } })
      .catch(() => undefined);
  }, [pathname]);
  return null;
}
