"use client";

import { ResourceBoundary } from "@/components/financials/resource";
import { PeerTable } from "@/components/financials/peer-table";
import { PriceDiagnosticsPanel } from "@/components/markets/price-diagnostics";
import { useHoldingMacro } from "@/lib/holding-data/hooks";
import { mergeGaps } from "@/lib/holding-data/logic";
import { parseNetworkSummary, parsePatentSummary } from "@/lib/holding-data/parse";
import type { EvidenceSummary, HoldingDataContext } from "@/lib/holding-data/types";
import { usePeerMetrics } from "@/lib/financials/hooks";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import { useHoldingListing, usePriceDiagnostics } from "@/lib/markets/use-markets";

import { EvidenceLedger } from "./evidence-ledger";
import { ExposureCard } from "./exposure-card";
import { FinancialHistoryCard } from "./financial-history-card";
import { GapsList } from "./gaps-list";
import { ScrollToHash } from "./shared";
import { HoldingAsOfBar } from "./holding-as-of-bar";
import { NetworkPatentsCard } from "./network-patents-card";
import { OwnershipSummaryCard } from "./ownership-summary-card";
import { SourceDisagreementsCard } from "./source-disagreements-card";

/** The data a resource currently has: the latest result, or the previous one next to an error. */
function last<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

export { HoldingAsOfBar };

/**
 * Data-context sections of the holding page: financial history and peers, country/trade
 * exposure, business network and patents, source disagreements with price-source diagnostics,
 * and the explicit gaps. One request each for data-context and evidence (owned by the page,
 * passed in); macro, peers and price diagnostics reuse the existing hooks of their own views.
 */
export function DataContextSections({ ticker, asOf, context, evidence }: { ticker: string; asOf: string; context: ResourceState<HoldingDataContext>; evidence: ResourceState<EvidenceSummary> }) {
  const macro = useHoldingMacro(ticker, asOf);
  const peers = usePeerMetrics(ticker, asOf || null, false);
  const diagnostics = usePriceDiagnostics(ticker);
  const listing = useHoldingListing(ticker);
  const data = last(context);
  const gaps = mergeGaps(data?.gaps, last(evidence)?.gaps);
  const macroData = last(macro.state);
  const diag = last(diagnostics.state);
  const listingParsed = last(listing.state);
  const listingData = listingParsed?.ok ? listingParsed.data : null;

  return (
    <div className="flex min-w-0 flex-col gap-5" id="data-context">
      <ResourceBoundary state={context} label="Data context">
        {(ctx) => (
          <>
            <ScrollToHash />
            <FinancialHistoryCard history={ctx.financial_history} ticker={ticker} />
            <ResourceBoundary state={peers.state} label="Peer card">
              {(p) => <PeerTable data={p} ticker={ticker} />}
            </ResourceBoundary>
            <ExposureCard macro={macroData} trade={ctx.trade_exposure} ticker={ticker} />
            <NetworkPatentsCard
              network={parseNetworkSummary(ctx.business_network)}
              patents={parsePatentSummary(ctx.patents)}
              ticker={ticker}
              asOf={ctx.known_as_of.slice(0, 10)}
              knownAsOf={ctx.known_as_of}
            />
            <SourceDisagreementsCard disagreements={ctx.source_disagreements} coverage={ctx.coverage} asOf={ctx.known_as_of.slice(0, 10)} />
          </>
        )}
      </ResourceBoundary>
      <div id="price-sources" className="scroll-mt-4">
        <ResourceBoundary state={diagnostics.state} label="Price-source diagnostics">
          {() => <PriceDiagnosticsPanel ticker={ticker} rows={diag ?? []} listing={listingData} listingNote={listing.state.kind === "loading" ? "Loading the derived listing…" : listingParsed && !listingParsed.ok ? `The listing response did not match the expected shape (${listingParsed.reason}).` : listing.state.kind === "error" ? (listing.state.status === 404 ? "No listing has been derived for this holding yet." : `The listing could not be read: ${listing.state.title}.`) : null} />}
        </ResourceBoundary>
      </div>
      <GapsList gaps={gaps} complete={context.kind === "ok" && evidence.kind === "ok"} loading={context.kind === "loading" || evidence.kind === "loading"} />
    </div>
  );
}

/** Ownership summary and the evidence ledger, from the one evidence-summary request. */
export function EvidenceSections({ ticker, evidence }: { ticker: string; evidence: ResourceState<EvidenceSummary> }) {
  return (
    <ResourceBoundary state={evidence} label="Evidence">
      {(s) => (
        <>
          <ScrollToHash />
          <OwnershipSummaryCard summary={s} ticker={ticker} />
          <EvidenceLedger summary={s} />
        </>
      )}
    </ResourceBoundary>
  );
}
