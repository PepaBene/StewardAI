"use client";

import { useEffect, useSyncExternalStore } from "react";

import { getBrowserApiClient, type FeedbackOut } from "@/lib/api/client";
import type { FeedbackTarget } from "@/lib/feedback/feedback";
import { createMarksStore } from "@/lib/feedback/marks-store";

/** One store per browser tab: every feedback control on screen shares its batched reads. */
export const feedbackMarks = createMarksStore(async (kind, ids) => {
  const { data, response } = await getBrowserApiClient().GET("/feedback", {
    params: { query: { target_kind: kind, target_id: ids, limit: 500 } },
  });
  if (!data) throw new Error(`GET /feedback answered HTTP ${response.status}`);
  return data;
});

/** The owner's current mark on `target`: undefined while loading, null when there is none. */
export function useFeedbackMark(target: FeedbackTarget): FeedbackOut | null | undefined {
  const { kind, id, sentencePath } = target;
  useEffect(() => {
    feedbackMarks.request({ kind, id, sentencePath });
  }, [kind, id, sentencePath]);
  return useSyncExternalStore(
    feedbackMarks.subscribe,
    () => feedbackMarks.get({ kind, id, sentencePath }),
    () => undefined,
  );
}
