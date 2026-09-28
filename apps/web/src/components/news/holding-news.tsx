"use client";

import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { SeverityDot } from "@/components/news/severity-timeline";
import { FreshnessText } from "@/components/freshness/as-of-label";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type JobSummary, type NewsItemOut } from "@/lib/api/client";
import { byLead, groupByStory } from "@/lib/news/feed-filters";
import { escalationsForTicker } from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";
import { formatDate } from "@/lib/format";
import { ingestFreshness, ingestPrefix } from "@/lib/freshness";
import { useNow } from "@/lib/hooks/use-now";
import { isExternalUrl } from "@/lib/sources/sources";

export type HoldingNewsData = {
  items: NewsItemOut[] | null;
  failed: boolean;
  lastIngest: JobSummary | null;
  nextDueAt: string | null;
};

/** The holding's linked news (`GET /news?ticker=`), read once for the timeline and the list. */
export function useHoldingNews(ticker: string): HoldingNewsData {
  const [items, setItems] = useState<NewsItemOut[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [lastIngest, setLastIngest] = useState<JobSummary | null>(null);
  const [nextDueAt, setNextDueAt] = useState<string | null>(null);

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
  return { items, failed, lastIngest, nextDueAt };
}

/** The holding page's latest linked news, one row per story. */
export function HoldingNews({ ticker, news }: { ticker: string; news: HoldingNewsData }) {
  const { items, failed, lastIngest, nextDueAt } = news;
  const now = useNow();
  const freshness = ingestFreshness(lastIngest, now, undefined, nextDueAt);
  const stories = items ? groupByStory(items).slice(0, 8) : [];
  return (
    <Blueprint aria-labelledby="news-heading" className="space-y-3 px-[22px] py-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <PanelHeading kicker="News" title={<span id="news-heading">Linked stories</span>} />
        <span className="flex flex-wrap gap-3">
          <Link href={escalationsForTicker(ticker)} className="text-sm text-accent-ink hover:underline">
            Impact notes
          </Link>
          <Link href={`/news?ticker=${encodeURIComponent(ticker)}`} className="text-sm text-accent-ink hover:underline">
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
        <ul className="divide-y divide-border-soft border-y border-border-soft">
          {stories.map((group) => {
            const [lead] = byLead(group);
            return (
              <li key={lead.story.id} className="flex flex-wrap items-start justify-between gap-2 py-2.5 text-sm">
                <div className="min-w-0 flex-1">
                  <span className="flex items-start gap-1.5">
                    <SeverityDot severity={lead.story.severity} className="mt-1" />
                    {isExternalUrl(lead.url) ? (
                      <a href={lead.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 font-medium hover:text-accent-ink hover:underline">
                        {lead.headline}
                        <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
                      </a>
                    ) : (
                      <span className="font-medium">{lead.headline}</span>
                    )}
                  </span>
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
    </Blueprint>
  );
}
