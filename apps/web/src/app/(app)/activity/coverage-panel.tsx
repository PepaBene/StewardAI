"use client";

import { CircleCheckIcon, LoaderCircleIcon, RefreshCwIcon, TriangleAlertIcon } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { coverageRows, type CoverageCell } from "@/lib/activity/activity";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchCoverage = () => getBrowserApiClient().GET("/activity/coverage");

/** Per holding: when news, prices and earnings checks last covered it, with each gap in words. */
export function CoveragePanel() {
  const { state, reload } = useApiResource(fetchCoverage);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const rows = useMemo(() => (data ? coverageRows(data) : []), [data]);

  return (
    <section id="coverage" aria-labelledby="coverage-heading" className="scroll-mt-24 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="coverage-heading" className="text-lg font-semibold">
            Coverage per holding
          </h2>
          <p className="text-sm text-text-muted">
            A holding has a <strong className="font-medium text-text">news gap</strong> when no successful news run has
            covered it within the monitoring window
            {data ? ` (${data.news_window_hours} h)` : ""}, and a <strong className="font-medium text-text">price gap</strong>{" "}
            when its latest daily bar is more than 4 days old. The window is set in{" "}
            <Link href="/config/jobs" className="text-accent hover:underline">
              Config: Jobs
            </Link>{" "}
            (monitoring).
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Reload
        </Button>
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading coverage…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Could not load coverage: ${state.title}`}>{state.detail}</AlertMessage> : null}

      {data ? (
        <>
          <p className="flex flex-wrap items-center gap-1.5 text-sm" role="status">
            {data.gaps > 0 ? (
              <>
                <TriangleAlertIcon aria-hidden="true" className="size-4 text-warning-signal" />
                <span className="font-medium">
                  {data.gaps} holding{data.gaps === 1 ? "" : "s"} with a coverage gap
                </span>
              </>
            ) : (
              <>
                <CircleCheckIcon aria-hidden="true" className="size-4 text-neutral-signal" />
                <span>No coverage gaps</span>
              </>
            )}
            <span className="num text-text-muted">· checked {formatDateTime(data.checked_at)}</span>
          </p>
          {rows.length === 0 ? (
            <EmptyState title="No open holdings">Coverage is listed once a portfolio sync has recorded positions.</EmptyState>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
              <table className="w-full min-w-[48rem] text-sm">
                <caption className="sr-only">Coverage per holding; holdings with gaps first</caption>
                <thead className="bg-surface-muted">
                  <tr className="border-b border-border text-text-muted">
                    <th scope="col" className="px-3 py-2 text-left font-medium">Holding</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">News</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Prices</th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">Earnings check</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.ticker} data-gap={r.gaps > 0 || undefined} className="border-b border-border align-top last:border-0">
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        <Link href={`/holdings/${encodeURIComponent(r.ticker)}`} className="font-medium hover:text-accent hover:underline">
                          {r.name}
                        </Link>
                        <span className="block font-mono text-xs text-text-muted">{r.ticker}</span>
                        {r.news.gap ? (
                          <Link
                            href={`/activity?type=news_ingest#job-log`}
                            className="text-xs text-accent hover:underline"
                          >
                            News runs
                          </Link>
                        ) : null}
                      </th>
                      <td className="px-3 py-2">
                        <Cell cell={r.news} />
                      </td>
                      <td className="px-3 py-2">
                        <Cell cell={r.prices} />
                      </td>
                      <td className="px-3 py-2">
                        <Cell cell={r.earnings} />
                        <Link href={`/activity?type=earnings_check&ticker=${encodeURIComponent(r.ticker)}#job-log`} className="text-xs text-accent hover:underline">
                          Its runs
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </section>
  );
}

function Cell({ cell }: { cell: CoverageCell }) {
  if (!cell.gap) {
    return (
      <span className="block">
        <span className="num">{cell.label}</span>
        <span className="block text-xs text-text-muted">{cell.detail}</span>
      </span>
    );
  }
  return (
    <span className="block space-y-0.5">
      <span className="inline-flex items-center gap-1 rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 text-xs font-medium text-text">
        <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
        {cell.label}
      </span>
      <span className="block text-xs text-text">{cell.detail}</span>
    </span>
  );
}
