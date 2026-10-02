import { ArrowRightIcon, RotateCcwIcon } from "lucide-react";
import Link from "next/link";

import { DataStateBadge } from "@/components/data-state";
import { describeCoverage } from "@/lib/data-state/provenance";
import { formatDate, formatRelative } from "@/lib/format";
import { drillThroughHref, groupByProvider, missingCount } from "@/lib/source-coverage/logic";
import type { CoverageCell, CoverageRow } from "@/lib/source-coverage/types";
import { cn } from "@/lib/utils";

function age(iso: string | null, now: Date): string {
  return iso ? formatRelative(new Date(iso), now) : "never verified";
}

function Cell({ cell, now }: { cell: CoverageCell; now: Date }) {
  const missing = missingCount(cell);
  return (
    <div className="flex flex-col gap-1">
      <DataStateBadge state={cell.state} reason={cell.reason} />
      <span className="num text-xs text-text-muted">{describeCoverage({ covered: cell.covered, total: cell.total, unit: "entities" })}</span>
      <span className="num text-xs text-text-muted">
        {cell.lastVerifiedAt ? (
          <>
            Verified <time dateTime={cell.lastVerifiedAt} title={formatDate(cell.lastVerifiedAt)}>{age(cell.lastVerifiedAt, now)}</time>
          </>
        ) : (
          "No verified value"
        )}
      </span>
      {cell.refreshFailing && cell.lastVerifiedAt ? (
        <span className="inline-flex items-start gap-1 text-xs text-text">
          <RotateCcwIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-warning-signal" />
          Refresh failing: last verified value kept
        </span>
      ) : null}
      {cell.reason ? <span className="text-xs text-text-muted">{cell.reason}</span> : null}
      {missing > 0 && cell.state === "partial" ? <span className="num text-xs text-text-faint">{missing} missing</span> : null}
    </div>
  );
}

/**
 * Provider × dataset × (held | watchlist) matrix. It is a real table (providers as row
 * groups), so the screen-reader reading order is provider → dataset → held → watchlist and
 * no state depends on colour. Each row's endpoint links to the failed run, or to the job-log
 * filter for its job, when one exists.
 */
export function CoverageMatrix({ rows, now }: { rows: readonly CoverageRow[]; now: Date }) {
  const groups = groupByProvider(rows);
  return (
    <div className="relative overflow-x-auto border border-border">
      <table className="w-full min-w-[56rem] text-sm">
        <caption className="sr-only">
          Source coverage: the datasets of each provider with their access state, coverage denominator and freshness for held securities and for the watchlist
        </caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-3 py-2 text-left font-medium">Dataset</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Held</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Watchlist</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Endpoint</th>
          </tr>
        </thead>
        {groups.map((g) => (
          <tbody key={g.provider} className="border-b border-border last:border-0">
            <tr className="bg-surface-muted/60">
              <th colSpan={4} scope="colgroup" className="px-3 py-1.5 text-left font-heading text-[15px] font-semibold">
                {g.label}
              </th>
            </tr>
            {g.rows.map((r) => {
              const href = drillThroughHref(r);
              const failing = r.held.refreshFailing || r.watchlist.refreshFailing;
              return (
                <tr key={r.id} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <span className="font-medium">{r.dataset}</span>
                  </th>
                  <td className="px-3 py-2">
                    <Cell cell={r.held} now={now} />
                  </td>
                  <td className="px-3 py-2">
                    <Cell cell={r.watchlist} now={now} />
                  </td>
                  <td className="px-3 py-2">
                    <code className="block font-mono text-xs text-text-muted">{r.endpoint}</code>
                    {href ? (
                      <Link
                        href={href}
                        className={cn("mt-1 inline-flex items-center gap-1 text-xs hover:underline", failing ? "font-medium text-text underline" : "text-accent-ink")}
                      >
                        {failing ? "Failed run" : "Job runs"}
                        <ArrowRightIcon aria-hidden="true" className="size-3" />
                      </Link>
                    ) : (
                      <span className="mt-1 block text-xs text-text-faint">No scheduled job</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        ))}
      </table>
    </div>
  );
}
