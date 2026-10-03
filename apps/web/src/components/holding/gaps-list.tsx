import { DataStateBadge } from "@/components/data-state";
import { gapState } from "@/lib/holding-data/logic";
import type { Gap } from "@/lib/holding-data/types";

import { HoldingPanel } from "./shared";

/** Every explicit gap the backend reports for this holding: what is missing and why, never a blank. */
export function GapsList({ gaps, complete = true, loading = false }: { gaps: readonly Gap[]; complete?: boolean; loading?: boolean }) {
  // "No gap" is a claim about both responses: it needs both to have loaded.
  const takeaway =
    gaps.length > 0
      ? `${gaps.length} gap${gaps.length === 1 ? "" : "s"} reported${complete ? "" : " so far"}; nothing below is filled in with a default.${complete ? "" : loading ? " Some data is still loading, so more may follow." : " Part of the data could not be read, so this list may be incomplete."}`
      : complete
        ? "The API reports no gap for this holding at this cutoff."
        : loading
          ? "Loading: gaps are not known until the data-context and evidence responses have both arrived."
          : "Gaps could not be fully read: the data-context or evidence request failed, so absence of a listed gap says nothing.";
  return (
    <HoldingPanel
      id="gaps"
      kicker="Explicit gaps"
      title="What is missing for this holding"
      takeaway={takeaway}
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
