import { BellRingIcon, CircleDotIcon, CircleIcon, MinusIcon } from "lucide-react";

import { SEVERITY, TIERS, type SeverityLevel } from "@/lib/news/labels";
import { cn } from "@/lib/utils";

const ICONS = { 0: MinusIcon, 1: CircleIcon, 2: CircleDotIcon, 3: BellRingIcon } as const;

/** Severity from the event rules: muted palette, a word and an icon (never colour alone). */
export function SeverityBadge({ severity, className }: { severity: number | null; className?: string }) {
  if (severity === null) {
    return (
      <span className={cn("inline-flex items-center rounded-sm border border-border px-1.5 py-0.5 text-xs text-text-muted", className)}>
        Unrated
      </span>
    );
  }
  const level = Math.max(0, Math.min(3, severity)) as SeverityLevel;
  const { label, tone, description } = SEVERITY[level];
  const Icon = ICONS[level];
  return (
    <span
      title={description}
      data-severity={level}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text",
        tone === "neutral" && "border-border bg-neutral-signal-subtle",
        tone === "info" && "border-info-signal/40 bg-info-signal-subtle",
        tone === "warning" && "border-warning-signal/40 bg-warning-signal-subtle",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          "size-3.5",
          tone === "neutral" && "text-neutral-signal",
          tone === "info" && "text-info-signal",
          tone === "warning" && "text-warning-signal",
        )}
      />
      <span className="sr-only">Severity {level}:</span>
      {label}
    </span>
  );
}

/** Source tier as plain text with its meaning on hover. */
export function TierBadge({ tier, className }: { tier: number; className?: string }) {
  const known = tier === 1 || tier === 2 || tier === 3;
  return (
    <span
      title={known ? `${TIERS[tier].label}: ${TIERS[tier].description}` : undefined}
      className={cn("font-mono text-xs whitespace-nowrap text-text-muted", className)}
    >
      T{tier}
      <span className="sr-only"> (source tier {tier})</span>
    </span>
  );
}
