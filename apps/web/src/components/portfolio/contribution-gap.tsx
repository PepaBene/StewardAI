import type { ContributionReport } from "@/lib/api/client";

/**
 * Why a contribution report cannot be drawn, or null when it can. The API answers with a `gap`
 * when fewer than two position snapshots exist in the window (a holding seen once is left out,
 * never reported as 0). A wire that still sends one-snapshot zeros with no gap (the old
 * behaviour) is caught too: a period that starts on its own end date has nothing to attribute.
 */
export function contributionGap(data: ContributionReport): { needsSnapshots: boolean; text: string } | null {
  if (data.gap) {
    if (data.gap === "no position snapshot yet") {
      return { needsSnapshots: false, text: "No portfolio snapshot yet. The first Trading212 sync creates one." };
    }
    return { needsSnapshots: /at least two/i.test(data.gap), text: data.gap };
  }
  if (data.holdings.length > 0 && data.period_start && data.as_of && data.period_start >= data.as_of) {
    return {
      needsSnapshots: true,
      text: `Only one snapshot exists (as of ${data.as_of}), so there is no period to attribute yet.`,
    };
  }
  if (data.holdings.length === 0) {
    return { needsSnapshots: false, text: "No holdings with enough history in this range yet." };
  }
  return null;
}

/** The explicit state shown instead of a chart or rows when a contribution needs more snapshots. */
export function ContributionGapNote({ data }: { data: ContributionReport }) {
  const gap = contributionGap(data);
  if (!gap) return null;
  return (
    <div className="flex flex-col gap-1 text-sm text-text-muted" data-testid="contribution-gap">
      {gap.needsSnapshots ? <p className="font-medium text-text">Needs at least two snapshots</p> : null}
      <p>{gap.text}</p>
      {gap.needsSnapshots ? <p>Contributions appear after the next sync. Nothing is drawn until then, and no holding is shown as zero.</p> : null}
    </div>
  );
}
