"use client";

import { ClockIcon } from "lucide-react";

import { formatDate, formatRelative, formatTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";

/**
 * "As of 22:31 · synced 4 min ago". `syncedAt` is the finish time of the last step of the
 * sync that wrote the data on screen, so a stale figure is never shown as current.
 */
export function FreshnessLabel({
  syncedAt,
  snapshotDate,
}: {
  syncedAt: string;
  snapshotDate: string;
}) {
  const now = useNow();
  const at = new Date(syncedAt);
  const sameDay = at.toDateString() === now.toDateString();
  return (
    <p className="flex items-center gap-1.5 text-sm text-text-muted">
      <ClockIcon aria-hidden="true" className="size-4" />
      <span className="num">
        As of <time dateTime={syncedAt}>{formatTime(at)}</time>
        {sameDay ? null : <>, {formatDate(snapshotDate)}</>} · synced {formatRelative(at, now)}
      </span>
    </p>
  );
}
