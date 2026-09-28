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
        "flex gap-3 border border-l-[3px] px-3.5 py-3 text-sm",
        tone === "warning"
          ? "border-warning-signal/40 border-l-warning-signal bg-warning-signal-subtle"
          : "border-info-signal/40 border-l-info-signal bg-info-signal-subtle",
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
        <p className="font-semibold text-text">{title}</p>
        {children ? <div className="text-text-muted">{children}</div> : null}
      </div>
    </div>
  );
}
