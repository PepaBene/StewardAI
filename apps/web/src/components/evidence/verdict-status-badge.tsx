import { CheckIcon, HelpCircleIcon, XIcon } from "lucide-react";

import type { VerdictStatus } from "@/lib/api/client";
import { VERDICT_STATUS } from "@/lib/evidence/confidence";
import { cn } from "@/lib/utils";

const ICONS = { pass: CheckIcon, breach: XIcon, unknown: HelpCircleIcon } as const;

/* Pass/breach are financial state (gain/loss colours); unknown is dashed and neutral, so
   a missing figure never looks like either. */
export function VerdictStatusBadge({ status }: { status: VerdictStatus }) {
  const { label, tone } = VERDICT_STATUS[status];
  const Icon = ICONS[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-semibold whitespace-nowrap",
        tone === "gain" && "border-gain/40 bg-gain-subtle text-gain",
        tone === "loss" && "border-loss/40 bg-loss-subtle text-loss",
        tone === "unknown" && "border-dashed border-border text-text-muted",
      )}
    >
      <Icon aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  );
}
