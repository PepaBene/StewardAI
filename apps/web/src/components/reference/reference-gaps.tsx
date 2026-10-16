"use client";

import { CircleCheckIcon, ClockIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

const fetchGaps = () => getBrowserApiClient().GET("/reference/gaps");
const LINK = "text-accent underline-offset-4 hover:underline";

/**
 * Instruments whose identity could not be resolved and symbols Stooq returns no history
 * for: never skipped silently. Shown on /status, which the Activity page links to.
 */
export function ReferenceGapsPanel() {
  const { state, reload } = useApiResource(fetchGaps);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const empty = data !== null && data.identity.length === 0 && data.prices.length === 0;

  return (
    <section id="reference-gaps" aria-labelledby="reference-gaps-heading" className="max-w-3xl scroll-mt-24 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="reference-gaps-heading" className="text-[22px] leading-tight font-semibold">
            Reference data gaps
          </h2>
          <p className="text-sm text-text-muted">
            Holdings whose identity is incomplete, and symbols with no price history. They are listed here until the
            Activity page arrives.
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
          Loading gaps…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
        </AlertMessage>
      ) : null}
      {empty ? (
        <p className="flex items-center gap-2 rounded-md border border-border bg-surface-raised p-3 text-sm">
          <CircleCheckIcon aria-hidden="true" className="size-4 text-neutral-signal" />
          No gaps: every holding is resolved and every symbol has price history.
        </p>
      ) : null}
      {data && data.identity.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="px-3 pt-2 text-left font-medium">
              Identity not resolved <span className="num text-text-muted">({data.identity.length})</span>
            </caption>
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">Holding</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Problem</th>
              </tr>
            </thead>
            <tbody>
              {data.identity.map((g) => (
                <tr key={g.ticker} className="border-b border-border align-top last:border-0">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    <Link href={`/companies/${encodeURIComponent(g.ticker)}/network#identity-heading`} className={LINK}>
                      {g.name ?? g.ticker}
                    </Link>
                    {g.name ? <span className="ml-1 font-mono text-xs text-text-muted">{g.ticker}</span> : null}
                  </th>
                  <td className="px-3 py-1.5">{g.problem}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {data && data.prices.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="px-3 pt-2 text-left font-medium">
              No price history <span className="num text-text-muted">({data.prices.length})</span>
            </caption>
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">Holding or benchmark</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Kind</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Stooq symbol</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {data.prices.map((g) => (
                <tr key={g.key} className="border-b border-border align-top last:border-0">
                  <th scope="row" className="px-3 py-1.5 text-left font-mono font-normal">
                    {g.kind === "benchmark" ? (
                      g.key
                    ) : (
                      <Link href={`/companies/${encodeURIComponent(g.key)}/network#prices-heading`} className={LINK}>
                        {g.key}
                      </Link>
                    )}
                  </th>
                  <td className="px-3 py-1.5">{g.kind === "benchmark" ? "Benchmark" : "Holding"}</td>
                  <td className="px-3 py-1.5 font-mono">{g.symbol ?? "—"}</td>
                  <td className="px-3 py-1.5">{g.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {data?.prices_checked_at ? (
        <p className="flex items-center gap-1.5 text-xs text-text-muted">
          <ClockIcon aria-hidden="true" className="size-3.5" />
          <span className="num">Prices checked {formatDateTime(data.prices_checked_at)}</span>
        </p>
      ) : null}
      {data && data.prices.length > 0 ? (
        <p className="text-xs text-text-muted">
          Fix a wrong symbol with the Stooq price symbol in{" "}
          <Link href="/config/holding-profiles" className={LINK}>
            Config: Holding profiles
          </Link>
          ; benchmarks are set in{" "}
          <Link href="/config/jobs#prices-heading" className={LINK}>
            Config: Jobs
          </Link>
          .
        </p>
      ) : null}
    </section>
  );
}
