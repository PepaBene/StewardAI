"use client";

import Link from "next/link";
import { useMemo } from "react";

import { Sankey } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { directionLabel, knockOnSankey, linkCounts, parseKnockOn } from "@/lib/graph/knock-on";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";

const fetchKnockOn = () => getBrowserApiClient().GET("/portfolio/relationship-knock-on", { params: { query: {} } });

/**
 * Knock-on between your own holdings: confirmed, dated customer and supplier links where both
 * companies are open positions. Suppliers on the left, customers on the right; every ribbon is
 * one link (the reported share is in the table, as reported, never summed).
 */
export function KnockOnSection() {
  const { state } = useApiResource(fetchKnockOn);
  const data = dataOf(state);
  const knock = useMemo(() => (data ? parseKnockOn(data) : null), [data]);
  const sankey = useMemo(() => (knock ? knockOnSankey(knock.links) : null), [knock]);
  const counts = useMemo(() => (knock ? linkCounts(knock.links) : []), [knock]);

  return (
    <Panel
      id="knock-on"
      title="Knock-on between holdings"
      description="Where one of your holdings sells to or buys from another, so trouble at one can reach the other. Only confirmed, dated links between two open positions are listed; a missing link means none is confirmed, not that none exists."
    >
      <ResourceNotice state={state} what="holding links" />
      {knock && knock.links.length === 0 ? (
        <EmptyState title="No confirmed links between your holdings">
          Business-network edges appear here once a customer or supplier link between two held companies is confirmed.
        </EmptyState>
      ) : null}
      {knock && knock.links.length > 0 && sankey ? (
        <div className="space-y-4">
          {sankey.links.length > 0 ? (
            <Sankey
              nodes={sankey.nodes}
              links={sankey.links}
              height={Math.max(180, 34 * Math.max(sankey.nodes.filter((n) => n.column === 0).length, sankey.nodes.filter((n) => n.column === 1).length))}
              formatValue={(v) => `${v} link${v === 1 ? "" : "s"}`}
              label="Supplier to customer links between holdings"
            />
          ) : null}
          {sankey.unclear > 0 ? (
            <p className="text-xs text-text-muted">
              {sankey.unclear} link{sankey.unclear === 1 ? " has" : "s have"} no clear direction and {sankey.unclear === 1 ? "is" : "are"} left out of the diagram; they are in the table.
            </p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] border-collapse text-sm">
              <caption className="sr-only">Confirmed links between holdings</caption>
              <thead>
                <tr className="border-b border-border text-left text-xs text-text-muted">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Link</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Type</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Reported share</th>
                  <th scope="col" className="py-1.5 font-medium">Basis and period</th>
                </tr>
              </thead>
              <tbody>
                {knock.links.map((l, i) => (
                  <tr key={`${l.source ?? i}-${i}`} className="border-b border-border-soft">
                    <td className="py-1.5 pr-3">
                      <Link href={`/companies/${encodeURIComponent(l.holder)}`} className="text-accent-ink hover:underline">
                        {directionLabel(l)}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3 text-text-muted">{titleCase(l.relationship)}</td>
                    <td className="num py-1.5 pr-3 text-right">{l.reportedShare === null ? <span className="text-text-muted">Not published</span> : formatPct(l.reportedShare, 0)}</td>
                    <td className="py-1.5 text-xs text-text-muted">
                      {l.reportedShare === null ? "" : `of ${l.shareBasis ?? "an unstated basis"}`}
                      {l.fiscalPeriod ? ` · ${l.fiscalPeriod}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {counts.length > 0 ? (
            <p className="text-xs text-text-muted">
              Most connected: {counts.slice(0, 4).map((c) => `${c.ticker} (supplies ${c.asSupplier}, buys from ${c.asCustomer})`).join("; ")}.
            </p>
          ) : null}
          <ul className="space-y-0.5 text-xs text-text-muted">
            {knock.limitations.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
