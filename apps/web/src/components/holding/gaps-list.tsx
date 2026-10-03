import { DataStateBadge } from "@/components/data-state";
import { gapState } from "@/lib/holding-data/logic";
import type { Gap } from "@/lib/holding-data/types";

import { HoldingPanel } from "./shared";

/** Every explicit gap the backend reports for this holding: what is missing and why, never a blank. */
export function GapsList({ gaps }: { gaps: readonly Gap[] }) {
  return (
    <HoldingPanel
      id="gaps"
      kicker="Explicit gaps"
      title="What is missing for this holding"
      takeaway={gaps.length === 0 ? "The API reports no gap for this holding at this cutoff." : `${gaps.length} gap${gaps.length === 1 ? "" : "s"} reported; nothing below is filled in with a default.`}
    >
      {gaps.length > 0 ? (
        <ul className="flex flex-col gap-2 text-sm">
          {gaps.map((g) => (
            <li key={`${g.domain}|${g.reason}`} className="flex min-w-0 flex-wrap items-start gap-x-3 gap-y-1 border-b border-border-soft pb-2 last:border-0">
              <DataStateBadge state={gapState(g.state)} />
              <span className="num shrink-0 text-xs text-text-muted">{g.domain}</span>
              <span className="min-w-0 flex-1 text-text-muted">{g.reason}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </HoldingPanel>
  );
}
