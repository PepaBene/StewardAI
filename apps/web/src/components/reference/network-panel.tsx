"use client";

import { useCallback, useMemo } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { parseNetwork, STATUS_LABEL, statusCounts, typeLabel, type EdgeStatus, type NetworkEdge } from "@/lib/graph/network";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

/** Confirmed facts are solid; anything unconfirmed is dashed so it is never read as a fact. */
const STATUS_CHIP: Record<EdgeStatus, string> = {
  confirmed: "border-accent bg-accent-soft text-text",
  suggested: "border-dashed border-border text-text-muted",
  disputed: "border-disputed/60 bg-disputed-subtle text-text",
  rejected: "border-dashed border-border text-text-muted line-through",
};

/** GET /holdings/{ticker}/business-network: sourced relationships and deal events of a holding. */
export function NetworkPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/business-network", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const view = useMemo(() => parseNetwork(raw), [raw]);
  const counts = view ? statusCounts(view.edges) : null;

  return (
    <Blueprint aria-labelledby="network-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Business network · with sources"
        title={<span id="network-heading">Partners, deals and corporate links</span>}
      />
      {state.kind === "loading" && !view ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !view ? (
        <p className="text-sm text-text-muted">
          Business network unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}

      {view && counts && view.edges.length === 0 && view.events.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No relationships or deal events are recorded for this holding. Suggestions from Diffbot, Wikidata and filings
          appear here for review; none counts as a fact until you confirm it against a source.
        </p>
      ) : null}

      {view && counts && view.edges.length > 0 ? (
        <>
          <p className="max-w-[68ch] text-sm text-text-muted">
            {counts.confirmed} confirmed, {counts.suggested} suggested, {counts.disputed} disputed
            {counts.rejected > 0 ? `, ${counts.rejected} rejected` : ""}. Only confirmed links are facts; the rest are
            leads to check against the cited source.
          </p>
          <div className="relative overflow-x-auto border border-border">
            <table className="w-full min-w-[44rem] text-sm">
              <caption className="sr-only">Relationships of the holding, confirmed first</caption>
              <thead className="bg-surface-muted">
                <tr className="border-b border-border text-xs text-text-muted">
                  <th scope="col" className="px-3 py-2 text-left font-medium">Counterparty</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Relationship</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Status</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Valid</th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {view.edges.map((e) => (
                  <EdgeRow key={e.id} edge={e} />
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      {view && view.events.length > 0 ? (
        <div className="space-y-1.5">
          <h3 className="text-sm font-semibold">Deal and company events</h3>
          <ul className="divide-y divide-border-soft border border-border">
            {view.events.map((ev) => (
              <li key={ev.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-3 py-2 text-sm">
                <span>
                  {typeLabel(ev.type)}
                  {ev.state ? <span className="text-text-muted">, {ev.state.replaceAll("_", " ")}</span> : null}
                  {ev.counterparty ? <span className="text-text-muted"> with {ev.counterparty}</span> : null}
                  {ev.status && ev.status !== "confirmed" ? (
                    <span className={cn("ml-2 inline-block border px-1.5 py-0.5 text-xs", STATUS_CHIP[ev.status])}>{STATUS_LABEL[ev.status]}</span>
                  ) : null}
                </span>
                <span className="num text-xs text-text-muted">
                  {ev.date ? formatDate(ev.date) : "Date not stated"}
                  {ev.sourceUrl ? (
                    <>
                      {", "}
                      <a href={ev.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
                        {ev.source ?? "Source"}
                      </a>
                    </>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {view && view.limitations.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-text-muted">
          {view.limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
      ) : null}
    </Blueprint>
  );
}

function EdgeRow({ edge }: { edge: NetworkEdge }) {
  return (
    <tr className="border-b border-border align-top last:border-0">
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <span className="font-medium">{edge.counterparty}</span>
        {edge.direction && edge.direction !== "outgoing" ? <span className="block text-xs text-text-muted">{edge.direction}</span> : null}
      </th>
      <td className="px-3 py-2">{typeLabel(edge.type)}</td>
      <td className="px-3 py-2">
        <span className={cn("inline-block border px-1.5 py-0.5 text-xs font-medium", STATUS_CHIP[edge.status])}>{STATUS_LABEL[edge.status]}</span>
        {edge.reason ? <span className="block text-xs text-text-muted">{edge.reason}</span> : null}
      </td>
      <td className="num px-3 py-2 text-xs text-text-muted">
        {edge.validFrom ? formatDate(edge.validFrom) : "Start not stated"}
        {edge.validTo ? ` to ${formatDate(edge.validTo)}` : ""}
      </td>
      <td className="max-w-[24rem] px-3 py-2 text-xs">
        <span className="text-text-muted">
          {edge.tier ? `${edge.tier}, ` : ""}
          {edge.source ?? "source not recorded"}
        </span>
        {edge.sourceUrl && /^https?:/.test(edge.sourceUrl) ? (
          <>
            {" "}
            <a href={edge.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
              Open
            </a>
          </>
        ) : null}
        {edge.passage ? <span className="mt-0.5 block text-text-muted">&ldquo;{edge.passage}&rdquo;</span> : null}
      </td>
    </tr>
  );
}
