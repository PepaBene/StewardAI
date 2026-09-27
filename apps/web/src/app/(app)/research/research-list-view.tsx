"use client";

import { LoaderCircleIcon, RefreshCwIcon, SearchIcon, SlidersHorizontalIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { ResearchStarter } from "@/components/research/research-starter";
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
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Research</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Deep research on any ticker under a research profile. Figures come from stored records and are computed in
            code; the model writes only the narrative sections, and every sentence cites its sources.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="ghost" size="sm">
            <Link href="/reports?type=research">
              <SearchIcon aria-hidden="true" />
              Search reports
            </Link>
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/config/research-profiles">
              <SlidersHorizontalIcon aria-hidden="true" />
              Research profiles
            </Link>
          </Button>
        </div>
      </div>

      <section aria-labelledby="new-research-heading" className="space-y-2 rounded-lg border border-border bg-surface-raised p-4">
        <h2 id="new-research-heading" className="font-medium">
          New research
        </h2>
        <ResearchStarter initialTicker={ticker ?? ""} onFinished={() => setReloadKey((k) => k + 1)} />
      </section>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[22px] leading-tight font-semibold">
          {ticker ? (
            <>
              Reports on <span className="font-mono">{ticker}</span>
            </>
          ) : (
            "All reports"
          )}
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
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised">
          {load.items.map((r) => (
            <li key={r.id} className="space-y-1 p-4">
              <span className="flex flex-wrap items-center justify-between gap-2">
                <Link href={researchHref(r.id)} className="font-medium hover:text-accent hover:underline">
                  {r.title}
                </Link>
                <time dateTime={r.created_at} className="num text-sm text-text-muted">
                  {formatDateTime(r.created_at)}
                </time>
              </span>
              {r.headline ? <span className="block text-sm text-text-muted">{r.headline}</span> : null}
              <span className="flex flex-wrap gap-3 text-xs text-text-muted">
                {r.ticker ? (
                  <Link href={researchForTicker(r.ticker)} className="font-mono text-accent hover:underline" title={`Every report on ${r.ticker}`}>
                    {r.ticker}
                  </Link>
                ) : null}
                {r.profile ? <span>profile {r.profile}</span> : null}
                {r.job_id !== null ? (
                  <Link href={jobHref(r.job_id)} className="hover:text-text hover:underline">
                    job #{r.job_id}
                  </Link>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
