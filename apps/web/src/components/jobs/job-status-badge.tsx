import { CircleCheckIcon, CircleDashedIcon, CircleSlashIcon, CircleXIcon, LoaderCircleIcon } from "lucide-react";

import { StatusDot } from "@/components/ui/status-dot";
import type { JobStatus, JobStepOut } from "@/lib/api/client";
import { JOB_STATUS } from "@/lib/jobs/job-detail";
import { cn } from "@/lib/utils";

/** A job or step's status as a list/timeline marker — the same tone as JobStatusBadge. */
export function JobStatusDot({ status, className, interrupted = false }: { status: JobStatus | JobStepOut["status"]; className?: string; interrupted?: boolean }) {
  return <StatusDot tone={interrupted && status === "failed" ? "neutral" : JOB_STATUS[status].tone} className={className} />;
}

const ICONS = {
  queued: CircleDashedIcon,
  waiting: CircleDashedIcon,
  running: LoaderCircleIcon,
  succeeded: CircleCheckIcon,
  failed: CircleXIcon,
} as const;

/** A job's or a step's status: muted palette, an icon and a word. */
export function JobStatusBadge({
  status,
  className,
  interrupted = false,
}: {
  status: JobStatus | JobStepOut["status"];
  className?: string;
  /** A failed run the system closed because its process stopped: shown as "Interrupted", not as a failure. */
  interrupted?: boolean;
}) {
  const stopped = interrupted && status === "failed";
  const { label, tone } = stopped ? { label: "Interrupted", tone: "neutral" as const } : JOB_STATUS[status];
  const Icon = stopped ? CircleSlashIcon : ICONS[status];
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
