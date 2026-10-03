"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { AsOfControl } from "@/components/network/as-of-control";
import { ChangesList } from "@/components/network/changes-list";
import { ConcentrationPanel, KnockOnPanel } from "@/components/network/concentration-panel";
import { DealsTimeline } from "@/components/network/deals-timeline";
import { EdgeLedger } from "@/components/network/edge-ledger";
import { IdentityPanel } from "@/components/network/identity-panel";
import { LeadershipPanel, PeerRanking } from "@/components/network/peer-leadership";
import { PatentMatrix } from "@/components/network/patent-matrix";
import { RecordForms } from "@/components/network/record-forms";
import { RefreshActions } from "@/components/network/refresh-actions";
import { ResourceBoundary } from "@/components/network/resource-boundary";
import { ReviewPanel } from "@/components/network/review-queue";
import { KpiLinksPanel, ResearchPanel } from "@/components/network/thesis-links-panel";
import { PageHeader } from "@/components/page-header";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  fetchBusinessNetwork,
  fetchConcentration,
  fetchDeals,
  fetchIdentity,
  fetchKnockOn,
  fetchKpiLinks,
  fetchMonthlyReviews,
  fetchObservations,
  fetchPatentOverlap,
  fetchPatentTrends,
  fetchReconciliations,
  fetchReviewQueue,
} from "@/lib/network/client";
import { asOfParam, isIsoDate, latestDate } from "@/lib/network/logic";

/**
 * Business network and innovation for one holding (free-data plan, Phase 5): a time-aware
 * relationship ledger, ranked peers and leadership, the patent landscape, deals and
 * financing, customer/supplier concentration from confirmed disclosures, and passage-level
 * review. Every figure comes from the API; each panel loads, fails and stays empty on its own.
 */
export function NetworkView({ ticker }: { ticker: string }) {
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [asOf, setAsOf] = useState("");
  const asOfDate = isIsoDate(asOf) ? asOf : null;
  const asOfApi = asOfParam(asOf);
  const [sinceOverride, setSinceOverride] = useState<string | null>(null);

  const network = useApiResource(useCallback(() => fetchBusinessNetwork(ticker, asOfApi), [ticker, asOfApi]));
  const deals = useApiResource(useCallback(() => fetchDeals(ticker, asOfApi), [ticker, asOfApi]));
  const concentration = useApiResource(useCallback(() => fetchConcentration(ticker, asOfApi), [ticker, asOfApi]));
  const trends = useApiResource(useCallback(() => fetchPatentTrends(ticker), [ticker]));
  const overlap = useApiResource(useCallback(() => fetchPatentOverlap(ticker, asOfApi), [ticker, asOfApi]));
  const kpiLinks = useApiResource(useCallback(() => fetchKpiLinks(ticker, asOfApi), [ticker, asOfApi]));
  const observations = useApiResource(useCallback(() => fetchObservations(ticker, asOfApi), [ticker, asOfApi]));
  const reconciliations = useApiResource(useCallback(() => fetchReconciliations(ticker), [ticker]));
  const queue = useApiResource(useCallback(() => fetchReviewQueue(ticker), [ticker]));
  const knockOn = useApiResource(useCallback(() => fetchKnockOn(asOfApi), [asOfApi]));
  const identity = useApiResource(useCallback(() => fetchIdentity(ticker), [ticker]));
  const reviews = useApiResource(fetchMonthlyReviews);

  const reloadAll = useCallback(() => {
    for (const r of [network, deals, concentration, trends, overlap, kpiLinks, observations, reconciliations, queue, knockOn]) r.reload();
  }, [network, deals, concentration, trends, overlap, kpiLinks, observations, reconciliations, queue, knockOn]);

  const lastReview = reviews.state.kind === "ok" ? latestDate(reviews.state.data.map((r) => r.created_at)) : null;
  const defaultSince = lastReview ? lastReview.slice(0, 10) : "";
  const since = sinceOverride ?? defaultSince;
  const sinceOrigin = lastReview ? `Defaults to the latest monthly review, ${formatDate(lastReview.slice(0, 10))}.` : null;

  const trendRows = trends.state.kind === "ok" && trends.state.data.ok ? trends.state.data.value : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title={`Business network and innovation: ${ticker}`}
        description="Typed, dated relationships with their evidence, deals, patents and leadership. Suggested relationships are shown as candidates and never count as facts until you confirm them against the source passage."
        aside={
          <Link href={`/holdings/${encodeURIComponent(ticker)}`} className="text-accent-ink hover:underline">
            Back to {ticker}
          </Link>
        }
      />
      <AsOfControl value={asOf} onChange={setAsOf} today={today} />
      {asOfDate ? (
        <AlertMessage tone="info" title={`Viewing the network as known on ${formatDate(asOfDate)}`}>
          Rows learned of and decisions made after that day are not applied. The patent timing table ignores this date. Decisions you make here apply now.
        </AlertMessage>
      ) : null}

      <ResourceBoundary state={network.state} title="Relationships and patents" kicker="Entity graph">
        {(net) => (
          <>
            <EdgeLedger ticker={ticker} edges={net.edges} asOfDate={asOfDate} limitations={net.limitations} knownAt={net.knownAsOf} onChanged={reloadAll} />
            <ChangesList net={net} since={since} onSince={setSinceOverride} sinceOrigin={sinceOrigin} asOfDate={asOfDate} />
            <LeadershipPanel net={net} asOfDate={asOfDate} knownAt={net.knownAsOf} />
            <PatentMatrix patents={net.patents} trends={trendRows} asOfDate={asOfDate} knownAt={net.knownAsOf} limitations={[]} />
            <ReviewPanel ticker={ticker} disclosures={net.disclosures} queue={queue.state.kind === "ok" && queue.state.data.ok ? queue.state.data.value : null} asOfDate={asOfDate} onChanged={reloadAll} />
          </>
        )}
      </ResourceBoundary>

      <ResourceBoundary state={overlap.state} title="Peer comparison" kicker="Peers">
        {(rows) => <PeerRanking rows={rows} asOfDate={asOfDate} />}
      </ResourceBoundary>

      <ResourceBoundary state={deals.state} title="Deals and financing" kicker="Transactions">
        {(data) => <DealsTimeline ticker={ticker} data={data} asOfDate={asOfDate} onChanged={reloadAll} />}
      </ResourceBoundary>

      <ResourceBoundary state={concentration.state} title="Customer and supplier concentration" kicker="Customers and suppliers">
        {(data) => <ConcentrationPanel data={data} asOfDate={asOfDate} />}
      </ResourceBoundary>

      <ResourceBoundary state={knockOn.state} title="Links to other holdings" kicker="Portfolio">
        {(data) => <KnockOnPanel ticker={ticker} data={data} asOfDate={asOfDate} />}
      </ResourceBoundary>

      <ResourceBoundary state={kpiLinks.state} title="Events linked to thesis KPIs" kicker="Thesis">
        {(links) => <KpiLinksPanel links={links} asOfDate={asOfDate} />}
      </ResourceBoundary>

      <ResourceBoundary state={observations.state} title="Company research" kicker="Company research">
        {(obs) => (
          <ResearchPanel
            observations={obs}
            reconciliations={reconciliations.state.kind === "ok" && reconciliations.state.data.ok ? reconciliations.state.data.value : []}
            asOfDate={asOfDate}
          />
        )}
      </ResourceBoundary>

      {identity.state.kind === "ok" ? <IdentityPanel identity={identity.state.data} asOfParam={asOfApi} asOfDate={asOfDate} /> : null}
      {identity.state.kind === "error" ? (
        <AlertMessage title="Legal structure is unavailable">{identity.state.title}. {identity.state.detail}</AlertMessage>
      ) : null}

      <RecordForms ticker={ticker} onRecorded={reloadAll} />
      <RefreshActions ticker={ticker} onDone={reloadAll} />
    </div>
  );
}
