"use client";

import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { OutstandingNote } from "@/components/reference/outstanding-note";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  edgarFilingUrl,
  formatChange,
  formatShares,
  formatUsdCompact,
  OWNERSHIP_NOTE,
  ownershipState,
  periodLabel,
} from "@/lib/reference/ownership";
import { formatOutstandingPct } from "@/lib/reference/shares-outstanding";

function Change({ value }: { value: number | string | null }) {
  const c = formatChange(value);
  return (
    <span className="num" title={c.words}>
      {c.text}
      <span className="sr-only"> ({c.words})</span>
    </span>
  );
}

/**
 * Institutional ownership of a holding from SEC Form 13F data sets: holders, shares and value
 * per quarter (newest first) with the change against the quarter before, and the largest
 * holders of the latest quarter. Every figure is dated by its quarter end and filing date.
 */
export function useHoldingOwnership(ticker: string) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/ownership", { params: { path: { ticker } } }),
    [ticker],
  );
  return useApiResource(fetcher);
}

export function OwnershipSection({ resource }: { resource: ReturnType<typeof useHoldingOwnership> }) {
  const { state } = resource;
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = data ? ownershipState(data) : null;

  return (
    <Blueprint aria-labelledby="ownership-heading" className="space-y-3 px-[22px] py-5">
      <div>
        <PanelHeading kicker="Ownership details" title={<span id="ownership-heading">Institutional ownership by quarter</span>} />
        <p className="mt-1 text-xs text-text-muted">{OWNERSHIP_NOTE}</p>
      </div>
      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading 13F ownership…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title={`13F ownership: ${state.title}`}>{state.detail}</AlertMessage> : null}
      {view?.kind === "empty" ? (
        <EmptyState title={data?.applicable === false ? "13F ownership: not applicable" : "No 13F ownership"}>
          {data?.applicable === false ? data.not_applicable_reason : view.reason}
        </EmptyState>
      ) : null}
      {data && view?.kind === "ok" ? (
        <>
          <p className="num text-sm">
            <span className="font-medium">{view.latest.holders}</span> managers held{" "}
            <span className="font-medium">{formatShares(view.latest.shares)}</span> shares worth{" "}
            <span className="font-medium">{formatUsdCompact(view.latest.value_usd)}</span>
            {typeof view.latest.pct_of_shares_outstanding === "number" ? (
              <>
                {" "}
                — <span className="font-medium">{formatOutstandingPct(view.latest.pct_of_shares_outstanding)}</span> of
                shares outstanding
              </>
            ) : null}
            <span className="text-text-muted"> · {periodLabel(view.latest, formatDate)}</span>
          </p>
          <OutstandingNote data={data} />
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[46rem] text-sm">
              <caption className="px-3 pt-2 text-left text-xs text-text-muted">
                Per quarter, newest first; changes are against the quarter before it.
              </caption>
              <thead>
                <tr className="border-b border-border text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Quarter ended</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Filed up to</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Holders</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Change</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">New / exited</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Shares</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Change</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">% of shares out.</th>
                </tr>
              </thead>
              <tbody>
                {view.periods.map((p) => (
                  <tr key={p.period} className="border-b border-border last:border-0">
                    <th scope="row" className="num px-3 py-2 text-left font-normal">
                      {formatDate(p.period)}
                    </th>
                    <td className="num px-3 py-2">{p.latest_filed ? formatDate(p.latest_filed.slice(0, 10)) : "—"}</td>
                    <td className="num px-3 py-2 text-right">{p.holders}</td>
                    <td className="px-3 py-2 text-right">
                      <Change value={p.holders_change} />
                    </td>
                    <td className="num px-3 py-2 text-right">
                      {p.new_holders === null && p.exited_holders === null ? "—" : `+${p.new_holders ?? 0} / −${p.exited_holders ?? 0}`}
                    </td>
                    <td className="num px-3 py-2 text-right">{formatShares(p.shares)}</td>
                    <td className="px-3 py-2 text-right">
                      <Change value={p.shares_change} />
                    </td>
                    <td className="num px-3 py-2 text-right" title={`${p.value_usd} USD`}>
                      {formatUsdCompact(p.value_usd)}
                    </td>
                    <td className="num px-3 py-2 text-right">{formatOutstandingPct(p.pct_of_shares_outstanding)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.top_holders.length > 0 ? (
            <div className="space-y-1">
              <h3 className="text-sm font-medium">
                Largest holders <span className="font-normal text-text-muted">· {periodLabel(view.latest, formatDate)}</span>
              </h3>
              <ol className="divide-y divide-border rounded-lg border border-border bg-surface-raised text-sm">
                {data.top_holders.map((h, i) => {
                  const filing = edgarFilingUrl(h.cik, h.accession);
                  return (
                    <li key={`${h.cik}:${h.accession}`} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                      <span>
                        <span className="num mr-2 text-text-muted">{i + 1}.</span>
                        {h.manager_name ?? `CIK ${h.cik}`}
                      </span>
                      <span className="num flex flex-wrap items-center gap-3 text-text-muted">
                        <span>{formatShares(h.shares)} shares</span>
                        <span>{formatUsdCompact(h.value_usd)}</span>
                        {typeof h.pct_of_shares_outstanding === "number" ? (
                          <span title="of shares outstanding">{formatOutstandingPct(h.pct_of_shares_outstanding)}</span>
                        ) : null}
                        {filing ? (
                          <a href={filing} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                            filing
                            <ExternalLinkIcon aria-hidden="true" className="size-3" />
                            <span className="sr-only">{h.accession} on SEC EDGAR</span>
                          </a>
                        ) : (
                          <span className="font-mono text-xs">{h.accession}</span>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ol>
            </div>
          ) : null}
          <p className="text-xs text-text-muted">Source: {data.source}.</p>
        </>
      ) : null}
    </Blueprint>
  );
}
