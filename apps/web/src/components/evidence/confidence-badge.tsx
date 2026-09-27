import { CircleHelpIcon, LayersIcon, ShieldAlertIcon, ShieldCheckIcon } from "lucide-react";

import type { Confidence } from "@/lib/api/client";
import { CONFIDENCE } from "@/lib/evidence/confidence";
import { cn } from "@/lib/utils";

const ICONS = {
  verified: ShieldCheckIcon,
  corroborated: LayersIcon,
  unconfirmed: CircleHelpIcon,
  disputed: ShieldAlertIcon,
} as const satisfies Record<Confidence, unknown>;

/** Exactly one per fact or verdict, placed next to it: label + icon + muted colour. */
export function ConfidenceBadge({ confidence, className }: { confidence: Confidence; className?: string }) {
  const { label, tone, description } = CONFIDENCE[confidence];
  const Icon = ICONS[confidence];
  return (
    <span
      title={description}
      data-confidence={confidence}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text",
        tone === "verified" && "border-verified/40 bg-verified-subtle",
        tone === "corroborated" && "border-corroborated/40 bg-corroborated-subtle",
        tone === "unconfirmed" && "border-unconfirmed/40 bg-unconfirmed-subtle",
        tone === "disputed" && "border-disputed/40 bg-disputed-subtle",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          "size-3.5",
          tone === "verified" && "text-verified",
          tone === "corroborated" && "text-corroborated",
          tone === "unconfirmed" && "text-unconfirmed",
          tone === "disputed" && "text-disputed",
        )}
      />
      {label}
    </span>
  );
}
