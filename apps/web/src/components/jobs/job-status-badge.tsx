import { CircleCheckIcon, CircleDashedIcon, CircleXIcon, LoaderCircleIcon } from "lucide-react";

import type { JobStatus, JobStepOut } from "@/lib/api/client";
import { JOB_STATUS } from "@/lib/jobs/job-detail";
import { cn } from "@/lib/utils";

const ICONS = {
  queued: CircleDashedIcon,
  waiting: CircleDashedIcon,
  running: LoaderCircleIcon,
  succeeded: CircleCheckIcon,
  failed: CircleXIcon,
} as const;

/** A job's or a step's status: muted palette, an icon and a word. */
export function JobStatusBadge({ status, className }: { status: JobStatus | JobStepOut["status"]; className?: string }) {
  const { label, tone } = JOB_STATUS[status];
  const Icon = ICONS[status];
  return (
    <span
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
          status === "running" && "animate-spin",
          tone === "neutral" && "text-neutral-signal",
          tone === "info" && "text-info-signal",
          tone === "warning" && "text-warning-signal",
        )}
      />
      {label}
    </span>
  );
}
