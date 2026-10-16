import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * Badge (visual spec B7): a 22px pill. Status badges always carry words, never colour alone.
 *   limited   amber outline   "Limited data"
 *   estimated purple outline  "est."
 *   stale     amber wash      "Stale"
 *   na        muted           "n/a"
 *   new       accent fill     "New"
 *   kind      muted           feed and inbox type chips ("Earnings", "Escalation")
 *   gain/loss sign washes     "Pass", "Breach"
 */

export type BadgeVariant = "limited" | "estimated" | "stale" | "na" | "new" | "kind" | "gain" | "loss" | "fair-value";

const VARIANT: Record<BadgeVariant, string> = {
  limited: "border-warning-signal text-warning-signal",
  estimated: "border-chart-forecast text-chart-forecast",
  stale: "border-transparent bg-warning-signal-subtle text-warning-signal",
  na: "border-transparent bg-surface-muted text-text-muted",
  new: "border-transparent bg-accent text-accent-foreground",
  kind: "border-transparent bg-surface-muted text-text-muted",
  gain: "border-transparent bg-gain-subtle text-gain",
  loss: "border-transparent bg-loss-subtle text-loss",
  "fair-value": "border-chart-fair-value text-text",
};

export function Badge({
  variant = "kind",
  icon,
  children,
  className,
  title,
}: {
  variant?: BadgeVariant;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-[22px] shrink-0 items-center gap-1 rounded-full border px-2 text-xs font-medium whitespace-nowrap [&_svg]:size-3",
        VARIANT[variant],
        className,
      )}
    >
      {variant === "fair-value" ? <span aria-hidden="true" className="h-0.5 w-2.5 bg-chart-fair-value" /> : null}
      {icon}
      {children}
    </span>
  );
}
