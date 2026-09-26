"use client";

import { ClockIcon, TriangleAlertIcon } from "lucide-react";

import { formatDateTime } from "@/lib/format";
import { freshness, type Freshness, type FreshnessKind } from "@/lib/freshness";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

/**
 * "As of 14:05 · 3 h ago" for data on screen. Stale data gets the warning icon, a visible
 * "Stale" tag and the reason (never colour alone).
 */
export function FreshnessText({
  value,
  prefix = "As of",
  className,
}: {
  value: Freshness;
  prefix?: string;
  className?: string;
}) {
  const Icon = value.stale ? TriangleAlertIcon : ClockIcon;
  return (
    <p
      data-stale={value.stale || undefined}
      className={cn(
        "inline-flex flex-wrap items-center gap-1.5 text-sm",
        value.stale ? "text-text" : "text-text-muted",
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn("size-4 shrink-0", value.stale ? "text-warning-signal" : undefined)} />
      <span className="num">
        {prefix}{" "}
        <time dateTime={value.at} title={formatDateTime(value.at)}>
          {value.asOf}
        </time>{" "}
        · {value.age}
      </span>
      {value.nextDue && !value.stale ? (
        <span className="num text-xs text-text-muted" title="When the next scheduled run is due; the data is current until then">
          · {value.nextDue}
        </span>
      ) : null}
      {value.stale ? (
        <span
          title={value.staleReason ?? undefined}
          className="rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 text-xs font-medium"
        >
          Stale<span className="font-normal text-text-muted">{value.staleReason ? `: ${value.staleReason}` : ""}</span>
        </span>
      ) : null}
    </p>
  );
}

/**
 * The freshness label for a timestamp, refreshed every 30 s. Renders nothing without one.
 * `nextDueAt` (the next scheduled run, from the API) makes staleness follow the schedule.
 */
export function AsOfLabel({
  at,
  kind,
  prefix,
  forceStale,
  nextDueAt,
  className,
}: {
  at: string | null | undefined;
  kind: FreshnessKind;
  prefix?: string;
  forceStale?: string;
  nextDueAt?: string | null;
  className?: string;
}) {
  const now = useNow();
  const value = freshness(at, kind, now, { forceStale, nextDueAt });
  if (!value) return null;
  return <FreshnessText value={value} prefix={prefix} className={className} />;
}
