"use client";

import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { FreshnessText } from "@/components/freshness/as-of-label";
import { getBrowserApiClient, type JobSummary, type NewsItemOut } from "@/lib/api/client";
import { byLead, groupByStory } from "@/lib/news/feed-filters";
import { escalationsForTicker } from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";
import { formatDate } from "@/lib/format";
import { ingestFreshness, ingestPrefix } from "@/lib/freshness";
import { useNow } from "@/lib/hooks/use-now";

/** The holding page's latest linked news, one row per story. */
export function HoldingNews({ ticker }: { ticker: string }) {
  const [items, setItems] = useState<NewsItemOut[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [lastIngest, setLastIngest] = useState<JobSummary | null>(null);
  const [nextDueAt, setNextDueAt] = useState<string | null>(null);
  const now = useNow();
  const freshness = ingestFreshness(lastIngest, now, undefined, nextDueAt);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/news", { params: { query: { ticker, limit: 30 } } })
      .then(({ data }) => {
        if (cancelled) return;
        if (data) {
          setItems(data.items);
          setLastIngest(data.last_ingest);
          setNextDueAt(data.next_ingest_due_at ?? null);
        } else setFailed(true);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const stories = items ? groupByStory(items).slice(0, 8) : [];
  return (
    <section aria-labelledby="news-heading" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="news-heading" className="text-lg font-semibold">
          News
        </h2>
        <span className="flex flex-wrap gap-3">
          <Link href={escalationsForTicker(ticker)} className="text-sm text-accent hover:underline">
            Impact notes
          </Link>
          <Link href={`/news?ticker=${encodeURIComponent(ticker)}`} className="text-sm text-accent hover:underline">
            All news for {ticker}
          </Link>
        </span>
      </div>
      {freshness && lastIngest ? (
        <FreshnessText
          value={freshness}
          prefix={ingestPrefix(lastIngest.status)}
          className="text-xs"
        />
      ) : null}
      {failed ? <p className="text-sm text-text-muted">News could not be loaded.</p> : null}
      {items !== null && stories.length === 0 ? (
        <p className="text-sm text-text-muted">No news linked to this holding in the feed window.</p>
      ) : null}
      {stories.length > 0 ? (
        <ul className="divide-y divide-border rounded-lg border border-border bg-surface-raised">
          {stories.map((group) => {
            const [lead] = byLead(group);
            return (
              <li key={lead.story.id} className="flex flex-wrap items-start justify-between gap-2 p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <a href={lead.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 font-medium hover:text-accent hover:underline">
                    {lead.headline}
                    <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
                  </a>
                  <p className="flex flex-wrap items-center gap-x-2 text-text-muted">
                    <span>{lead.outlet_name ?? lead.outlet}</span>
                    <TierBadge tier={lead.tier} />
                    <span className="num">{formatDate(new Date(lead.published_at))}</span>
                    <span>{eventLabel(lead.story.event_type)}</span>
                  </p>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <SeverityBadge severity={lead.story.severity} />
                  <ConfidenceBadge confidence={lead.story.confidence} />
                  <FeedbackControl target={{ kind: "story", id: lead.story.id }} label={`story “${lead.headline}”`} />
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
