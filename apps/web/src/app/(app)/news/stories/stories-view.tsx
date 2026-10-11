"use client";

import { ChevronDownIcon, LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { NewsTabs } from "@/components/news/news-tabs";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { LIFECYCLE_LABELS, type StudyRow, type ThreadDetail, type ThreadOut } from "@/lib/news/analysis";
import { eventLabel } from "@/lib/news/labels";
import { cn } from "@/lib/utils";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; threads: ThreadOut[]; study: StudyRow[] | null };

/** Developing situations: each thread with its stories in order and what each one added. */
export function StoriesView() {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    const api = getBrowserApiClient();
    let cancelled = false;
    Promise.all([
      api.GET("/threads", { params: { query: { limit: 100 } } }),
      // null marks a failed event-study request so it is not shown as "no study".
      api.GET("/news/event-study").catch(() => null),
    ])
      .then(([threads, study]) => {
        if (cancelled) return;
        if (threads.data) setLoad({ kind: "ok", threads: threads.data, study: study?.data ?? null });
        else setLoad({ kind: "error", message: `The API answered HTTP ${threads.response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="News"
        title="Stories"
        description={
          <span className="block max-w-prose">
            Stories on one developing situation, such as a merger, a strike or a tariff dispute, grouped so ten articles do
            not read as ten events. Open a thread to see what each update added.
          </span>
        }
      />
      <NewsTabs active="stories" />
      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading threads…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the threads">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.threads.length === 0 ? (
        <EmptyState title="No threads yet">
          Threads appear once the news analysis job has run on stories about your holdings. It runs every three hours.
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.threads.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-3 p-0" aria-label="Threads">
          {load.threads.map((t) => (
            <ThreadCard key={t.id} thread={t} />
          ))}
        </ul>
      ) : null}
      {load.kind === "ok" && load.study === null ? (
        <AlertMessage title="Could not load the event study">The request failed, so the study is unknown, not empty.</AlertMessage>
      ) : null}
      {load.kind === "ok" && load.study && load.study.length > 0 ? <Study rows={load.study} /> : null}
    </div>
  );
}

function ThreadCard({ thread }: { thread: ThreadOut }) {
  const now = useNow();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [fetching, setFetching] = useState(false);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && detail === null && !fetching) {
      // A retry starts clean: the old error must not outlive the new attempt.
      setError(null);
      setFetching(true);
      try {
        const { data, response } = await getBrowserApiClient().GET("/threads/{thread_id}", { params: { path: { thread_id: thread.id } } });
        if (data) setDetail(data as ThreadDetail);
        else setError(`The API answered HTTP ${response.status}.`);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setFetching(false);
      }
    }
  }

  return (
    <Blueprint as="li" className="space-y-2 px-5 py-3">
      <button type="button" onClick={() => void toggle()} aria-expanded={open} className="flex w-full items-start justify-between gap-3 text-left">
        <span className="min-w-0 space-y-0.5">
          <span className="block text-base font-semibold [overflow-wrap:anywhere]">{thread.title}</span>
          <span className="block text-xs text-text-muted">
            {thread.ticker ? <span className="font-mono">{thread.ticker}</span> : "No single holding"}
            {" · "}
            {thread.stories} stor{thread.stories === 1 ? "y" : "ies"}
            {" · "}
            {thread.state === "open" ? "open" : thread.state === "quiet" ? "quiet" : "closed"}, last update{" "}
            <time dateTime={thread.last_update_at}>{formatRelative(new Date(thread.last_update_at), now)}</time>
          </span>
        </span>
        <ChevronDownIcon aria-hidden="true" className={cn("mt-1 size-4 shrink-0 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        error ? (
          <AlertMessage title="Could not load the thread">{error}</AlertMessage>
        ) : detail === null ? (
          <p className="text-sm text-text-muted" role="status">
            Loading…
          </p>
        ) : (
          <ol className="m-0 list-none space-y-3 border-l border-border p-0 pl-4">
            {detail.timeline.map((s) => (
              <li key={s.story_id} className="space-y-1 text-sm">
                <p>
                  <Link href={`/news/stories/${s.story_id}`} className="font-medium hover:text-accent-ink hover:underline">
                    {s.headline}
                  </Link>{" "}
                  <span className="text-xs text-text-muted">
                    {LIFECYCLE_LABELS[s.lifecycle] ?? s.lifecycle}, <time dateTime={s.at}>{formatDateTime(s.at)}</time>
                  </span>
                </p>
                {s.diff.new.length > 0 ? <Diff label="New" items={s.diff.new} /> : null}
                {s.diff.changed.length > 0 ? <Diff label="Changed" items={s.diff.changed.map((c) => `${c.now} (was: ${c.was})`)} /> : null}
                {s.diff.retracted.length > 0 ? <Diff label="Corrected" items={s.diff.retracted} /> : null}
              </li>
            ))}
          </ol>
        )
      ) : null}
    </Blueprint>
  );
}

function Diff({ label, items }: { label: string; items: string[] }) {
  return (
    <ul className="m-0 list-none space-y-0.5 p-0 text-xs text-text-muted">
      {items.map((t) => (
        <li key={t}>
          <span className="font-medium text-text">{label}:</span> {t}
        </li>
      ))}
    </ul>
  );
}

function Study({ rows }: { rows: StudyRow[] }) {
  return (
    <section aria-labelledby="study-heading" className="space-y-2">
      <h2 id="study-heading" className="text-[22px] leading-tight font-semibold">
        What stories like these were followed by
      </h2>
      <p className="max-w-prose text-sm text-text-muted">
        From your own history: the median move of the holding against its market over three trading days after a story of
        that type, with the middle half of cases and the number of stories. It describes what happened; it does not predict
        what will.
      </p>
      <table aria-label="Typical move by event type, region and outlet tier" className="num w-full max-w-2xl text-sm">
        <thead className="text-left text-xs text-text-muted">
          <tr>
            <th scope="col" className="py-1 font-normal">Event</th>
            <th scope="col" className="font-normal">Region</th>
            <th scope="col" className="font-normal">Best outlet tier</th>
            <th scope="col" className="font-normal">Stories</th>
            <th scope="col" className="font-normal">Median</th>
            <th scope="col" className="font-normal">Middle half</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.event_type}${r.region}${r.tier}`}>
              <td className="py-0.5">{eventLabel(r.event_type as never)}</td>
              <td>{r.region}</td>
              <td>{r.tier}</td>
              <td>{r.n}</td>
              <td>{r.median_pct.toFixed(1)}%</td>
              <td>
                {r.p25_pct.toFixed(1)}% to {r.p75_pct.toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
