"use client";

import { useEffect } from "react";

import { getBrowserApiClient } from "@/lib/api/client";

type OpenKind = "news_story" | "report" | "notice" | "escalation" | "thesis" | "company" | "fund";

/**
 * Tell the app the owner opened this item, so it can send less of what is never read. Only the
 * kind and id are sent. A failure is ignored: opening a page never depends on it.
 */
export function useTrackOpen(kind: OpenKind, ref: string | number | null | undefined) {
  useEffect(() => {
    if (ref === null || ref === undefined || ref === 0 || ref === "") return;
    void getBrowserApiClient()
      .POST("/engagement", { body: { kind, ref: String(ref) } })
      .catch(() => undefined);
  }, [kind, ref]);
}
