import { BotIcon, CircleDashedIcon } from "lucide-react";

import type { EscalationBody } from "@/lib/api/client";
import { escalationStatus } from "@/lib/news/escalation";
import { cn } from "@/lib/utils";

/** Whether a model assessed an impact note (muted palette, an icon and a word). */
export function EscalationStatusBadge({ body, className }: { body: EscalationBody; className?: string }) {
  const status = escalationStatus(body);
  const Icon = status.kind === "assessed" ? BotIcon : CircleDashedIcon;
  return (
    <span
      title={status.kind === "not_assessed" ? `Not assessed: ${status.reason}` : status.label}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text",
        status.kind === "assessed" ? "border-info-signal/40 bg-info-signal-subtle" : "border-warning-signal/40 bg-warning-signal-subtle",
        className,
      )}
    >
      <Icon aria-hidden="true" className={cn("size-3.5", status.kind === "assessed" ? "text-info-signal" : "text-warning-signal")} />
      {status.kind === "assessed" ? "Assessed" : "Not assessed"}
    </span>
  );
}
