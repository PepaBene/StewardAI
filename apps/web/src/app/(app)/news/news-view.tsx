"use client";

import { ChevronDownIcon, ExternalLinkIcon, LoaderCircleIcon, PlayIcon, RefreshCwIcon, ScaleIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Sparkline } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { FreshnessText } from "@/components/freshness/as-of-label";
import { UpdatesFeedPanel } from "@/components/insight/updates-feed";
import { NewsSummary, type Loaded } from "@/components/news/news-summary";
import { NewsTabs } from "@/components/news/news-tabs";
import { TierBadge } from "@/components/news/severity-badge";
import { SeverityBlocks } from "@/components/news/severity-timeline";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { FilterChips } from "@/components/ui/filter-chips";
import {
  getBrowserApiClient,
  type Holding,
  type JobSummary,
  type NewsFeed,
  type NewsItemOut,
  type SourceTrustOut,
} from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatPct } from "@/lib/charts/format";
import { CONFIDENCE } from "@/lib/evidence/confidence";
import { formatDateTime } from "@/lib/format";
import { ingestFreshness, ingestPrefix } from "@/lib/freshness";
import { useNow } from "@/lib/hooks/use-now";
import { jobHref } from "@/lib/jobs/job-detail";
import { latestOnly } from "@/lib/latest-request";
import { disputedStoryHref } from "@/lib/news/disputed";
import { decodeEntities } from "@/lib/news/entities";
import { dayLabel, eventTypeCounts, groupByDay, storyEventType } from "@/lib/news/feed-days";
import {
  byLead,
  EMPTY_FILTERS,
  feedScope,
  filtersToSearch,
  groupByStory,
  isFiltered,
  parseFilters,
  splitLinks,
  toggle,
  type FeedFilters,
} from "@/lib/news/feed-filters";
import { CONFIDENCES, eventLabel, SEVERITY, STATUS_LABELS, TIERS } from "@/lib/news/labels";
import { priceReaction, storyDay, type ClosePoint } from "@/lib/news/price-reaction";
import { activePreset, healthSummary, PRESETS, presetFilters, sourceBars, storyTime } from "@/lib/news/stats";
import { shortTicker } from "@/lib/portfolio/ticker";
import { appendUnique } from "@/lib/portfolio/transactions";
import { hostOf, isExternalUrl } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

const PAGE = 50;
const STATS_DAYS = 14;
const PRICE_DAYS = 30;
// The news-like updates; dividends, valuation and risk cards live on the dashboard and Inbox.
const NEWS_FEED_TYPES = ["news", "earnings", "analyst", "insider"] as const;

type NewsStats = components["schemas"]["NewsStats"];
type SourcesHealth = components["schemas"]["SourcesHealth"];
type PriceMap = Map<string, ClosePoint[]>;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | {
      kind: "ok";
      items: NewsItemOut[];
      next: number | null;
      lastIngest: JobSummary | null;
      /** When the next scheduled news check is due: the feed is current until then. */
      nextIngestDueAt: string | null;
      loadingMore: boolean;
      moreError: string | null;
    };

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function query(filters: FeedFilters, beforeId: number | null) {
  const scope = feedScope(filters);
  return {
    ticker: filters.ticker ?? undefined,
    direct: scope.direct,
    severity: scope.severity,
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

/** One GET for a summary panel, as a Loaded state. */
function useLoaded<T>(fetcher: () => Promise<{ data?: T; response: Response }>, reloadKey: number): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ kind: "loading" });
  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then(({ data, response }) => {
        if (cancelled) return;
        setState(data !== undefined ? { kind: "ok", data } : { kind: "error", message: `HTTP ${response.status}` });
      })
      .catch((err: unknown) => !cancelled && setState({ kind: "error", message: message(err) }));
    return () => {
      cancelled = true;
    };
  }, [fetcher, reloadKey]);
  return state;
}

const fetchStats = () => getBrowserApiClient().GET("/news/stats", { params: { query: { days: STATS_DAYS } } });
const fetchHealth = () => getBrowserApiClient().GET("/news/sources/health");
const fetchTrust = () => getBrowserApiClient().GET("/sources/trust");
const fetchPrices = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: PRICE_DAYS } } });

export function NewsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => parseFilters(new URLSearchParams(params.toString())), [params]);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [queued, setQueued] = useState<{ text: string; jobId: number } | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueing, setQueueing] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [feedOpen, setFeedOpen] = useState(false);
  // Event-type chip: filters the loaded stories (the feed API has no event-type filter).
  const [eventType, setEventType] = useState<string | null>(null);
  const now = useNow();
  // Only the newest load's answers are applied (filters can change while a page loads).
  const sequence = useRef(0);

  const stats = useLoaded<NewsStats>(fetchStats, reloadKey);
  const health = useLoaded<SourcesHealth>(fetchHealth, reloadKey);
  const trust = useLoaded<SourceTrustOut[]>(fetchTrust, reloadKey);
  const pricesState = useLoaded(fetchPrices, reloadKey);
  const prices: PriceMap = useMemo(
    () => new Map(pricesState.kind === "ok" ? pricesState.data.holdings.map((h) => [h.ticker, h.bars]) : []),
    [pricesState],
  );

  const setFilters = useCallback(
    (next: FeedFilters) => router.replace(`${pathname}${filtersToSearch(next)}`, { scroll: false }),
    [router, pathname],
  );

  useEffect(() => {
    const mine = ++sequence.current;
    // A changed filter or a reload must not show the old page under the new filter.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoad({ kind: "loading" });
    fetchFeed(filters, null)
      .then((feed) => {
        if (mine === sequence.current) {
          setLoad({
            kind: "ok",
            items: feed.items,
            next: feed.next_before_id,
            lastIngest: feed.last_ingest,
            nextIngestDueAt: feed.next_ingest_due_at ?? null,
            loadingMore: false,
            moreError: null,
          });
        }
      })
      .catch((err: unknown) => {
        if (mine === sequence.current) setLoad({ kind: "error", message: message(err) });
      });
    return () => {
      sequence.current += 1;
    };
  }, [filters, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  async function loadMore() {
    if (load.kind !== "ok" || load.next === null || load.loadingMore) return;
    const beforeId = load.next;
    setLoad({ ...load, loadingMore: true, moreError: null });
    const outcome = await latestOnly(sequence, () => fetchFeed(filters, beforeId));
    if (outcome.stale) return;
    setLoad((prev) => {
      if (prev.kind !== "ok") return prev;
      if (!outcome.ok) return { ...prev, loadingMore: false, moreError: outcome.error };
      return { ...prev, items: appendUnique(prev.items, outcome.value.items), next: outcome.value.next_before_id, loadingMore: false, moreError: null };
    });
  }

  async function runNow() {
    setQueueing(true);
    setQueued(null);
    setQueueError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/news-ingest");
      if (data) setQueued({ text: `News check queued (job #${data.id}). Reload in a minute to see new items.`, jobId: data.id });
      else setQueueError(`Could not queue: the API answered HTTP ${response.status}.`);
    } catch (err) {
      setQueueError(message(err));
    } finally {
      setQueueing(false);
    }
  }

  const allGroups = load.kind === "ok" ? groupByStory(load.items) : [];
  const typeCounts = eventTypeCounts(allGroups);
  const groups = eventType === null ? allGroups : allGroups.filter((g) => (storyEventType(g) ?? "") === eventType);
  // An empty feed is only "no news yet" when the sources are known to be healthy.
  const sourcesNote =
    health.kind === "error"
      ? `Source health could not be checked (${health.message}), so an empty feed may mean the sources are failing.`
      : health.kind === "ok" && healthSummary(health.data).failing.length > 0
        ? `${healthSummary(health.data).failing.length} of ${healthSummary(health.data).enabled} enabled sources are unhealthy, so an empty feed may mean they are failing. See the feed health above.`
        : null;
  const preset = activePreset(filters);
  const disputed = stats.kind === "ok" ? stats.data.disputed_count : null;

  const chip = (active: boolean) =>
    cn(
      "inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[13px] font-medium whitespace-nowrap transition-colors",
      active ? "bg-accent text-accent-foreground" : "bg-surface-muted text-text-muted hover:text-text",
    );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="News"
        description="Stories clustered across feeds, filings and IR pages, classified and corroborated before they alert. Only confirmed severity-3 stories alert."
        aside={
          <nav aria-label="Quick filters" className="flex flex-wrap gap-1.5">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                title={p.title}
                aria-pressed={preset === p.id}
                className={chip(preset === p.id)}
                onClick={() => setFilters(presetFilters(p.id))}
              >
                {p.label}
              </button>
            ))}
            <Link href="/inbox?kind=disputed" className={chip(false)} title={`Stories whose sources contradict each other, last ${STATS_DAYS} days. You settle them in the Inbox.`}>
              <ScaleIcon aria-hidden="true" className="size-3.5" />
              Disputed{disputed !== null ? <span className="num text-text-faint">{disputed}</span> : null}
            </Link>
          </nav>
        }
      />
      <NewsTabs active="feed" />

      <div className="flex flex-wrap items-center justify-between gap-3">
        {load.kind === "ok" ? <IngestFreshness job={load.lastIngest} nextDueAt={load.nextIngestDueAt} /> : <span />}
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
          <Button variant="outline" size="sm" onClick={() => void runNow()} disabled={queueing}>
            {queueing ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
            Check news now
          </Button>
        </div>
      </div>
      {queued ? (
        <AlertMessage tone="info" title="Queued">
          {queued.text}{" "}
          <Link href={jobHref(queued.jobId)} className="text-accent-ink hover:underline">
            Follow the job
          </Link>
        </AlertMessage>
      ) : null}
      {queueError ? <AlertMessage title="Not queued">{queueError}</AlertMessage> : null}

      <NewsSummary stats={stats} health={health} trust={trust} now={now} />

      <details className="group" onToggle={(e) => setFeedOpen(e.currentTarget.open)}>
        <summary className="cursor-pointer text-sm text-accent-ink">Updates on your holdings, as typed cards</summary>
        {/* Mounted on first open so the feed is only fetched when it is read. */}
        {feedOpen ? <UpdatesFeedPanel className="mt-3" title="Updates on your holdings" limit={20} types={NEWS_FEED_TYPES} /> : null}
      </details>

      <Filters filters={filters} holdings={holdings} onChange={setFilters} />

      {typeCounts.length > 1 ? (
        <FilterChips<string>
          label="Event type"
          value={eventType ?? "*"}
          onChange={(k) => setEventType(k === "*" ? null : k)}
          chips={[
            { key: "*", label: "All types", count: allGroups.length },
            ...typeCounts.map((t) => ({ key: t.type, label: eventLabel(t.type || null), count: t.count })),
          ]}
        />
      ) : null}

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading news…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the news">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && groups.length === 0 ? (
        <EmptyState title={isFiltered(filters) ? "Nothing matches these filters" : sourcesNote ? "No news, and the sources may be failing" : "No news yet"}>
          {isFiltered(filters)
            ? "Clear a filter to see more."
            : sourcesNote ?? "The news check runs every two hours from 06:00 to 22:00. Add feeds under Config / News sources."}
        </EmptyState>
      ) : null}

      {groups.length > 0 ? (
        <Blueprint as="section" aria-label="News stories">
          <div
            aria-hidden="true"
            className="hidden border-b border-border-soft px-5 py-2.5 text-xs text-text-muted lg:grid lg:grid-cols-[4rem_7rem_minmax(0,1fr)_4.5rem_8.5rem_8.5rem] lg:gap-x-4"
          >
            <span>Time</span>
            <span>Severity</span>
            <span>Story and holdings</span>
            <span className="text-right">Sources</span>
            <span>Confidence</span>
            <span className="text-right">Price reaction</span>
          </div>
          {groupByDay(groups).map((day) => (
            <section key={day.day} aria-label={dayLabel(day.at, now)}>
              <h3 className="sticky top-14 z-10 border-b border-border-soft bg-surface px-5 py-2 text-[13px] font-semibold text-text">
                {dayLabel(day.at, now)} <span className="num font-normal text-text-faint">{day.groups.length}</span>
              </h3>
              <ol className="m-0 list-none p-0">
                {day.groups.map((group) => (
                  <StoryRow
                    key={group[0].story.id}
                    items={group}
                    now={now}
                    prices={prices}
                    onStory={(id) => setFilters({ ...EMPTY_FILTERS, story: id })}
                  />
                ))}
              </ol>
            </section>
          ))}
        </Blueprint>
      ) : null}
      {load.kind === "ok" && load.moreError ? <AlertMessage title="Could not load older items">{load.moreError}</AlertMessage> : null}
      {load.kind === "ok" && load.next !== null ? (
        <div>
          <Button variant="outline" onClick={() => void loadMore()} disabled={load.loadingMore}>
            {load.loadingMore ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            Load older items
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function IngestFreshness({ job, nextDueAt }: { job: JobSummary | null; nextDueAt: string | null }) {
  const now = useNow();
  const value = ingestFreshness(job, now, undefined, nextDueAt);
  if (!job || !value) return <p className="text-sm text-text-muted">No news check has run yet.</p>;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm">
      <FreshnessText value={value} prefix={ingestPrefix(job.status)} />
      <Link href={jobHref(job.id)} className="text-xs text-accent-ink hover:underline">
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
      "inline-flex cursor-pointer items-center gap-1.5 border px-2 py-1 text-[13px] has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus",
      active ? "border-accent bg-accent-soft font-medium text-text" : "border-border text-text-muted hover:text-text",
    );
  return (
    <Blueprint as="section" aria-label="Filters" className="flex flex-wrap items-start gap-x-6 gap-y-3 px-5 py-4">
      <label className="space-y-1 text-sm">
        <span className="kicker block">Holding</span>
        <select
          value={filters.ticker ?? ""}
          onChange={(e) => onChange({ ...filters, story: null, ticker: e.target.value || null })}
          className="h-8 border border-border bg-surface-raised px-2 text-sm text-text"
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
        <legend className="kicker">Severity</legend>
        <div className="flex flex-wrap gap-1">
          {([3, 2, 1, 0] as const).map((level) => (
            <label key={level} className={chip(filters.severities.includes(level))} title={SEVERITY[level].description}>
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
        <legend className="kicker">Confidence</legend>
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
        <legend className="kicker">Source tier</legend>
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
      <fieldset className="space-y-1">
        <legend className="kicker">Also show</legend>
        <div className="flex flex-wrap gap-1">
          <label className={chip(Boolean(filters.related))} title="Stories that reach a holding only through a rival, supplier, customer or watch topic">
            <input type="checkbox" className="sr-only" checked={Boolean(filters.related)} onChange={() => onChange({ ...filters, story: null, related: !filters.related })} />
            About rivals, suppliers and customers
          </label>
          <label className={chip(Boolean(filters.ignored))} title="Stories the classifier judged not worth a digest line (severity 0)">
            <input type="checkbox" className="sr-only" checked={Boolean(filters.ignored)} onChange={() => onChange({ ...filters, story: null, ignored: !filters.ignored })} />
            Ignored stories
          </label>
        </div>
      </fieldset>
      {isFiltered(filters) ? (
        <Button variant="ghost" size="sm" className="self-end" onClick={() => onChange({ ...EMPTY_FILTERS, related: filters.related, ignored: filters.ignored })}>
          <XIcon aria-hidden="true" />
          Clear filters
        </Button>
      ) : null}
      {filters.story ? (
        <p className="basis-full text-sm text-text-muted">Showing one story (#{filters.story}) and its copies.</p>
      ) : null}
    </Blueprint>
  );
}

/** The stored classification, to start a corrected label from. */
function labelDefaults(item: NewsItemOut) {
  return {
    eventType: item.event_type ?? item.story.event_type,
    affectedTicker: item.affected_ticker ?? item.story.ticker,
    relevant: item.relevant,
    rumour: item.rumour ?? item.story.rumour,
  };
}

function StoryRow({
  items: group,
  now,
  prices,
  onStory,
}: {
  items: NewsItemOut[];
  now: Date;
  prices: PriceMap;
  onStory: (id: number) => void;
}) {
  const items = byLead(group);
  const lead = items[0];
  const story = lead.story;
  const copies = items.slice(1);
  const published = new Date(lead.published_at);
  const corroborating = story.corroborating_urls ?? [];
  const contradicting = story.contradicting_urls ?? [];
  const outlet = lead.outlet_name ?? lead.outlet ?? lead.source_id;
  const hasMore = story.source_count > items.length || copies.length > 0;
  const bars = story.ticker ? prices.get(story.ticker) : undefined;
  const reaction = bars ? priceReaction(bars, storyDay(lead.published_at)) : null;

  return (
    <li className="border-b border-border-soft last:border-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5 hover:bg-surface-muted lg:grid lg:grid-cols-[4rem_7rem_minmax(0,1fr)_4.5rem_8.5rem_8.5rem]">
        <time dateTime={lead.published_at} className="num font-mono text-xs text-text-muted" title={published.toLocaleString()}>
          {storyTime(published, now)}
        </time>
        <SeverityBlocks severity={story.severity} />
        <div className="min-w-0 basis-full lg:basis-auto">
          {isExternalUrl(lead.url) ? (
            <a href={lead.url} target="_blank" rel="noreferrer" className="group inline text-[15px] font-medium text-text hover:text-accent-ink hover:underline">
              {decodeEntities(lead.headline)}
              <ExternalLinkIcon aria-hidden="true" className="ml-1 inline size-3.5 align-[-2px] text-text-faint group-hover:text-accent-ink" />
            </a>
          ) : (
            <span className="text-[15px] font-medium text-text">{decodeEntities(lead.headline)}</span>
          )}
          <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-text-muted">
            <span>{eventLabel(story.event_type ?? lead.event_type)}</span>
            <span aria-hidden="true">·</span>
            <span>
              clustered from {story.source_count} {story.source_count === 1 ? "item" : "items"}
            </span>
            <span aria-hidden="true">·</span>
            <span>{outlet}</span>
            <TierBadge tier={lead.tier} />
            {story.rumour ? <span className="border border-border px-1 text-xs">rumour</span> : null}
            {story.recycled ? <span className="border border-border px-1 text-xs">recycled</span> : null}
            <span className="inline-flex items-center gap-1">
              <span aria-hidden="true">·</span> Story
              <FeedbackControl target={{ kind: "story", id: story.id }} label={`story “${decodeEntities(lead.headline)}”`} />
            </span>
          </p>
          <StoryHoldings links={lead.links} storyTicker={story.ticker} />
        </div>
        <SourceCount count={story.source_count} onClick={hasMore ? () => onStory(story.id) : undefined} />
        <span>
          <ConfidenceBadge confidence={story.confidence} />
        </span>
        <Reaction reaction={reaction} ticker={story.ticker} hasPrices={Boolean(bars && bars.length > 0)} />
      </div>

      <details className="group px-5 pb-3 lg:pl-[calc(1.25rem+4rem+7rem+2rem)]">
        <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-xs text-text-muted hover:text-text [&::-webkit-details-marker]:hidden">
          <ChevronDownIcon aria-hidden="true" className="size-3.5 transition-transform group-open:rotate-180" />
          {STATUS_LABELS[story.status] ?? story.status}
          {story.status_reason ? `: ${story.status_reason}` : ""} · summary, sources and feedback
        </summary>
        <div className="mt-2 space-y-2 text-sm">
          {lead.summary ? <p className="max-w-prose text-text-muted">{decodeEntities(lead.summary)}</p> : null}
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
            <span className="inline-flex items-center gap-1">
              This item ({outlet})
              <FeedbackControl target={{ kind: "news_item", id: lead.id }} label={`news item “${decodeEntities(lead.headline)}”`} labelDefaults={labelDefaults(lead)} />
            </span>
            <span className="num">Published {formatDateTime(lead.published_at)}</span>
            {story.status === "disputed" ? (
              <Link href={disputedStoryHref(story.id)} className="text-accent-ink hover:underline">
                Compare the versions
              </Link>
            ) : null}
            {hasMore ? (
              <button type="button" onClick={() => onStory(story.id)} className="text-accent-ink hover:underline">
                {story.source_count} {story.source_count === 1 ? "outlet" : "outlets"}
              </button>
            ) : null}
          </p>
          {copies.length > 0 ? (
            <ul className="space-y-0.5 border-l-2 border-border pl-3" aria-label="Copies of this story">
              {copies.map((copy) => (
                <li key={copy.id} className="flex flex-wrap items-center gap-2">
                  {isExternalUrl(copy.url) ? (
                    <a href={copy.url} target="_blank" rel="noreferrer" className="text-text-muted hover:text-accent-ink hover:underline">
                      {copy.outlet_name ?? copy.outlet ?? copy.source_id}
                    </a>
                  ) : (
                    <span className="text-text-muted">{copy.outlet_name ?? copy.outlet ?? copy.source_id}</span>
                  )}
                  <TierBadge tier={copy.tier} />
                  <FeedbackControl target={{ kind: "news_item", id: copy.id }} label={`news item “${decodeEntities(copy.headline)}”`} labelDefaults={labelDefaults(copy)} />
                </li>
              ))}
            </ul>
          ) : null}
          {corroborating.length > 0 || contradicting.length > 0 ? (
            <ul className="space-y-0.5">
              {corroborating.map((url) => (
                <li key={url}>
                  Corroborated by <SourceLink url={url} />
                </li>
              ))}
              {contradicting.map((url) => (
                <li key={url}>
                  Contradicted by <SourceLink url={url} />
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </details>
    </li>
  );
}

/**
 * The holdings a story is about as short ticker links; those it reaches only through a rival,
 * supplier, customer or topic folded into one muted line, named in full on hover.
 */
function StoryHoldings({ links, storyTicker }: { links: NewsItemOut["links"]; storyTicker: string | null | undefined }) {
  const { about, related } = splitLinks(links, storyTicker);
  if (about.length === 0 && related.length === 0) return null;
  const relatedText = related.map((l) => `${shortTicker(l.ticker)} (${l.via}: ${l.term})`).join(", ");
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
      {about.map((link) => (
        <Link
          key={link.ticker}
          href={`/companies/${encodeURIComponent(link.ticker)}`}
          title={`Named as ${link.term}`}
          className="rounded-sm bg-accent-soft px-1.5 py-0.5 font-semibold text-text hover:text-accent-ink"
        >
          {shortTicker(link.ticker)}
        </Link>
      ))}
      {related.length > 0 ? (
        <span className="text-text-muted" title={relatedText}>
          {about.length > 0 ? "also reaches " : "reaches "}
          {related.length <= 2 ? related.map((l) => `${shortTicker(l.ticker)} as ${l.via}`).join(", ") : `${related.length} holdings through rivals, suppliers or customers`}
        </span>
      ) : null}
    </p>
  );
}

/** Outlet count as five small bars; a button to the single-story view when there are copies. */
function SourceCount({ count, onClick }: { count: number; onClick?: () => void }) {
  const bars = sourceBars(count);
  const inner = (
    <>
      <span aria-hidden="true" className="flex h-[18px] items-end gap-0.5">
        {bars.map((on, i) => (
          <span key={i} className={cn("w-[5px]", on ? "bg-accent" : "bg-border")} style={{ height: on ? 6 + i * 3 : 2 }} />
        ))}
      </span>
      <span className="sr-only">
        {count} {count === 1 ? "outlet" : "outlets"}
      </span>
    </>
  );
  return onClick ? (
    <button type="button" onClick={onClick} title={`${count} outlets: show this story and its copies`} className="flex justify-end lg:w-full">
      {inner}
    </button>
  ) : (
    <span className="flex justify-end lg:w-full" title={`${count} ${count === 1 ? "outlet" : "outlets"}`}>
      {inner}
    </span>
  );
}

function Reaction({
  reaction,
  ticker,
  hasPrices,
}: {
  reaction: ReturnType<typeof priceReaction>;
  ticker: string | null;
  hasPrices: boolean;
}) {
  if (!reaction) {
    return (
      <span
        className="text-right text-xs text-text-faint lg:block"
        title={!ticker ? "Not about one holding" : !hasPrices ? "No stored prices" : "No close on both sides of the story yet"}
      >
        —
      </span>
    );
  }
  const up = reaction.pct >= 0;
  return (
    <span
      className="flex items-center justify-end gap-2"
      title={`Close ${reaction.from} to close ${reaction.to} (daily closes; not intraday)`}
    >
      <span className="w-16">
        <Sparkline values={reaction.window} tone={up ? "gain" : "loss"} width={64} height={22} className="h-[22px]" />
      </span>
      <span className={cn("num w-12 text-right text-xs", up ? "text-gain" : "text-loss")}>
        {formatPct(reaction.pct, 1, true)}
        <span className="sr-only"> close to close, {up ? "up" : "down"}</span>
      </span>
    </span>
  );
}

/** A corroborating/contradicting source: a link for http(s) URLs, plain text otherwise. */
function SourceLink({ url }: { url: string }) {
  if (!isExternalUrl(url)) return <span className="break-all">{url}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-accent-ink hover:underline">
      {hostOf(url)}
    </a>
  );
}
