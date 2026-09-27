"use client";

import {
  CircleCheckIcon,
  CircleXIcon,
  ExternalLinkIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  ScaleIcon,
  SnowflakeIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { SeverityDot } from "@/components/news/severity-timeline";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type ClaimSource,
  type DisputedStory,
  type StoryResolution,
} from "@/lib/api/client";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import {
  applyResolution,
  disputedStoryAnchor,
  NOTE_MAX,
  noteError,
  RESOLUTION_EFFECTS,
  resolutionBody,
  resolutionState,
  sortSources,
  sourceOutlet,
  splitDisputed,
} from "@/lib/news/disputed";
import { eventLabel } from "@/lib/news/labels";
import { cn } from "@/lib/utils";

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; stories: DisputedStory[] };

const CONTROL =
  "w-full rounded-md border border-border bg-surface-raised px-3 py-1.5 text-base text-text shadow-xs outline-none placeholder:text-text-muted md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal";

export function DisputedView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const includeResolved = params.get("resolved") === "1";
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/stories/disputed", { params: { query: { include_resolved: includeResolved } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setLoad({ kind: "ok", stories: data });
        else setLoad({ kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [includeResolved, reloadKey]);

  const { open, resolved } = load.kind === "ok" ? splitDisputed(load.stories) : { open: [], resolved: [] };
  const onResolved = (updated: DisputedStory[]) =>
    setLoad((l) => (l.kind === "ok" ? { ...l, stories: applyResolution(l.stories, updated) } : l));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/news" className="hover:text-text hover:underline">
              News
            </Link>{" "}
            / Disputed
          </p>
          <h1 className="page-title">Disputed stories</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Stories whose sources contradict each other: the claim and its sources beside the verified sources that
            dispute it, each with its tier. A disputed story never alerts, and the holding&rsquo;s thesis verdicts do not
            change until you resolve it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-accent"
              checked={includeResolved}
              onChange={(e) => router.replace(`${pathname}${e.target.checked ? "?resolved=1" : ""}`, { scroll: false })}
            />
            Show resolved
          </label>
          <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading disputed stories…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the disputed stories">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && open.length === 0 ? (
        <EmptyState title="No open disputes">
          When a verified source contradicts a story, it appears here with both versions side by side.
        </EmptyState>
      ) : null}

      {open.length > 0 ? (
        <ol className="space-y-4" aria-label="Open disputes">
          {open.map((s) => (
            <StoryCard key={s.id} story={s} onResolved={onResolved} />
          ))}
        </ol>
      ) : null}

      {includeResolved && resolved.length > 0 ? (
        <section aria-labelledby="resolved-heading" className="space-y-3">
          <h2 id="resolved-heading" className="text-[22px] leading-tight font-semibold">
            Resolved <span className="num text-sm font-normal text-text-muted">({resolved.length})</span>
          </h2>
          <ol className="space-y-4">
            {resolved.map((s) => (
              <StoryCard key={s.id} story={s} onResolved={onResolved} />
            ))}
          </ol>
        </section>
      ) : null}
      {includeResolved && load.kind === "ok" && resolved.length === 0 ? (
        <p className="text-sm text-text-muted">No resolved stories yet.</p>
      ) : null}
    </div>
  );
}

function StoryCard({ story, onResolved }: { story: DisputedStory; onResolved: (updated: DisputedStory[]) => void }) {
  const now = useNow();
  const state = resolutionState(story);
  const headingId = useId();
  return (
    <li
      id={disputedStoryAnchor(story.id)}
      aria-labelledby={headingId}
      className={cn(
        "scroll-mt-24 space-y-4 rounded-lg border bg-surface-raised p-4",
        state.kind === "open" ? "border-disputed/40" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <h3 id={headingId} className="flex items-start gap-2 font-medium">
            <SeverityDot severity={story.severity} className="mt-1.5" />
            {story.headline}
          </h3>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
            {story.ticker ? (
              <Link href={`/holdings/${encodeURIComponent(story.ticker)}`} className="font-mono text-xs text-accent hover:underline">
                {story.ticker}
              </Link>
            ) : (
              <span>Markets and macro</span>
            )}
            <span aria-hidden="true">·</span>
            <span>{eventLabel(story.event_type)}</span>
            <span aria-hidden="true">·</span>
            <span className="num">
              first seen <time dateTime={story.first_seen}>{formatDateTime(story.first_seen)}</time>, last{" "}
              <time dateTime={story.last_seen}>{formatRelative(new Date(story.last_seen), now)}</time>
            </span>
            <span aria-hidden="true">·</span>
            <Link href={`/news?story=${story.id}`} className="text-accent hover:underline">
              all copies in the feed
            </Link>
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          <SeverityBadge severity={story.severity} />
          <ResolutionBadge story={story} />
          <FeedbackControl target={{ kind: "story", id: story.id }} label={`story “${story.headline}”`} />
        </div>
      </div>

      {story.verdicts_frozen ? (
        <p className="flex items-center gap-1.5 text-sm text-text">
          <SnowflakeIcon aria-hidden="true" className="size-4 text-info-signal" />
          The thesis verdicts of {story.ticker} are frozen until you resolve this story.
        </p>
      ) : null}
      {story.note || story.status_reason ? (
        <p className="max-w-prose text-sm text-text-muted">
          {story.note ?? story.status_reason}
        </p>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <SourceColumn title="The claim" description="The story's own sources." sources={story.claim} empty="No source is stored for the claim." />
        <SourceColumn
          title="Contradicted by"
          description="Verified sources that dispute it."
          sources={story.contradicting}
          empty="No contradicting source is stored."
          disputed
        />
      </div>
      {story.supporting.length > 0 ? (
        <SourceColumn title="Supported by" description="Verified sources found to support it." sources={story.supporting} empty="" />
      ) : null}

      {state.kind === "open" ? (
        <ResolveForm story={story} onResolved={onResolved} />
      ) : (
        <div className="rounded-md border border-border bg-surface-muted p-3 text-sm">
          <p className="font-medium">
            {state.label} <span className="num font-normal text-text-muted">on {formatDateTime(state.at)}</span>
          </p>
          {state.note ? <p className="mt-1 text-text-muted">&ldquo;{state.note}&rdquo;</p> : null}
        </div>
      )}
    </li>
  );
}

function ResolutionBadge({ story }: { story: DisputedStory }) {
  const state = resolutionState(story);
  const Icon = state.kind === "open" ? ScaleIcon : state.kind === "confirmed" ? CircleCheckIcon : CircleXIcon;
  const text = state.kind === "open" ? "Disputed" : state.kind === "confirmed" ? "Confirmed by you" : "Marked false";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text",
        state.kind === "open" ? "border-disputed/40 bg-disputed-subtle" : "border-border bg-neutral-signal-subtle",
      )}
    >
      <Icon aria-hidden="true" className={cn("size-3.5", state.kind === "open" ? "text-disputed" : "text-neutral-signal")} />
      {text}
    </span>
  );
}

function SourceColumn({
  title,
  description,
  sources,
  empty,
  disputed = false,
}: {
  title: string;
  description: string;
  sources: ClaimSource[];
  empty: string;
  disputed?: boolean;
}) {
  const sorted = sortSources(sources);
  return (
    <section
      aria-label={title}
      className={cn("space-y-2 rounded-md border p-3", disputed ? "border-disputed/40" : "border-border")}
    >
      <div>
        <h4 className="text-sm font-medium">
          {title} <span className="num font-normal text-text-muted">({sources.length})</span>
        </h4>
        <p className="text-xs text-text-muted">{description}</p>
      </div>
      {sorted.length === 0 ? <p className="text-sm text-text-muted">{empty}</p> : null}
      <ul className="space-y-2">
        {sorted.map((s) => (
          <li key={s.url} className="space-y-0.5 text-sm">
            <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
              <TierBadge tier={s.tier} />
              <span>{sourceOutlet(s)}</span>
              {s.published_at ? (
                <time dateTime={s.published_at} className="num">
                  {formatDateTime(s.published_at)}
                </time>
              ) : null}
            </p>
            <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent hover:underline">
              <span className="break-words">{s.headline ?? s.url}</span>
              <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
            </a>
            {typeof s.news_item_id === "number" ? (
              <FeedbackControl
                className="flex"
                target={{ kind: "news_item", id: s.news_item_id }}
                label={`news item “${s.headline ?? sourceOutlet(s)}”`}
              />
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ResolveForm({ story, onResolved }: { story: DisputedStory; onResolved: (updated: DisputedStory[]) => void }) {
  const noteId = useId();
  const [note, setNote] = useState("");
  const [pending, setPending] = useState<StoryResolution["resolution"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const invalid = noteError(note);

  async function resolve(resolution: StoryResolution["resolution"]) {
    if (invalid) return;
    setPending(resolution);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/stories/{story_id}/resolve", {
        params: { path: { story_id: story.id } },
        body: resolutionBody(resolution, note),
      });
      if (data) onResolved(data);
      else setError(response.status === 404 ? "This story is no longer disputed. Reload the page." : `The API answered HTTP ${response.status}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-border bg-surface-muted p-3">
      <p className="text-sm font-medium">Resolve</p>
      <div className="space-y-1">
        <Label htmlFor={noteId}>Note (optional)</Label>
        <textarea
          id={noteId}
          rows={2}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={NOTE_MAX + 50}
          placeholder="What settled it, e.g. the company's statement"
          aria-invalid={invalid ? true : undefined}
          className={CONTROL}
        />
        {invalid ? <p className="text-xs text-text">{invalid}</p> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void resolve("confirmed")} disabled={pending !== null || Boolean(invalid)} title={RESOLUTION_EFFECTS.confirmed}>
          {pending === "confirmed" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CircleCheckIcon aria-hidden="true" />}
          Confirm: it happened
        </Button>
        <Button size="sm" variant="outline" onClick={() => void resolve("false")} disabled={pending !== null || Boolean(invalid)} title={RESOLUTION_EFFECTS.false}>
          {pending === "false" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CircleXIcon aria-hidden="true" />}
          Mark false
        </Button>
      </div>
      <ul className="space-y-0.5 text-xs text-text-muted">
        <li>Confirm: {RESOLUTION_EFFECTS.confirmed}</li>
        <li>Mark false: {RESOLUTION_EFFECTS.false}</li>
      </ul>
      {error ? <AlertMessage title="Could not resolve">{error}</AlertMessage> : null}
    </div>
  );
}
