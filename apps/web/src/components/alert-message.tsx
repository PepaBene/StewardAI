import { CircleAlertIcon, InfoIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Inline message. Severity is carried by an icon and a visible text label as
 * well as colour (no meaning by colour alone). Uses the muted severity palette,
 * never gain/loss.
 */
export function AlertMessage({
  tone = "warning",
  title,
  children,
  className,
}: {
  tone?: "warning" | "info";
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  const Icon = tone === "warning" ? CircleAlertIcon : InfoIcon;
  return (
    <div
      role={tone === "warning" ? "alert" : "status"}
      className={cn(
        "flex gap-3 rounded-md border p-3 text-sm",
        tone === "warning"
          ? "border-warning-signal/40 bg-warning-signal-subtle"
          : "border-info-signal/40 bg-info-signal-subtle",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          "mt-0.5 size-4 shrink-0",
          tone === "warning" ? "text-warning-signal" : "text-info-signal",
        )}
      />
      <div className="space-y-1">
        <p className="font-medium text-text">{title}</p>
        {children ? <div className="text-text-muted">{children}</div> : null}
      </div>
    </div>
  );
}
