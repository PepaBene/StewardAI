import {
  BanIcon,
  CircleCheckIcon,
  CircleDashedIcon,
  CircleHelpIcon,
  ClockAlertIcon,
  GaugeIcon,
  PieChartIcon,
  ScaleIcon,
  ServerCrashIcon,
  type LucideIcon,
} from "lucide-react";

import { DATA_STATE_META, STATE_TONE_CLASSES, type DataState } from "@/lib/data-state/states";
import { cn } from "@/lib/utils";

const ICONS: Record<DataState, LucideIcon> = {
  verified: CircleCheckIcon,
  stale: ClockAlertIcon,
  partial: PieChartIcon,
  not_covered: CircleDashedIcon,
  paid_only: BanIcon,
  quota_exhausted: GaugeIcon,
  access_unverified: CircleHelpIcon,
  disputed: ScaleIcon,
  unavailable: ServerCrashIcon,
};

/**
 * One of the nine availability states as icon + label (never colour alone). The tooltip
 * carries the full sentence; `reason` appends the specific cause for this entity.
 */
export function DataStateBadge({
  state,
  reason,
  className,
}: {
  state: DataState;
  reason?: string | null;
  className?: string;
}) {
  const meta = DATA_STATE_META[state];
  const Icon = ICONS[state];
  return (
    <span
      data-state={state}
      title={reason ? `${meta.description} ${reason}` : meta.description}
      className={cn(
        "inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
        STATE_TONE_CLASSES[meta.tone].chip,
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {meta.label}
    </span>
  );
}
