"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResearchStarter } from "@/components/research/research-starter";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { researchForTicker, researchHref } from "@/lib/research/report";

/**
 * A holding's research reports (`GET /research?ticker=`) and "Research again" under a
 * chosen profile; the new report is linked once its run has finished.
 */
export function HoldingResearch({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/research", { params: { query: { ticker, limit: 10 } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const reports = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <Blueprint id="research" aria-labelledby="research-heading" className="scroll-mt-6 space-y-3 px-[22px] py-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <PanelHeading title={<span id="research-heading">Research reports</span>} />
        <Link href={researchForTicker(ticker)} className="text-sm text-accent-ink hover:underline">
          All research on {ticker}
        </Link>
      </div>
      <div className="border border-border-soft bg-surface-muted p-4">
        <ResearchStarter ticker={ticker} submitLabel="Research again" onFinished={reload} />
      </div>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading research…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`Research: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {reports && reports.length === 0 ? <p className="text-sm text-text-muted">No research on {ticker} yet.</p> : null}
      {reports && reports.length > 0 ? (
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {reports.map((r) => (
            <li key={r.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm">
              <span className="min-w-0 flex-1">
                <Link href={researchHref(r.id)} className="font-medium hover:text-accent-ink hover:underline">
                  {r.headline || r.title}
                </Link>
                {r.profile ? <span className="ml-2 text-xs text-text-muted">profile {r.profile}</span> : null}
              </span>
              <time dateTime={r.created_at} className="num text-xs text-text-muted">
                {formatDateTime(r.created_at)}
              </time>
            </li>
          ))}
        </ul>
      ) : null}
    </Blueprint>
  );
}
