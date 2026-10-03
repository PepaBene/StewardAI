import type { DriftStatus } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/*
 * The drift band as a compact uppercase tag (the Holdings table's UNDER / WITHIN / OVER).
 * Drift is an allocation signal, not a gain or loss, so it never uses gain/loss colours:
 * out-of-band is the solid accent, in-band a quiet tint, and the word always carries the
 * meaning.
 */
const BAND = {
  in_band: { label: "Within", title: "Within its target band", tone: "quiet" },
  below_band: { label: "Under", title: "Below its target band", tone: "strong" },
  above_band: { label: "Over", title: "Above its target band", tone: "strong" },
  above_hard_limit: { label: "Over limit", title: "Above its hard limit", tone: "alarm" },
  no_target: { label: "No target", title: "No target weight set", tone: "none" },
} as const satisfies Record<DriftStatus, unknown>;

export function BandBadge({ status, className }: { status: DriftStatus; className?: string }) {
  const b = BAND[status];
  return (
    <span
      title={b.title}
      className={cn(
        "inline-flex items-center px-1.5 py-0.5 text-[10px] leading-none font-semibold tracking-[0.05em] whitespace-nowrap uppercase",
        b.tone === "quiet" && "bg-border-soft text-text-muted",
        b.tone === "strong" && "bg-accent text-accent-foreground",
        b.tone === "alarm" && "bg-warning-signal text-warning-signal-foreground",
        b.tone === "none" && "border border-border text-text-muted",
        className,
      )}
    >
      {b.label}
    </span>
  );
}
