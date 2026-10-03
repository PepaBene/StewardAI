"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { HoldingSelector } from "@/components/financials/holding-selector";
import { FundamentalsSections, PeerSections, ProbeSection, ValuationSection } from "@/components/financials/sections";
import { PageHeader } from "@/components/page-header";
import { useHoldingsList } from "@/lib/financials/hooks";
import { displayTicker } from "@/lib/portfolio/holdings-view";

/**
 * Fundamentals, sectors and the estimates boundary (free-data plan, Phase 4), for one holding
 * chosen from the portfolio: as-filed statements with a point-in-time ("as known on") cut-off
 * the API applies, cash-to-earnings walk and debt maturities only from filed facts, segments,
 * estimates with actual / guidance / consensus / scenario / calendar estimate kept apart,
 * actual-versus-consensus, and a peer comparison with covered/excluded counts. Every figure
 * comes from the backend; where it has nothing, the state says so.
 */
export function FinancialsView() {
  const params = useSearchParams();
  const { state } = useHoldingsList();
  const holdings = useMemo(() => (state.kind === "ok" ? state.data.holdings : state.kind === "error" && state.previous ? state.previous.holdings : []), [state]);
  const [picked, setPicked] = useState<string | null>(params.get("ticker"));
  const ticker = picked ?? holdings[0]?.ticker ?? null;
  const name = holdings.find((h) => h.ticker === ticker)?.name ?? (ticker ? displayTicker(ticker) : "");
  const [asOf, setAsOf] = useState<string | null>(null);
  const [reconciledOnly, setReconciledOnly] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const today = new Date().toISOString().slice(0, 10);

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Financials"
        description="Comparable company histories and honest forward-looking fields: values as filed, restatements visible, and no estimate that no source published."
      />
      <div role="group" aria-label="Financials scope" className="flex min-w-0 flex-wrap items-end gap-x-5 gap-y-3 border border-border-soft px-4 py-3">
        <HoldingSelector
          holdings={holdings}
          value={ticker}
          onChange={setPicked}
          status={state.kind === "error" ? { kind: "error", title: state.title } : state.kind === "loading" ? { kind: "loading" } : { kind: "ok" }}
        />
        <label className="flex flex-col gap-0.5 text-xs text-text-muted">
          <span>As known on (point in time)</span>
          <span className="flex items-center gap-1.5">
            <input
              type="date"
              value={asOf ?? ""}
              max={today}
              onChange={(e) => setAsOf(e.target.value === "" ? null : e.target.value)}
              className="num border border-border bg-surface-raised px-2 py-1 text-sm text-text"
            />
            {asOf ? (
              <button type="button" onClick={() => setAsOf(null)} className="border border-border px-2 py-1 text-xs font-semibold outline-hidden hover:bg-border-soft focus-visible:outline-2 focus-visible:outline-focus">
                Latest
              </button>
            ) : null}
          </span>
        </label>
        <label className="flex items-center gap-2 pb-1.5 text-xs text-text-muted">
          <input type="checkbox" checked={reconciledOnly} onChange={(e) => setReconciledOnly(e.target.checked)} />
          <span>Reconciled facts only</span>
        </label>
        <p className="min-w-0 max-w-prose pb-1 text-xs text-text-muted">
          {asOf ? `The API returns only what was known on ${asOf}: nothing filed later is fetched.` : "Showing the latest stored view."} Applies to statements, forward data, IR releases and peers.
        </p>
      </div>

      {ticker ? (
        <div key={`${ticker}:${refresh}`} className="flex min-w-0 flex-col gap-5">
          <FundamentalsSections ticker={ticker} name={name} asOf={asOf} reconciledOnly={reconciledOnly} />
          <PeerSections ticker={ticker} name={name} asOf={asOf} reconciledOnly={reconciledOnly} />
          <ValuationSection ticker={ticker} />
        </div>
      ) : (
        <p role="status" className="text-sm text-text-muted">
          {state.kind === "loading" ? "Loading holdings…" : "No holdings are available to choose from."}
        </p>
      )}
      {ticker ? <ProbeSection ticker={ticker} onDone={() => setRefresh((k) => k + 1)} /> : null}
    </div>
  );
}
