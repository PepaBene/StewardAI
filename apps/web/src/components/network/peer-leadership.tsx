import { ChartFrame } from "@/components/data-state";
import { HBar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { leadershipEntries, rankPeers, rowsState } from "@/lib/network/logic";
import type { BusinessNetwork, PatentOverlapRow } from "@/lib/network/types";

import { RowStatusBadge, SourceLink } from "./badges";

/**
 * Peers ranked by technology overlap (a ranked comparison, not a network drawing). The rank
 * rests on confirmed peers only (the API's rule), and a peer without patent data is listed
 * unranked, not as zero overlap.
 */
export function PeerRanking({ rows, asOfDate }: { rows: readonly PatentOverlapRow[]; asOfDate: string | null }) {
  const ranked = rankPeers(rows);
  const rankedCount = ranked.filter((r) => r.ranked).length;
  return (
    <ChartFrame
      id="network-peers"
      kicker="Peers"
      title="Technology overlap with confirmed peers"
      takeaway={
        rows.length === 0
          ? "No confirmed peer or competitor edge is stored, so there is nothing to compare."
          : `${rankedCount} of ${rows.length} confirmed peers have patent data; the top peer shares ${formatPct(ranked[0]?.overlapShare ?? 0, 0)} of this company's families by CPC subclass.`
      }
      units="Overlap share: part of this company's attributed families with at least one CPC subclass the peer also files in. Jaccard: shared subclasses over all subclasses."
      provenance={{
        source: "Patent families by CPC subclass, confirmed peer set and competitor edges",
        asOf: asOfDate,
        method: "Technology overlap on CPC subclass; not a freedom-to-operate or infringement assessment.",
        state: rows.length === 0 ? "not_covered" : rankedCount < rows.length ? "partial" : "verified",
        reason: rows.length === 0 ? "Confirm peers (peer set or competitor edges) to enable the comparison." : null,
        coverage: rows.length ? { covered: rankedCount, total: rows.length, unit: "peers with patent data" } : null,
      }}
      hasValue={rows.length > 0}
      table={{
        caption: "Peers ranked by technology overlap",
        columns: ["Peer", "Rank", "Overlap share", "Jaccard", "Peer families", "Own families", "Shared subclasses"],
        rows: ranked.map((r) => [
          r.peer,
          r.rank ?? "unranked",
          r.overlapShare === null ? "no data" : formatPct(r.overlapShare, 1),
          r.jaccard === null ? "no data" : r.jaccard.toFixed(2),
          r.peerFamilies,
          r.ownFamilies,
          r.sharedSubclasses.join(", ") || "none",
        ]),
      }}
    >
      <ol className="flex flex-col gap-2">
        {ranked.map((r) => (
          <li key={r.peer} className="grid grid-cols-[2rem_minmax(0,1fr)] items-center gap-x-2 gap-y-0.5 sm:grid-cols-[2rem_8rem_minmax(0,1fr)_6rem]">
            <span className="num text-text-muted">{r.rank ?? "–"}</span>
            <span className="min-w-0 truncate font-medium">{r.peer}</span>
            <span className="col-span-2 sm:col-span-1">
              <HBar value={r.overlapShare} min={0} max={1} label={`${r.peer} overlap share`} formatValue={(v) => formatPct(v, 0)} />
            </span>
            <span className="num col-span-2 text-xs text-text-muted sm:col-span-1 sm:text-right">
              {r.overlapShare === null ? "no patent data" : `${formatPct(r.overlapShare, 0)} · J ${r.jaccard?.toFixed(2) ?? "–"}`}
            </span>
          </li>
        ))}
      </ol>
    </ChartFrame>
  );
}

export function LeadershipPanel({ net, asOfDate, knownAt }: { net: Pick<BusinessNetwork, "edges" | "events">; asOfDate: string | null; knownAt: string | null }) {
  const entries = leadershipEntries(net);
  const rows = entries.map((e) => ({ status: e.status }));
  return (
    <Blueprint as="section" aria-labelledby="network-leadership-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Leadership" title={<span id="network-leadership-heading">Executives and leadership changes</span>} />
      <p className="max-w-prose text-sm text-text">
        {entries.length === 0
          ? "No executive edge or leadership event is stored for this company."
          : `${entries.filter((e) => e.status === "confirmed").length} of ${entries.length} entries are confirmed against an issuer or regulator disclosure; the rest are candidates.`}
      </p>
      {entries.length > 0 ? (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <caption className="sr-only">Executives and leadership changes, most recent start first</caption>
            <thead className="text-[11px] tracking-[0.08em] text-text-muted uppercase">
              <tr className="border-b border-border text-left">
                <th scope="col" className="px-2 py-1.5 font-medium">Person</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Role</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Tenure</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Status</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.key} className="border-b border-border-soft align-top">
                  <th scope="row" className="px-2 py-1.5 text-left font-medium">{e.name}</th>
                  <td className="px-2 py-1.5">{e.role ?? "role not stated"}</td>
                  <td className="num px-2 py-1.5 text-xs">
                    {e.from ? formatDate(e.from) : "start unknown"} to {e.to ? formatDate(e.to) : "present"}
                  </td>
                  <td className="px-2 py-1.5"><RowStatusBadge status={e.status} /></td>
                  <td className="px-2 py-1.5 text-xs"><SourceLink href={e.sourceUrl}>{e.source}</SourceLink></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <ProvenanceFooter
        provenance={{
          source: "Executive edges and leadership events (issuer or regulator disclosures confirm; Diffbot and Wikidata only suggest)",
          asOf: asOfDate,
          knownAt,
          method: "A leadership change is confirmed only against a current issuer or regulator disclosure.",
          state: entries.length === 0 ? "not_covered" : rowsState(rows),
        }}
      />
    </Blueprint>
  );
}
