"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PreviewBanner, Segmented } from "@/components/data-state";
import { CandleWithActions } from "@/components/markets/candle-with-actions";
import { CorrelationPanel } from "@/components/markets/correlation-panel";
import { EventStudyPanel } from "@/components/markets/event-study-panel";
import { PriceDiagnosticsPanel } from "@/components/markets/price-diagnostics";
import { SessionStrip } from "@/components/markets/session-strip";
import { PageHeader } from "@/components/page-header";
import { resourceData, useDataResource } from "@/lib/hooks/use-data-resource";
import { loadMarketsPage } from "@/lib/markets/client";

/**
 * Price coverage and corporate-action truth (free-data plan, Phase 3): candles with the
 * action ledger and an explicit adjustment basis, an event study that appears only on a
 * reconciled series, cross-holding correlation, venue-session coverage and per-listing
 * source diagnostics. Fixture-backed until the price read APIs exist.
 */
export function MarketsView() {
  const { state } = useDataResource(loadMarketsPage);
  const data = resourceData(state);
  const [selected, setSelected] = useState<string | null>(null);
  const listing = data ? (data.listings.find((l) => l.listingId === selected) ?? data.listings[0]) : null;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Price coverage"
        description="Defensible prices and returns: raw and adjusted series kept apart, every corporate action sourced, and a gap on a real trading day told apart from a holiday."
        aside={
          data ? (
            <Segmented
              label="Listing"
              value={listing!.listingId}
              onChange={setSelected}
              options={data.listings.map((l) => ({ id: l.listingId, label: `${l.ticker} · ${l.mic}` }))}
            />
          ) : null
        }
      />
      <PreviewBanner endpoint="GET /holdings/{ticker}/prices · /corporate-actions · /calendar/{mic} · /portfolio/correlation" />

      {state.kind === "loading" ? (
        <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading price coverage…
        </p>
      ) : null}
      {state.kind === "error" ? <AlertMessage title="Could not load price coverage">{state.message}</AlertMessage> : null}

      {data && listing ? (
        <>
          <CandleWithActions key={listing.listingId} listing={listing} />
          <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
            <EventStudyPanel key={`e-${listing.listingId}`} listing={listing} />
            <PriceDiagnosticsPanel listing={listing} diagnostics={data.diagnostics[listing.listingId]} calendar={data.calendars[listing.mic]} />
          </div>
          <SessionStrip listing={listing} calendar={data.calendars[listing.mic]} />
          <CorrelationPanel report={data.correlation} />
        </>
      ) : null}
    </div>
  );
}
