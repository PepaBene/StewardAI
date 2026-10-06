"use client";

import { useCallback, useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { isExternalUrl } from "@/lib/sources/sources";
import { SLOT_LABELS, TIER_LABELS, decidedByOwner, parseWebFacts, type WebFact } from "@/lib/reference/web-facts";

/** GET /holdings/{ticker}/web-facts: facts that structured sources left empty, found on reliable web pages. */
export function WebFactsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/web-facts", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state, reload } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const facts = parseWebFacts(raw);
  if (facts.length === 0) return null;

  return (
    <Blueprint aria-labelledby="web-facts-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Found by web research"
        title={<span id="web-facts-heading">Facts from reliable web sources, not from a filing</span>}
      />
      <p className="max-w-[68ch] text-sm text-text-muted">
        These fill gaps the data sources left empty. An AI approver checked each against its source tier and quote; a value
        from a filing or data feed replaces it automatically. You can overturn any decision.
      </p>
      <ul className="divide-y divide-border border border-border">
        {facts.map((f) => (
          <FactRow key={f.id} fact={f} onChanged={reload} />
        ))}
      </ul>
    </Blueprint>
  );
}

function FactRow({ fact, onChanged }: { fact: WebFact; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(status: "approved" | "rejected") {
    setBusy(true);
    setError(null);
    const res = await getBrowserApiClient().POST("/web-facts/{fact_id}/decision", {
      params: { path: { fact_id: fact.id } },
      body: { status },
    });
    setBusy(false);
    if (res.error) setError("Could not save the decision.");
    else onChanged();
  }

  return (
    <li className="flex flex-col gap-1 px-3 py-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-medium">{SLOT_LABELS[fact.slot] ?? fact.slot}</span>
        <span className="num">{fact.value}</span>
        <span className="rounded-sm border border-border px-1.5 text-xs text-text-muted" title={TIER_LABELS[fact.tier] ?? fact.tier}>
          {fact.tier}
        </span>
        <span className="text-xs text-text-muted">
          {fact.status === "approved" ? "Approved" : fact.status === "needs_review" ? "Needs your review" : "Rejected"}
          {decidedByOwner(fact) ? " by you" : " by AI"}
          {fact.confidence !== null ? ` (${fact.confidence.toFixed(2)})` : ""}
        </span>
      </div>
      <blockquote className="max-w-[80ch] border-l-2 border-border pl-3 text-xs text-text-muted">“{fact.quote}”</blockquote>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
        {isExternalUrl(fact.sourceUrl) ? (
          <a href={fact.sourceUrl} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
            {fact.host}
          </a>
        ) : (
          <span>{fact.host}</span>
        )}
        <span>retrieved {formatDate(fact.retrievedAt)}</span>
        {fact.reasons ? <span>{fact.reasons}</span> : null}
      </div>
      <div className="flex gap-2 pt-1">
        {fact.status !== "approved" ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => decide("approved")}>
            Approve
          </Button>
        ) : null}
        {fact.status !== "rejected" ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => decide("rejected")}>
            Reject
          </Button>
        ) : null}
        {error ? <span className="text-xs text-danger">{error}</span> : null}
      </div>
    </li>
  );
}
