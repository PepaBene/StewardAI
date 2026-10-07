"use client";

import Link from "next/link";
import { useCallback } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { ALERT_HREF, ALERT_KIND_LABEL, byKnown, whyLines } from "@/lib/disclosure/alerts";
import { formatDay } from "@/lib/disclosure/model";
import { useApiResource } from "@/lib/hooks/use-api-resource";

/**
 * GET /disclosures/alerts for one holding: what was filed that you have not read yet, with the
 * rule that fired. It states what was filed, never why anyone acted.
 */
export function DisclosureAlertsPanel({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/disclosures/alerts", { params: { query: { ticker, limit: 20 } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  const alerts = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const shown = alerts ? byKnown(alerts) : [];

  return (
    <Blueprint aria-labelledby="disclosure-alerts-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading kicker="Public disclosures" title={<span id="disclosure-alerts-heading">Unread disclosure alerts</span>} />
      {state.kind === "loading" && !alerts ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !alerts ? <p className="text-sm text-text-muted">Disclosure alerts unavailable: {state.title}</p> : null}
      {alerts && shown.length === 0 ? <p className="text-sm text-text-muted">Nothing unread for {ticker}. New filings appear here after the next collection.</p> : null}
      {shown.length > 0 ? (
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {shown.map((a) => (
            <li key={a.id} className="space-y-0.5 py-2 text-sm">
              <p className="font-medium">{a.headline}</p>
              <p className="text-xs text-text-muted">
                {ALERT_KIND_LABEL[a.kind]} · known <span className="num">{formatDay(a.known_at)}</span> · {whyLines(a.why_fired)[0]}
              </p>
              <p className="text-xs">
                <Link href={ALERT_HREF[a.kind]} className="text-accent-ink hover:underline">Open the full view</Link>
                {a.source_url ? (
                  <>
                    {" "}
                    <a href={a.source_url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">Open the filing</a>
                  </>
                ) : null}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs">
        <Link href="/disclosures/alerts" className="text-accent-ink hover:underline">All disclosure alerts</Link>
      </p>
    </Blueprint>
  );
}
