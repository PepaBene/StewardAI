import { DATA_STATE_META, placeholderState, type DataState } from "@/lib/data-state/states";
import { cn } from "@/lib/utils";

import { DataStateBadge } from "./data-state-badge";

/**
 * What stands where a figure or chart would be when none may be drawn. It names the state
 * and the reason; it never shows 0, "—" alone or an empty axis that could pass for data.
 */
export function StatePlaceholder({
  state,
  reason,
  minHeight = 120,
  className,
}: {
  state: DataState;
  reason?: string | null;
  minHeight?: number;
  className?: string;
}) {
  const shown = placeholderState(state);
  const meta = DATA_STATE_META[shown];
  return (
    <div
      data-slot="state-placeholder"
      role="status"
      className={cn(
        "flex flex-col items-center justify-center gap-1.5 border border-dashed border-border px-4 py-4 text-center",
        className,
      )}
      style={{ minHeight }}
    >
      <DataStateBadge state={shown} />
      <p className="max-w-prose text-xs text-text-muted">{reason ?? meta.description}</p>
      {meta.nextStep ? <p className="text-xs text-text-muted">{meta.nextStep}</p> : null}
    </div>
  );
}
