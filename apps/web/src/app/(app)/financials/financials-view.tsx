"use client";

import { LoaderCircleIcon } from "lucide-react";

import { AlertMessage } from "@/components/alert-message";
import { PreviewBanner } from "@/components/data-state";
import { CashWaterfall } from "@/components/financials/cash-waterfall";
import { DebtMaturityChart } from "@/components/financials/debt-maturity-chart";
import { EarningsCard } from "@/components/financials/earnings-card";
import { EstimatesFan } from "@/components/financials/estimates-fan";
import { PeerTable } from "@/components/financials/peer-table";
import { SegmentTreemapPanel } from "@/components/financials/segment-treemap-panel";
import { StatementExplorer } from "@/components/financials/statement-explorer";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { loadFinancialsPage } from "@/lib/financials/client";
import { resourceData, useDataResource } from "@/lib/hooks/use-data-resource";

/**
 * Fundamentals, sectors and the estimates boundary (free-data plan, Phase 4): the as-filed
 * statement explorer, cash-to-earnings waterfall, debt maturities, segment treemap, estimates
 * with actual / guidance / consensus / scenario kept visibly apart, actual-versus-estimate
 * cards and a peer comparison on one definition per metric. Fixture-backed until the
 * fundamentals read APIs exist.
 */
export function FinancialsView() {
  const { state } = useDataResource(loadFinancialsPage);
  const data = resourceData(state);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Financials"
        description="Comparable company histories and honest forward-looking fields: values as filed, restatements visible, and no estimate that no source published."
      />
      <PreviewBanner endpoint="GET /holdings/{ticker}/statements · /segments · /debt-maturities · /estimates · /peers" />

      {state.kind === "loading" ? (
        <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading financials…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title="Could not load financials">{state.message}</AlertMessage> : null}

      {data ? (
        <>
          <StatementExplorer set={data.statements} />
          <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
            <CashWaterfall walk={data.cashWalk} source="10-K FY2025, cash-flow statement" sourceUrl={data.statements.sourceUrl} />
            <DebtMaturityChart schedule={data.debt} sourceUrl={data.statements.sourceUrl} />
          </div>
          <SegmentTreemapPanel set={data.segments} sourceUrl={data.statements.sourceUrl} />
          <SegmentTreemapPanel set={data.segmentsMisaligned} sourceUrl={data.statements.sourceUrl} />
          <EstimatesFan set={data.estimates} />
          <Blueprint as="section" aria-labelledby="earnings-heading" className="flex flex-col gap-3 px-5 py-5">
            <PanelHeading kicker="Latest report" title={<span id="earnings-heading">Earnings against the available estimate</span>} />
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {data.earnings.map((e) => (
                <EarningsCard key={e.metric} event={e} />
              ))}
            </div>
          </Blueprint>
          <PeerTable set={data.peers} />
        </>
      ) : null}
    </div>
  );
}
