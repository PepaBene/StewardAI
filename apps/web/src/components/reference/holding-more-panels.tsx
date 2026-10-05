"use client";

import { IdentityActions } from "@/components/reference/identity-actions";
import {
  CompanyObservationsPanel,
  DealsPanel,
  EvidenceSummaryPanel,
  PdmrPanel,
  ReferenceHistoryPanel,
  ReturnsPanel,
  ThesisKpiLinksPanel,
} from "@/components/reference/holding-evidence-panels";
import { ProviderTriggersPanel } from "@/components/reference/provider-triggers-panel";
import { ResearchContextPanel } from "@/components/reference/research-context-panel";

/** The holding page's panels over the free-source endpoints that had no screen: one mount point in holding-view. */
export function HoldingMorePanels({ ticker }: { ticker: string }) {
  return (
    <>
      <ReturnsPanel ticker={ticker} />
      <DealsPanel ticker={ticker} />
      <ThesisKpiLinksPanel ticker={ticker} />
      <PdmrPanel ticker={ticker} />
      <EvidenceSummaryPanel ticker={ticker} />
      <CompanyObservationsPanel ticker={ticker} />
      <ResearchContextPanel ticker={ticker} />
      <ProviderTriggersPanel ticker={ticker} />
      <IdentityActions ticker={ticker} />
      <ReferenceHistoryPanel ticker={ticker} />
    </>
  );
}
