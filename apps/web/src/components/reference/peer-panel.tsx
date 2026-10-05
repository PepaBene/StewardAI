"use client";

import { useCallback, useMemo } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { formatPeerValue, parsePeerMetrics, peerDelta, trackPositions, trackRange, type PeerRow } from "@/lib/reference/peers";

/** GET /holdings/{ticker}/peer-metrics: the holding against its owner-confirmed peers. */
export function PeerPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/peer-metrics", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => parsePeerMetrics(raw), [raw]);

  return (
    <Blueprint aria-labelledby="peers-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Peers · as filed"
        title={<span id="peers-heading">How it compares with its peers</span>}
      />
      {state.kind === "loading" && !view ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !view ? (
        <p className="text-sm text-text-muted">
          Peer comparison unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {view && view.confirmedPeers === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No peers are confirmed for this holding yet, so there is nothing to compare against. Confirm peers from the
          suggestions to see a comparison; the comparison never falls back to a market-wide average.
        </p>
      ) : null}

      {view && view.confirmedPeers > 0 ? (
        <>
          <p className="max-w-[68ch] text-sm text-text-muted">
            {view.periodEnd ? `Latest period ending ${formatDate(view.periodEnd)}` : "No period computed"}
            {view.basis ? `, ${view.basis}` : ""}. Peer means use only peers with a comparable period and the same formula.
            Peers: {view.confirmedPeers} confirmed.
          </p>
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[40rem] text-sm">
              <caption className="sr-only">Holding against the mean of comparable confirmed peers</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Metric</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Holding</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Peer mean</th>
                  <th scope="col" className="w-40 px-3 py-2 text-left font-medium">Position (own scale, min to max)</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Peers used</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((r) => (
                  <PeerTableRow key={r.id} row={r} confirmed={view.confirmedPeers} />
                ))}
              </tbody>
            </table>
          </div>
          {view.excludedPeers.length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer text-accent-ink hover:underline">
                {view.excludedPeers.length} peer{view.excludedPeers.length === 1 ? "" : "s"} without a comparable period
              </summary>
              <ul className="mt-2 space-y-1 text-xs text-text-muted">
                {view.excludedPeers.map((e) => (
                  <li key={e.peer}>
                    <span className="font-mono text-text">{e.peer}</span>: {e.reason}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          <p className="text-xs text-text-muted">{view.universe}</p>
        </>
      ) : null}
    </Blueprint>
  );
}

function PeerTableRow({ row, confirmed }: { row: PeerRow; confirmed: number }) {
  const pos = trackPositions(row);
  const range = trackRange(row);
  const delta = peerDelta(row);
  const unavailable = row.holding === null ? (row.holdingReason ?? "Not computable") : null;
  return (
    <tr className="border-b border-border align-top last:border-0">
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <span title={row.formula ?? undefined}>{row.label}</span>
        {unavailable ? <span className="block text-xs text-text-muted">{unavailable}</span> : null}
      </th>
      <td className="num px-3 py-2 text-right">{formatPeerValue(row.holding, row.kind)}</td>
      <td className="num px-3 py-2 text-right">{formatPeerValue(row.peerMean, row.kind)}</td>
      <td className="px-3 py-2">
        {pos ? (
          <span
            role="img"
            aria-label={
              delta === null
                ? "Only one of the two values is available"
                : `Holding is ${delta >= 0 ? "above" : "below"} the peer mean by ${formatPeerValue(Math.abs(delta), row.kind)}`
            }
            className="relative block h-4 w-full"
          >
            <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
            <span className="absolute top-0 h-4 w-px bg-border" style={{ left: `${pos.zero}%` }} />
            {pos.mean !== null ? (
              <span
                className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 border border-text-muted bg-surface"
                style={{ left: `${pos.mean}%` }}
              />
            ) : null}
            {pos.holding !== null ? (
              <span
                className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 bg-accent"
                style={{ left: `${pos.holding}%` }}
              />
            ) : null}
          </span>
        ) : (
          <span className="text-xs text-text-muted">No values to plot</span>
        )}
        {range ? (
          <span className="num mt-0.5 flex justify-between text-[11px] text-text-faint" title="Scale of this row: lowest to highest value shown, zero included">
            <span>{formatPeerValue(range.lo, row.kind)}</span>
            <span>{formatPeerValue(range.hi, row.kind)}</span>
          </span>
        ) : null}
      </td>
      <td className="num px-3 py-2 text-right">
        {row.covered} of {confirmed}
        {row.excluded.length > 0 ? (
          <span className="block text-xs text-text-muted" title={row.excluded.map((e) => `${e.peer}: ${e.reason}`).join("\n")}>
            {row.excluded.length} left out
          </span>
        ) : null}
      </td>
    </tr>
  );
}
