import Link from "next/link";

import { StatePlaceholder } from "@/components/data-state";
import type { NetworkSummary, PatentSummary } from "@/lib/holding-data/types";

import { day, HoldingPanel } from "./shared";

const entries = (r: Readonly<Record<string, number>>) => Object.entries(r).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

/**
 * Business network and patents in numbers. Confirmed edges are facts; everything else is a
 * candidate and counted separately, with the confirmed share of all edges as denominator. The
 * ledger, deals, concentration and the patent matrix live on the holding's network page.
 */
export function NetworkPatentsCard({
  network,
  patents,
  ticker,
  asOf,
  knownAsOf,
}: {
  network: NetworkSummary | null;
  patents: PatentSummary | null;
  ticker: string;
  asOf: string | null;
  knownAsOf: string | null;
}) {
  const href = `/holdings/${encodeURIComponent(ticker)}/network`;
  return (
    <HoldingPanel
      id="network"
      kicker="Business network and patents"
      title="Company graph and patent families"
      takeaway={
        network === null
          ? "The company-graph payload was not in a shape this view understands, so no count is shown."
          : `${network.confirmed} of ${network.edges} graph edges are confirmed; ${network.candidates} are candidates, not facts. ${patents ? `${patents.attributed} of ${patents.families} patent families are attributed to the issuer.` : "Patent families could not be read."}`
      }
      action={
        <Link href={href} className="text-xs text-accent-ink hover:underline">
          Open network
        </Link>
      }
      provenance={[
        {
          source: "Company graph, deal events and disclosures (stored, status as of the cutoff)",
          asOf,
          knownAt: knownAsOf,
          method: "Status in force at the cutoff; a confirmation or rejection made later is not applied.",
          state: network === null ? "unavailable" : network.edges === 0 ? "not_covered" : network.confirmed === 0 ? "partial" : "verified",
          reason: network === null ? "Unexpected payload shape." : network.edges === 0 ? "No graph edge is stored for this holding." : network.confirmed === 0 ? "Only candidate edges exist; none is confirmed." : null,
          coverage: network && network.edges > 0 ? { covered: network.confirmed, total: network.edges, unit: "graph edges confirmed" } : null,
        },
        {
          source: "Patent families attributed to the issuer (stored patent records)",
          asOf,
          knownAt: patents?.latestKnownAt ?? null,
          method: "Counts and citations are signals, not patent valuation or freedom-to-operate advice.",
          state: patents === null ? "unavailable" : patents.families === 0 ? "not_covered" : "verified",
          reason: patents === null ? "Unexpected payload shape." : patents.families === 0 ? "No patent family is attributed to the issuer by the cutoff." : null,
          coverage: patents && patents.families > 0 ? { covered: patents.attributed, total: patents.families, unit: "families attributed" } : null,
        },
      ]}
    >
      {network === null && patents === null ? (
        <StatePlaceholder state="unavailable" reason="Neither the company graph nor the patent families could be read." minHeight={80} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="min-w-0">
            <h3 className="kicker">Graph edges by status</h3>
            {network === null ? (
              <p className="text-sm text-text-muted">Unavailable.</p>
            ) : network.edges === 0 ? (
              <p className="text-sm text-text-muted">No edge stored. Deals: {network.events}, disclosures: {network.disclosures}.</p>
            ) : (
              <>
                <ul className="num mt-1 text-sm">
                  {entries(network.byStatus).map(([k, n]) => (
                    <li key={k}>{k}: {n}</li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-text-muted">Events: {network.events} · Disclosures: {network.disclosures}</p>
                {network.limitations.map((l) => (
                  <p key={l} className="text-xs text-text-muted">{l}</p>
                ))}
              </>
            )}
          </div>
          <div className="min-w-0">
            <h3 className="kicker">Patent families</h3>
            {patents === null ? (
              <p className="text-sm text-text-muted">Unavailable.</p>
            ) : patents.families === 0 ? (
              <p className="text-sm text-text-muted">No family attributed by the cutoff.</p>
            ) : (
              <>
                <p className="num mt-1 text-sm">{patents.families} families, {patents.attributed} attributed to the issuer</p>
                <ul className="num text-xs text-text-muted">
                  {entries(patents.byLegalStatus).map(([k, n]) => (
                    <li key={k}>{k}: {n}</li>
                  ))}
                </ul>
                <p className="text-xs text-text-muted">Latest record known {day(patents.latestKnownAt)}</p>
              </>
            )}
          </div>
        </div>
      )}
    </HoldingPanel>
  );
}
