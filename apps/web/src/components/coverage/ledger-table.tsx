import Link from "next/link";

import { DataStateBadge } from "@/components/data-state";
import { formatDate, formatDateTime } from "@/lib/format";
import { staleBasisPhrase } from "@/lib/source-coverage/parse";
import { providerLabel } from "@/lib/source-coverage/providers";
import type { CoverageEntry } from "@/lib/source-coverage/types";

/**
 * The ledger rows behind a matrix cell or the whole filtered atlas: state with the reason,
 * periods available against expected, latest period, last check and the provider's access
 * page. "Last checked" is the ledger's checked_at: a check that failed still moves it, so the
 * last good observation is shown separately.
 */
export function LedgerTable({
  entries,
  caption,
  totalMatching,
}: {
  entries: readonly CoverageEntry[];
  caption: string;
  /** Rows matching before any cap; when above `entries.length` the table says rows are left out. */ totalMatching?: number;
}) {
  const leftOut =
    totalMatching !== undefined && totalMatching > entries.length
      ? totalMatching - entries.length
      : 0;
  if (entries.length === 0) {
    return (
      <p className="text-sm text-text-muted">
        No ledger rows match. An entity with no row is unknown, not covered or
        uncovered.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {leftOut > 0 ? (
        <p role="status" className="text-xs text-text">
          Showing {entries.length} of {totalMatching} matching rows, gaps first;{" "}
          {leftOut} rows are left out (verified rows sort last). Narrow the
          filters or select a matrix cell to see them.
        </p>
      ) : null}
      <div className="relative max-h-[28rem] overflow-auto border border-border-soft">
        <table className="w-full min-w-[52rem] text-xs">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-surface-muted">
            <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
              {[
                "Entity",
                "Dataset",
                "Provider (source)",
                "State and reason",
                "Periods",
                "Latest period",
                "Last checked",
                "Last good observation",
              ].map((c) => (
                <th key={c} scope="col" className="px-2 py-1.5 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr
                key={e.id}
                className="border-b border-border-soft align-top last:border-0"
              >
                <th
                  scope="row"
                  className="num px-2 py-1.5 text-left font-medium break-all"
                >
                  {e.entityKey}
                </th>
                <td className="num px-2 py-1.5 break-all">{e.dataset}</td>
                <td className="px-2 py-1.5">
                  <Link
                    href="/activity#source-coverage"
                    className="text-accent-ink hover:underline"
                  >
                    {providerLabel(e.provider)}
                  </Link>
                </td>
                <td className="px-2 py-1.5">
                  <DataStateBadge state={e.state} reason={e.staleReason ?? e.reason} />
                  <p className="mt-1 max-w-[22rem] text-text-muted">
                    {e.staleReason ?? e.reason ??
                      `No reason recorded (ledger state "${e.rawState}").`}
                  </p>
                </td>
                <td className="num px-2 py-1.5">
                  {e.availablePeriods !== null && e.expectedPeriods !== null
                    ? `${e.availablePeriods} of ${e.expectedPeriods}`
                    : e.availablePeriods !== null
                      ? `${e.availablePeriods} (expected unknown)`
                      : "Unknown"}
                </td>
                <td className="num px-2 py-1.5">
                  {e.latestPeriod ? (
                    <>
                      <time dateTime={e.latestPeriod}>
                        {formatDate(e.latestPeriod)}
                      </time>
                      {e.ageDays !== null ? (
                        <span className="text-text-muted">
                          {" "}
                          · {e.ageDays} d old
                        </span>
                      ) : null}
                    </>
                  ) : (
                    "No period"
                  )}
                </td>
                <td className="num px-2 py-1.5">
                  <time dateTime={e.checkedAt}>
                    {formatDateTime(e.checkedAt)}
                  </time>
                  <span className="block text-text-muted">
                    {e.staleAfterHours !== null
                      ? `stale after ${e.staleAfterHours} h${staleBasisPhrase(e.staleBasis) ? ` (${staleBasisPhrase(e.staleBasis)})` : ""}`
                      : "age not judged (no refresh cadence known)"}
                  </span>
                </td>
                <td className="num px-2 py-1.5">
                  {e.lastGoodObservationId !== null
                    ? `#${e.lastGoodObservationId}`
                    : "None recorded"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
