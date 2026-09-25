import { ArrowDownIcon, ArrowUpIcon, CheckIcon, MinusIcon, TriangleAlertIcon } from "lucide-react";

import type { DriftStatus } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/*
 * Drift is an allocation signal, not a gain or a loss: it uses the muted severity palette
 * (neutral / warning), never gain/loss colours, and always carries a text label and an
 * icon so meaning never rests on colour alone.
 */
const STATUS = {
  in_band: { label: "In band", Icon: CheckIcon, tone: "neutral" },
  above_band: { label: "Above band", Icon: ArrowUpIcon, tone: "warning" },
  below_band: { label: "Below band", Icon: ArrowDownIcon, tone: "warning" },
  above_hard_limit: { label: "Above hard limit", Icon: TriangleAlertIcon, tone: "warning" },
  no_target: { label: "No target", Icon: MinusIcon, tone: "muted" },
} as const satisfies Record<DriftStatus, unknown>;

export function isOutOfBand(status: DriftStatus) {
  return STATUS[status].tone === "warning";
}

export function DriftStatusBadge({ status }: { status: DriftStatus }) {
  const { label, Icon, tone } = STATUS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        tone === "warning" && "border-warning-signal/40 bg-warning-signal-subtle text-text",
        tone === "neutral" && "border-border bg-neutral-signal-subtle text-text",
        tone === "muted" && "border-transparent text-text-muted",
        status === "above_hard_limit" && "font-semibold",
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("size-3.5", tone === "warning" ? "text-warning-signal" : "text-neutral-signal")}
      />
      {label}
    </span>
  );
}
