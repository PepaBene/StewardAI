"use client";

import { LoaderCircleIcon, RefreshCwIcon, SearchIcon, SlidersHorizontalIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { ResearchStarter } from "@/components/research/research-starter";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ResearchSummary } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { jobHref } from "@/lib/jobs/job-detail";
import { normaliseTicker, researchForTicker, researchHref } from "@/lib/research/report";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: ResearchSummary[] };

/** Past research reports (optionally for one ticker) and a "research this ticker" form. */
export function ResearchListView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const ticker = normaliseTicker(params.get("ticker") ?? "");
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/research", { params: { query: { ticker: ticker ?? undefined, limit: 100 } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", items: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, reloadKey]);

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        kicker="Research & Verification"
        title="Research"
        description="Deep research on any ticker under a research profile. Figures come from stored records and are computed in code; the model writes only the narrative sections, and every sentence cites its sources."
        aside={
          <>
            <Button asChild variant="outline" size="sm">
              <Link href="/reports?type=research">
                <SearchIcon aria-hidden="true" />
                Search reports
              </Link>
            </Button>
            <Button asChild variant="outline" size="sm">
              <Link href="/config/research-profiles">
                <SlidersHorizontalIcon aria-hidden="true" />
                Research profiles
              </Link>
            </Button>
          </>
        }
      />

      <Blueprint aria-labelledby="new-research-heading" className="flex flex-col gap-3 px-5 py-[18px]">
        <PanelHeading kicker="Start a run" title={<span id="new-research-heading">New research</span>} />
        <ResearchStarter initialTicker={ticker ?? ""} onFinished={() => setReloadKey((k) => k + 1)} />
      </Blueprint>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[22px] leading-tight font-semibold">
          {ticker ? (
            <>
              Reports on <span className="font-mono">{ticker}</span>
            </>
          ) : (
            "All reports"
          )}
          {load.kind === "ok" ? <span className="num ml-2 bg-border-soft px-2 py-px align-middle font-sans text-xs font-normal">{load.items.length}</span> : null}
        </h2>
        <div className="flex gap-1">
          {ticker ? (
            <Button variant="ghost" size="sm" onClick={() => router.replace(pathname, { scroll: false })}>
              <XIcon aria-hidden="true" />
              Show every ticker
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading research…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load research">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.items.length === 0 ? (
        <EmptyState title={ticker ? `No research on ${ticker} yet` : "No research yet"}>
          Start one above: pick a ticker and a profile.
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.items.length > 0 ? (
        <Blueprint className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-sm">
            <caption className="sr-only">Research reports, newest first</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-[11px] tracking-wide text-text-muted uppercase">
                <th scope="col" className="px-4 py-2 text-left font-medium">Report</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Ticker</th>
                <th scope="col" className="px-4 py-2 text-left font-medium">Profile</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Written</th>
                <th scope="col" className="px-4 py-2 text-right font-medium">Job</th>
              </tr>
            </thead>
            <tbody>
              {load.items.map((r) => (
                <tr key={r.id} className="border-b border-border-soft align-top last:border-0 hover:bg-border-soft">
                  <th scope="row" className="px-4 py-3 text-left font-normal">
                    <Link href={researchHref(r.id)} className="font-heading text-[17px] font-semibold hover:text-accent-ink hover:underline">
                      {r.headline || r.title}
                    </Link>
                    {r.headline ? <span className="block text-xs text-text-muted">{r.title}</span> : null}
                  </th>
                  <td className="px-4 py-3">
                    {r.ticker ? (
                      <Link href={researchForTicker(r.ticker)} className="font-mono text-xs text-accent-ink hover:underline" title={`Every report on ${r.ticker}`}>
                        {r.ticker}
                      </Link>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-text-muted">{r.profile ?? "—"}</td>
                  <td className="num px-4 py-3 text-right whitespace-nowrap text-text-muted">
                    <time dateTime={r.created_at}>{formatDateTime(r.created_at)}</time>
                  </td>
                  <td className="num px-4 py-3 text-right">
                    {r.job_id !== null ? (
                      <Link href={jobHref(r.job_id)} className="text-text-muted hover:text-text hover:underline">
                        #{r.job_id}
                      </Link>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Blueprint>
      ) : null}
    </div>
  );
}
