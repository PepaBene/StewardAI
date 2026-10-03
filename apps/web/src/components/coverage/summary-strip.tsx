import { summaryKeyLabel } from "@/lib/coverage/logic";
import type { CoverageSummaryData, SummaryGroup } from "@/lib/coverage/types";
import { DATA_STATE_META, STATE_TONE_CLASSES, toDataState, type DataState } from "@/lib/data-state/states";
import { cn } from "@/lib/utils";

import { PatternFill, STATE_ICONS } from "./state-glyph";

/** Entries of a group per DataState (backend strings folded through the shared adapter). */
export function groupStates(g: SummaryGroup): [DataState, number][] {
  const m = new Map<DataState, number>();
  for (const [raw, n] of Object.entries(g.byState)) m.set(toDataState(raw), (m.get(toDataState(raw)) ?? 0) + n);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function Bar({ group }: { group: SummaryGroup }) {
  const states = groupStates(group);
  return (
    <div className="flex h-6 w-full min-w-[8rem] border border-border-soft" role="img" aria-label={states.map(([s, n]) => `${n} ${DATA_STATE_META[s].label}`).join(", ")}>
      {states.map(([s, n]) => {
        const Icon = STATE_ICONS[s];
        return (
          <span
            key={s}
            title={`${DATA_STATE_META[s].label}: ${n} of ${group.entries}`}
            style={{ width: `${(n / group.entries) * 100}%` }}
            className={cn("relative flex min-w-0 items-center justify-center overflow-hidden border-r border-surface-raised last:border-r-0", STATE_TONE_CLASSES[DATA_STATE_META[s].tone].chip)}
          >
            <PatternFill state={s} />
            <Icon aria-hidden="true" className="relative size-3 shrink-0" />
          </span>
        );
      })}
    </div>
  );
}

/**
 * `GET /data/coverage/summary`: one row per group with its entries split by state (icon + pattern
 * + count) and the API's coverage ratio with its own denominator. Counts are ledger entries,
 * never the global universe.
 */
export function SummaryStrip({ data, limit = 60 }: { data: CoverageSummaryData; limit?: number }) {
  const shown = data.groups.slice(0, limit);
  return (
    <div className="flex flex-col gap-2">
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[40rem] text-xs">
          <caption className="sr-only">Ledger entries grouped by {data.groupBy}</caption>
          <thead>
            <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <th scope="col" className="px-2 py-1.5 font-medium">
                {data.groupBy}
              </th>
              <th scope="col" className="px-2 py-1.5 font-medium">
                Entries by state
              </th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">
                Entries
              </th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">
                Periods available / expected
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((g) => (
              <tr key={g.key ?? "__none"} className="border-b border-border-soft align-middle last:border-0">
                <th scope="row" className="num max-w-[16rem] px-2 py-1.5 text-left font-normal break-all">
                  {summaryKeyLabel(data.groupBy, g.key)}
                </th>
                <td className="px-2 py-1.5">
                  <Bar group={g} />
                  <p className="mt-1 text-text-muted">
                    {groupStates(g)
                      .map(([s, n]) => `${DATA_STATE_META[s].label} ${n}`)
                      .join(" · ")}
                  </p>
                </td>
                <td className="num px-2 py-1.5 text-right">{g.entries}</td>
                <td className="num px-2 py-1.5 text-right">
                  {g.expectedPeriods !== null && g.expectedPeriods > 0 && g.availablePeriods !== null ? (
                    <>
                      {g.availablePeriods} / {g.expectedPeriods}
                      {g.coverageRatio !== null ? ` (${(g.coverageRatio * 100).toFixed(0)}%)` : ""}
                      <span className="block text-text-muted">over {g.entriesWithExpected} of {g.entries} entries with an expected count</span>
                    </>
                  ) : (
                    <span className="text-text-muted">No expected count stated</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.groups.length > limit ? <p className="text-xs text-text-muted">Showing the first {limit} of {data.groups.length} groups; narrow the filters to see the rest.</p> : null}
      <p className="text-xs text-text-muted">{data.note}</p>
    </div>
  );
}
