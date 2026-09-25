"use client";

import { ExternalLinkIcon, LoaderCircleIcon, PlayIcon, RefreshCwIcon, ScaleIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { FreshnessText } from "@/components/freshness/as-of-label";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { Button } from "@/components/ui/button";
import {
  getBrowserApiClient,
  type Holding,
  type JobSummary,
  type NewsFeed,
  type NewsItemOut,
} from "@/lib/api/client";
import { CONFIDENCE } from "@/lib/evidence/confidence";
import {
  byLead,
  EMPTY_FILTERS,
  filtersToSearch,
  groupByStory,
  isFiltered,
  parseFilters,
  toggle,
  type FeedFilters,
} from "@/lib/news/feed-filters";
import { CONFIDENCES, eventLabel, SEVERITY, STATUS_LABELS, TIERS } from "@/lib/news/labels";
import { formatRelative } from "@/lib/format";
import { ingestFreshness, ingestPrefix } from "@/lib/freshness";
import { jobHref } from "@/lib/jobs/job-detail";
import { disputedStoryHref } from "@/lib/news/disputed";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

const PAGE = 50;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: NewsItemOut[]; next: number | null; lastIngest: JobSummary | null };

function query(filters: FeedFilters, beforeId: number | null) {
  return {
    ticker: filters.ticker ?? undefined,
    severity: filters.severities.length ? filters.severities : undefined,
    confidence: filters.confidences.length ? filters.confidences : undefined,
    tier: filters.tiers.length ? filters.tiers : undefined,
    story: filters.story ?? undefined,
    before_id: beforeId ?? undefined,
    limit: PAGE,
  };
}

async function fetchFeed(filters: FeedFilters, beforeId: number | null): Promise<NewsFeed> {
  const { data, response } = await getBrowserApiClient().GET("/news", {
    params: { query: query(filters, beforeId) },
  });
  if (!data) throw new Error(`The API answered HTTP ${response.status}.`);
  return data;
}

export function NewsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => parseFilters(new URLSearchParams(params.toString())), [params]);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [queued, setQueued] = useState<{ text: string; jobId: number | null } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const setFilters = useCallback(
    (next: FeedFilters) => router.replace(`${pathname}${filtersToSearch(next)}`, { scroll: false }),
    [router, pathname],
  );

  useEffect(() => {
    let cancelled = false;
    fetchFeed(filters, null)
      .then((feed) => {
        if (!cancelled) {
          setLoad({ kind: "ok", items: feed.items, next: feed.next_before_id, lastIngest: feed.last_ingest });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [filters, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  async function loadMore() {
    if (load.kind !== "ok" || load.next === null) return;
    const feed = await fetchFeed(filters, load.next);
    setLoad({ ...load, items: [...load.items, ...feed.items], next: feed.next_before_id });
  }

  async function runNow() {
    setQueued(null);
    const { data, response } = await getBrowserApiClient().POST("/jobs/news-ingest");
    setQueued(
      data
        ? { text: `News check queued (job #${data.id}). Reload in a minute to see new items.`, jobId: data.id }
        : { text: `Could not queue: HTTP ${response.status}.`, jobId: null },
    );
  }

  const groups = load.kind === "ok" ? groupByStory(load.items) : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">News</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Items matched to your holdings, grouped by story. Severity comes from the event rules;
            the confidence label from the corroboration rules. Only confirmed severity-3 stories alert.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          {load.kind === "ok" ? <IngestFreshness job={load.lastIngest} /> : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link href="/news/disputed">
                <ScaleIcon aria-hidden="true" />
                Disputed stories
              </Link>
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
              <RefreshCwIcon aria-hidden="true" />
              Reload
            </Button>
            <Button variant="outline" size="sm" onClick={() => void runNow()}>
              <PlayIcon aria-hidden="true" />
              Check news now
            </Button>
          </div>
        </div>
      </div>
      {queued ? (
        <AlertMessage tone="info" title="Queued">
          {queued.text}{" "}
          {queued.jobId !== null ? (
            <Link href={jobHref(queued.jobId)} className="text-accent hover:underline">
              Follow the job
            </Link>
          ) : null}
        </AlertMessage>
      ) : null}

      <Filters filters={filters} holdings={holdings} onChange={setFilters} />

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading news…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the news">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && groups.length === 0 ? (
        <EmptyState title={isFiltered(filters) ? "Nothing matches these filters" : "No news yet"}>
          {isFiltered(filters)
            ? "Clear a filter to see more."
            : "The news check runs every two hours from 06:00 to 22:00. Add feeds under Config / News sources."}
        </EmptyState>
      ) : null}

      {groups.length > 0 ? (
        <ol className="divide-y divide-border rounded-lg border border-border bg-surface-raised" aria-label="News stories">
          {groups.map((group) => (
            <StoryRow key={group[0].story.id} items={group} onStory={(id) => setFilters({ ...EMPTY_FILTERS, story: id })} />
          ))}
        </ol>
      ) : null}
      {load.kind === "ok" && load.next !== null ? (
        <Button variant="outline" onClick={() => void loadMore()}>
          Load older items
        </Button>
      ) : null}
    </div>
  );
}

function IngestFreshness({ job }: { job: JobSummary | null }) {
  const now = useNow();
  const value = ingestFreshness(job, now);
  if (!job || !value) return <p className="text-sm text-text-muted">No news check has run yet.</p>;
  return (
    <div className="flex flex-col items-end gap-0.5">
      <FreshnessText value={value} prefix={ingestPrefix(job.status)} />
      <Link href={jobHref(job.id)} className="text-xs text-accent hover:underline">
        Last check: job #{job.id}
      </Link>
    </div>
  );
}

function Filters({
  filters,
  holdings,
  onChange,
}: {
  filters: FeedFilters;
  holdings: Holding[];
  onChange: (next: FeedFilters) => void;
}) {
  const chip = (active: boolean) =>
    cn(
      "inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-sm has-focus-visible:ring-2 has-focus-visible:ring-focus",
      active ? "border-accent bg-surface-muted font-medium text-text" : "border-border text-text-muted hover:text-text",
    );
  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-3 rounded-lg border border-border bg-surface-raised p-3">
      <label className="space-y-1 text-sm">
        <span className="block font-medium">Holding</span>
        <select
          value={filters.ticker ?? ""}
          onChange={(e) => onChange({ ...filters, story: null, ticker: e.target.value || null })}
          className="rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text"
        >
          <option value="">All holdings</option>
          {filters.ticker && !holdings.some((h) => h.ticker === filters.ticker) ? (
            <option value={filters.ticker}>{filters.ticker}</option>
          ) : null}
          {holdings.map((h) => (
            <option key={h.ticker} value={h.ticker}>
              {h.name ?? h.ticker}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Severity</legend>
        <div className="flex flex-wrap gap-1">
          {([3, 2, 1, 0] as const).map((level) => (
            <label key={level} className={chip(filters.severities.includes(level))}>
              <input
                type="checkbox"
                className="sr-only"
                checked={filters.severities.includes(level)}
                onChange={() => onChange({ ...filters, story: null, severities: toggle(filters.severities, level) })}
              />
              {level} · {SEVERITY[level].label}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Confidence</legend>
        <div className="flex flex-wrap gap-1">
          {CONFIDENCES.map((c) => (
            <label key={c} className={chip(filters.confidences.includes(c))}>
              <input
                type="checkbox"
                className="sr-only"
                checked={filters.confidences.includes(c)}
                onChange={() => onChange({ ...filters, story: null, confidences: toggle(filters.confidences, c) })}
              />
              {CONFIDENCE[c].label}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Source tier</legend>
        <div className="flex flex-wrap gap-1">
          {([1, 2, 3] as const).map((tier) => (
            <label key={tier} className={chip(filters.tiers.includes(tier))} title={TIERS[tier].description}>
              <input
                type="checkbox"
                className="sr-only"
                checked={filters.tiers.includes(tier)}
                onChange={() => onChange({ ...filters, story: null, tiers: toggle(filters.tiers, tier) })}
              />
              {TIERS[tier].label}
            </label>
          ))}
        </div>
      </fieldset>
      {isFiltered(filters) ? (
        <Button variant="ghost" size="sm" className="self-end" onClick={() => onChange(EMPTY_FILTERS)}>
          <XIcon aria-hidden="true" />
          Clear filters
        </Button>
      ) : null}
      {filters.story ? (
        <p className="basis-full text-sm text-text-muted">Showing one story (#{filters.story}) and its copies.</p>
      ) : null}
    </div>
  );
}

function StoryRow({ items: group, onStory }: { items: NewsItemOut[]; onStory: (id: number) => void }) {
  const now = useNow();
  const items = byLead(group);
  const lead = items[0];
  const story = lead.story;
  const copies = items.slice(1);
  const published = new Date(lead.published_at);
  const corroborating = story.corroborating_urls ?? [];
  const contradicting = story.contradicting_urls ?? [];
  return (
    <li className="space-y-2 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <a
            href={lead.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-start gap-1 font-medium text-text hover:text-accent hover:underline"
          >
            <span>{lead.headline}</span>
            <ExternalLinkIcon aria-hidden="true" className="mt-1 size-3.5 shrink-0 text-text-muted" />
          </a>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
            <span>{lead.outlet_name ?? lead.outlet ?? lead.source_id}</span>
            <TierBadge tier={lead.tier} />
            <span aria-hidden="true">·</span>
            <time dateTime={lead.published_at} className="num" title={published.toLocaleString()}>
              {formatRelative(published, now)}
            </time>
            <span aria-hidden="true">·</span>
            <span>{eventLabel(story.event_type ?? lead.event_type)}</span>
            {story.rumour ? <span className="rounded-sm border border-border px-1 text-xs">rumour</span> : null}
            {story.recycled ? <span className="rounded-sm border border-border px-1 text-xs">recycled</span> : null}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <SeverityBadge severity={story.severity} />
          <ConfidenceBadge confidence={story.confidence} />
        </div>
      </div>
      {lead.summary ? <p className="max-w-prose text-sm text-text-muted">{lead.summary}</p> : null}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {lead.links.map((link) => (
          <Link
            key={link.ticker}
            href={`/holdings/${encodeURIComponent(link.ticker)}`}
            title={`Linked by ${link.via}: ${link.term}`}
            className={cn(
              "rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs hover:border-accent",
              link.ticker === story.ticker && "bg-surface-muted",
            )}
          >
            {link.ticker}
            {link.via !== "name" && link.via !== "source" ? <span className="font-sans text-text-muted"> · {link.via}</span> : null}
          </Link>
        ))}
        <span className="text-text-muted">
          {STATUS_LABELS[story.status] ?? story.status}
          {story.status_reason ? `: ${story.status_reason}` : ""}
        </span>
        {story.status === "disputed" ? (
          <Link href={disputedStoryHref(story.id)} className="text-accent hover:underline">
            Compare the versions
          </Link>
        ) : null}
        {story.source_count > items.length || copies.length > 0 ? (
          <button type="button" onClick={() => onStory(story.id)} className="text-accent hover:underline">
            {story.source_count} {story.source_count === 1 ? "outlet" : "outlets"}
          </button>
        ) : null}
      </div>
      {copies.length > 0 ? (
        <ul className="space-y-0.5 border-l-2 border-border pl-3 text-sm" aria-label="Copies of this story">
          {copies.map((copy) => (
            <li key={copy.id} className="flex flex-wrap items-center gap-2">
              <a href={copy.url} target="_blank" rel="noreferrer" className="text-text-muted hover:text-accent hover:underline">
                {copy.outlet_name ?? copy.outlet ?? copy.source_id}
              </a>
              <TierBadge tier={copy.tier} />
            </li>
          ))}
        </ul>
      ) : null}
      {corroborating.length > 0 || contradicting.length > 0 ? (
        <ul className="space-y-0.5 text-sm">
          {corroborating.map((url) => (
            <li key={url}>
              Corroborated by{" "}
              <a href={url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                {new URL(url).hostname.replace(/^www\./, "")}
              </a>
            </li>
          ))}
          {contradicting.map((url) => (
            <li key={url}>
              Contradicted by{" "}
              <a href={url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                {new URL(url).hostname.replace(/^www\./, "")}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}
