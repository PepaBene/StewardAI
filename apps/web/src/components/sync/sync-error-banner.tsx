"use client";

import { AlertMessage } from "@/components/alert-message";

import { useSyncStatus } from "./sync-status-provider";

/** Shown under the header when "Sync now" could not queue a sync. */
export function SyncErrorBanner() {
  const { startError } = useSyncStatus();
  if (!startError) return null;
  return (
    <AlertMessage title="Sync not started" className="mb-6">
      <p>{startError}</p>
    </AlertMessage>
  );
}
