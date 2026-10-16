"use client";

import {
  CircleAlertIcon,
  CircleDashedIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatRelative, formatTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

import { useSyncStatus } from "./sync-status-provider";

const PHASE_LABELS = { queued: "Sync queued", waiting: "Waiting for previous sync", running: "Syncing" };

/** Header indicator: the portfolio sync's live state, plus a "Sync now" button. */
export function SyncIndicator() {
  const { view, startSync, starting, startError } = useSyncStatus();
  const now = useNow();

  let icon = <CircleDashedIcon aria-hidden="true" className="size-4" />;
  let text = "Checking sync…";
  let title: string | undefined;
  let tone = "text-text-muted";

  if (view.kind === "unavailable") {
    icon = <CircleAlertIcon aria-hidden="true" className="size-4 text-warning-signal" />;
    text = "Sync status unavailable";
    title = "Could not read the sync status from the database. Syncing still works.";
  } else if (view.kind === "never") {
    text = "Never synced";
  } else if (view.kind === "active") {
    icon = <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />;
    text = view.stepLabel && view.phase === "running" ? `Syncing: ${view.stepLabel}` : PHASE_LABELS[view.phase];
    tone = "text-text";
  } else if (view.kind === "idle") {
    if (view.failure) {
      icon = <CircleAlertIcon aria-hidden="true" className="size-4 text-warning-signal" />;
      text = `Sync failed ${formatRelative(new Date(view.failure.at), now)}`;
      title = view.failure.error ?? undefined;
      tone = "text-text";
    } else if (view.lastSuccessAt) {
      const at = new Date(view.lastSuccessAt);
      icon = <span aria-hidden="true" className="size-[7px] rounded-full bg-gain" />;
      text = `Synced ${formatRelative(at, now)}`;
      title = `Last synced ${formatTime(at)}, ${at.toLocaleDateString()}`;
    }
  }

  // Not disabled while the status is still loading: a duplicate request only queues a
  // second run, which waits for the first (the backend serialises runs per job type).
  const busy = starting || view.kind === "active";

  return (
    <div className="flex items-center gap-2">
      <p
        role="status"
        aria-live="polite"
        title={title}
        className={cn("flex items-center gap-[7px] text-[13px]", tone)}
      >
        {icon}
        <span className="num hidden whitespace-nowrap xl:inline">{text}</span>
        <span className="sr-only xl:hidden">{text}</span>
      </p>
      <Button
        variant="outline"
        size="sm"
        onClick={() => void startSync()}
        disabled={busy}
        title={startError ?? undefined}
        aria-label="Sync now"
      >
        <RefreshCwIcon aria-hidden="true" className={cn(starting && "animate-spin")} />
        <span aria-hidden="true" className="hidden sm:inline lg:hidden xl:inline">
          Sync now
        </span>
      </Button>
    </div>
  );
}
