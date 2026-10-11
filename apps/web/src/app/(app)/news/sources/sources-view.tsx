"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { NewsTabs } from "@/components/news/news-tabs";
import { TierBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatDateTime } from "@/lib/format";
import { delayLabel, fetchMethods, responseTimeLabel } from "@/lib/news/stats";
import { cn } from "@/lib/utils";

type SourcesHealth = components["schemas"]["SourcesHealth"];
type SourceHealth = components["schemas"]["SourceHealth"];

const STATE_LABELS: Record<SourceHealth["state"], string> = {
  ok: "Working",
  degraded: "Slow or partly failing",
  down: "Unreachable",
  unknown: "Not fetched yet",
  disabled: "Off",
};

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; health: SourcesHealth };

/** Health of every feed. An unreachable outlet is shown as missing news, never as no news. */
export function SourcesView() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    getBrowserApiClient()
      .GET("/news/sources/health")
      .then(({ data, response }) => {
        if (data) setLoad({ kind: "ok", health: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) }));
  }, []);

  const down = load.kind === "ok" ? load.health.sources.filter((s) => s.enabled && s.state === "down") : [];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Sources"
        description={
          <span className="block max-w-prose">
            Every configured feed with its last fetch, how many items it brought in the last week, and what went wrong. Turn
            sources on or off, or add one, under{" "}
            <Link href="/config/news-sources" className="text-accent-ink hover:underline">
              Config, News sources
            </Link>
            .
          </span>
        }
      />
      <NewsTabs active="sources" />
      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading source health…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the sources">{load.message}</AlertMessage> : null}
      {down.length > 0 ? (
        <AlertMessage title={`${down.length} source${down.length === 1 ? " is" : "s are"} unreachable`}>
          News from {down.map((s) => s.name).join(", ")} is missing, not absent: a quiet feed here does not mean a quiet day.
        </AlertMessage>
      ) : null}
      {load.kind === "ok" ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[78rem] text-left text-sm">
            <caption className="sr-only">News sources and their health</caption>
            <thead className="text-xs text-text-muted">
              <tr className="border-b border-border">
                <th scope="col" className="py-2 pr-3 font-normal">Source</th>
                <th scope="col" className="pr-3 font-normal">Tier</th>
                <th scope="col" className="pr-3 font-normal">State</th>
                <th scope="col" className="pr-3 font-normal">Region</th>
                <th scope="col" className="pr-3 font-normal">Last success</th>
                <th scope="col" className="num pr-3 font-normal">Items a day</th>
                <th scope="col" className="num pr-3 font-normal">Kept for holdings</th>
                <th scope="col" className="pr-3 font-normal">Article text, last week</th>
                <th scope="col" className="pr-3 font-normal">Seen after publication</th>
                <th scope="col" className="num pr-3 font-normal">Answer time</th>
                <th scope="col" className="font-normal">Last error</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {load.health.sources.map((s) => (
                <tr key={s.id} className={cn(!s.enabled && "text-text-muted")}>
                  <td className="py-2 pr-3">
                    <span className="block font-medium">{s.name}</span>
                    <span className="block text-xs text-text-muted">
                      {s.type}
                      {s.host ? `, ${s.host}` : ""}
                      {s.paywall !== "open" ? `, ${s.paywall} paywall` : ""}
                      {s.state_media ? ", state media" : ""}
                    </span>
                  </td>
                  <td className="pr-3">
                    <TierBadge tier={s.tier} />
                  </td>
                  <td className={cn("pr-3", s.state === "down" && "font-medium text-text")}>
                    {STATE_LABELS[s.state]}
                    {s.consecutive_failures > 0 ? <span className="block text-xs text-text-muted">{s.consecutive_failures} failures in a row</span> : null}
                    {s.backoff_until ? <span className="block text-xs text-text-muted">retry after {formatDateTime(s.backoff_until)}</span> : null}
                  </td>
                  <td className="pr-3 font-mono text-xs">{[s.region, s.language].filter(Boolean).join(" ") || "none"}</td>
                  <td className="num pr-3">{s.last_success_at ? formatDateTime(s.last_success_at) : "never"}</td>
                  <td className="num pr-3">{(s.fetched_7d / 7).toFixed(1)}</td>
                  <td className="num pr-3">{s.stored_linked_7d}</td>
                  <td className="pr-3 text-xs">
                    {fetchMethods(s.fulltext_by_rung_7d)}
                    {(s.fulltext_truncated_7d ?? 0) > 0 ? <span className="block text-text-muted">{s.fulltext_truncated_7d} cut off</span> : null}
                    {(s.fulltext_failed_7d ?? 0) > 0 ? <span className="block text-text-muted">{s.fulltext_failed_7d} not fetched</span> : null}
                  </td>
                  <td className="pr-3 text-xs">{delayLabel(s.delay_p50_min, s.delay_p95_min)}</td>
                  <td className="num pr-3 text-xs">{responseTimeLabel(s.avg_latency_ms)}</td>
                  <td className="text-xs [overflow-wrap:anywhere]">{s.last_error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
