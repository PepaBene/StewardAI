import { CircleAlertIcon, CircleCheckIcon, CircleHelpIcon, InfoIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * F31 VerdictLine: one backend-written sentence beside a number. The tone comes from the verdict
 * (good, caution, bad, unknown, neutral), never from the sign of the number, and an icon always
 * rides along with the colour.
 */

export type Verdict = "good" | "caution" | "bad" | "unknown" | "neutral";

const ICON = { good: CircleCheckIcon, caution: CircleAlertIcon, bad: CircleAlertIcon, unknown: CircleHelpIcon, neutral: InfoIcon };
const TONE: Record<Verdict, string> = {
  good: "text-gain",
  caution: "text-warning-signal",
  bad: "text-loss",
  unknown: "text-text-faint",
  neutral: "text-text-muted",
};

export function VerdictLine({ verdict = "neutral", children, className }: { verdict?: Verdict; children: ReactNode; className?: string }) {
  const Icon = ICON[verdict];
  return (
    <p className={cn("flex items-start gap-1.5 text-sm leading-snug", className)}>
      <Icon aria-hidden="true" className={cn("mt-0.5 size-4 shrink-0", TONE[verdict])} />
      <span>{children}</span>
    </p>
  );
}
