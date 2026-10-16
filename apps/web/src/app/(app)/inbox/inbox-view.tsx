"use client";

import {
  CheckIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { EntityName } from "@/components/entity-name";
import { labelText } from "@/lib/labels";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { ageLabel } from "@/lib/news/escalation";
import { useNow } from "@/lib/hooks/use-now";
import {
  actionLabel,
  actionState,
  assessmentOf,
  formatSeconds,
  formatUsd,
  INBOX_TABS,
  kindLabel,
  noteSuggestionOf,
  parseTab,
  sourcesOf,
  statementsOf,
  TAB_LABELS,
  tabStatus,
  touchesOf,
  type AgentNote,
  type InboxItem,
  type MissedEvent,
  type NoteVersion,
  type RouteStat,
} from "@/lib/inbox";
import { cn } from "@/lib/utils";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; items: InboxItem[]; unread: number; missed: number };

/** What the event agents left for you: the evidence, what it touches and, at most, one job they ask you to confirm. */
export function InboxView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const now = useNow();

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/inbox", {
        params: { query: { status: tabStatus(tab), limit: 100 } },
      })
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data)
          setLoad({
            kind: "ok",
            items: data.items,
            unread: data.unread,
            missed: data.missed,
          });
        else
          setLoad({
            kind: "error",
            message: `The API answered HTTP ${response.status}.`,
          });
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setLoad({
            kind: "error",
            message: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      cancelled = true;
    };
  }, [tab, reloadKey]);

  // A link from the updates feed names one item (`?item=`): scroll it into view once loaded.
  const focusItem = params.get("item");
  const loaded = load.kind === "ok";
  useEffect(() => {
    if (!loaded || !focusItem) return;
    document.getElementById(`inbox-item-${focusItem}`)?.scrollIntoView({ block: "center" });
  }, [loaded, focusItem]);

  const onUpdated = useCallback(
    (updated: InboxItem) =>
      setLoad((l) =>
        l.kind !== "ok"
          ? l
          : {
              ...l,
              // An item that no longer belongs to the tab leaves it.
              items:
                tab === "new" && updated.status !== "new"
                  ? l.items.filter((i) => i.id !== updated.id)
                  : tab === "dismissed" && updated.status !== "dismissed"
                    ? l.items.filter((i) => i.id !== updated.id)
                    : l.items.map((i) => (i.id === updated.id ? updated : i)),
            },
      ),
    [tab],
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Inbox"
        description="Events the agents looked into for you. Nothing here has started anything: a proposed job runs only when you confirm it."
        aside={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setReloadKey((k) => k + 1)}
          >
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1.75fr)_minmax(0,1fr)]">
        <Blueprint aria-labelledby="inbox-heading">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <h2
              id="inbox-heading"
              className="text-[22px] leading-tight font-semibold"
            >
              Items
            </h2>
            <nav
              aria-label="Show"
              className="flex flex-wrap border border-border"
            >
              {INBOX_TABS.map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={tab === t}
                  onClick={() =>
                    router.replace(
                      t === "new" ? pathname : `${pathname}?tab=${t}`,
                      { scroll: false },
                    )
                  }
                  className={cn(
                    "border-l border-border px-3 py-1.5 text-xs first:border-l-0",
                    tab === t
                      ? "bg-accent text-accent-foreground"
                      : "text-text hover:bg-surface-muted",
                  )}
                >
                  {TAB_LABELS[t]}
                  {t === "new" && load.kind === "ok" ? (
                    <span className="num"> · {load.unread}</span>
                  ) : null}
                </button>
              ))}
            </nav>
          </div>

          {load.kind === "ok" && load.missed > 0 ? (
            <MissedPanel
              count={load.missed}
              onQueued={() => setReloadKey((k) => k + 1)}
            />
          ) : null}
          {load.kind === "loading" ? (
            <p
              className="flex items-center gap-2 px-5 py-4 text-sm text-text-muted"
              role="status"
            >
              <LoaderCircleIcon
                aria-hidden="true"
                className="size-4 animate-spin"
              />
              Loading the inbox…
            </p>
          ) : null}
          {load.kind === "error" ? (
            <div className="p-5">
              <AlertMessage title="Could not load the inbox">
                {load.message}
              </AlertMessage>
            </div>
          ) : null}
          {load.kind === "ok" && load.items.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title={tab === "new" ? "Nothing to look at" : "No items here"}
              >
                An agent looks into each new escalation note, thesis-breach
                alert, open disclosure alert, portfolio breach and KPI breach
                about once an hour. Turn it off or change how many it handles in
                Config: Jobs.
              </EmptyState>
            </div>
          ) : null}
          {load.kind === "ok" && load.items.length > 0 ? (
            <ul className="m-0 list-none p-0">
              {load.items.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  now={now}
                  onUpdated={onUpdated}
                  focused={String(item.id) === focusItem}
                />
              ))}
            </ul>
          ) : null}
        </Blueprint>

        <div className="flex flex-col gap-5">
          <NotesPanel />
          <RoutesPanel reloadKey={reloadKey} />
        </div>
      </div>
    </div>
  );
}

function ItemRow({
  item,
  now,
  onUpdated,
  focused = false,
}: {
  item: InboxItem;
  now: Date;
  focused?: boolean;
  onUpdated: (u: InboxItem) => void;
}) {
  const state = actionState(item);
  const statements = statementsOf(item);
  const sources = sourcesOf(item);
  const touches = touchesOf(item);
  const assessment = assessmentOf(item);
  const proposal = actionLabel(item.proposed_action);
  const reason =
    typeof item.proposed_action?.reason === "string"
      ? labelText(item.proposed_action.reason)
      : null;
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function call(
    name: string,
    run: () => Promise<{ data?: InboxItem; response: Response }>,
  ) {
    setPending(name);
    setError(null);
    try {
      const { data, response } = await run();
      if (data) onUpdated(data);
      else setError(`HTTP ${response.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(null);
    }
  }

  const api = getBrowserApiClient();
  const path = { item_id: item.id };
  const busy = pending !== null;

  return (
    <li
      id={`inbox-item-${item.id}`}
      className={cn("scroll-mt-24 border-b border-border-soft last:border-0", focused && "bg-accent-soft")}
    >
      <div className="grid grid-cols-[4px_minmax(0,1fr)] gap-x-4 px-5 py-4">
        <span
          aria-hidden="true"
          className={cn(
            "w-1",
            item.severity === 3
              ? "bg-loss"
              : item.severity === 2
                ? "bg-accent"
                : "bg-border",
          )}
        />
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
            <p className="text-[15px] font-medium">
              {item.ticker ? (
                <EntityName id={item.ticker} className="mr-2 text-xs font-semibold" />
              ) : (
                <span className="mr-2 text-xs font-semibold">—</span>
              )}
              {labelText(item.headline)}
            </p>
            <span className="text-xs text-text-muted">
              {kindLabel(item.trigger_kind)}
            </span>
            <time
              dateTime={item.created_at}
              className="num text-xs text-text-muted"
              title={new Date(item.created_at).toLocaleString()}
            >
              {ageLabel(item.created_at, now)}
            </time>
          </div>
          {assessment ? (
            <p className="mt-1.5 max-w-prose text-sm text-text">{assessment}</p>
          ) : null}
          {touches.length > 0 ? (
            <p className="mt-1 text-xs text-text-muted">
              Touches {touches.join(", ")}
            </p>
          ) : null}

          {statements.length > 0 ? (
            <ul className="mt-2 flex max-w-prose list-none flex-col gap-1 p-0 text-sm">
              {statements.map((s, n) => (
                <li key={n}>
                  {s.text}{" "}
                  {s.source_ids.map((id) => {
                    const src = sources.get(id);
                    const href = src?.url ?? src?.app_path ?? null;
                    return href ? (
                      <a
                        key={id}
                        href={href}
                        title={src?.title}
                        className="num text-xs text-accent-ink hover:underline"
                        {...(src?.url
                          ? { target: "_blank", rel: "noreferrer" }
                          : {})}
                      >
                        [{id}]
                      </a>
                    ) : (
                      <span
                        key={id}
                        title={src?.title}
                        className="num text-xs text-text-muted"
                      >
                        [{id}]
                      </span>
                    );
                  })}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-xs text-text-muted">
              No cited statements; the event text is the only record.
            </p>
          )}

          {state !== "none" && proposal ? (
            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border border-accent bg-accent-soft px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-accent-ink">
                  {state === "open"
                    ? "Proposed: "
                    : state === "confirmed"
                      ? "Started: "
                      : "Declined: "}
                  {proposal}
                </p>
                {reason ? (
                  <p className="text-xs text-text-muted">{reason}</p>
                ) : null}
                {state === "confirmed" && item.action_job_id ? (
                  <p className="text-xs text-text-muted">
                    Job{" "}
                    <Link
                      href={`/jobs/${item.action_job_id}`}
                      className="num text-accent-ink hover:underline"
                    >
                      #{item.action_job_id}
                    </Link>
                  </p>
                ) : null}
              </div>
              {state === "open" ? (
                <span className="inline-flex items-center gap-1.5">
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      void call("confirm", () =>
                        api.POST("/inbox/{item_id}/confirm", {
                          params: { path },
                        }),
                      )
                    }
                  >
                    <CheckIcon aria-hidden="true" />
                    Confirm
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void call("decline", () =>
                        api.POST("/inbox/{item_id}/decline", {
                          params: { path },
                        }),
                      )
                    }
                  >
                    Decline
                  </Button>
                </span>
              ) : null}
            </div>
          ) : null}

          <NoteSuggestionBlock item={item} onUpdated={onUpdated} />

          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            <Button
              size="sm"
              variant={item.feedback === "useful" ? "outline" : "ghost"}
              className="h-7 px-2 text-[13px]"
              aria-pressed={item.feedback === "useful"}
              disabled={busy}
              onClick={() =>
                void call("useful", () =>
                  api.POST("/inbox/{item_id}/feedback", {
                    params: { path },
                    body: { feedback: "useful" },
                  }),
                )
              }
            >
              <ThumbsUpIcon aria-hidden="true" />
              Useful
            </Button>
            <Button
              size="sm"
              variant={item.feedback === "not_useful" ? "outline" : "ghost"}
              className="h-7 px-2 text-[13px]"
              aria-pressed={item.feedback === "not_useful"}
              disabled={busy}
              onClick={() =>
                void call("not_useful", () =>
                  api.POST("/inbox/{item_id}/feedback", {
                    params: { path },
                    body: { feedback: "not_useful" },
                  }),
                )
              }
            >
              <ThumbsDownIcon aria-hidden="true" />
              Not useful
            </Button>
            {item.status === "new" ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[13px]"
                disabled={busy}
                onClick={() =>
                  void call("read", () =>
                    api.PATCH("/inbox/{item_id}", {
                      params: { path },
                      body: { status: "read" },
                    }),
                  )
                }
              >
                Mark read
              </Button>
            ) : null}
            {item.status !== "dismissed" && item.status !== "actioned" ? (
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[13px]"
                disabled={busy}
                onClick={() =>
                  void call("dismiss", () =>
                    api.PATCH("/inbox/{item_id}", {
                      params: { path },
                      body: { status: "dismissed" },
                    }),
                  )
                }
              >
                <XIcon aria-hidden="true" />
                Dismiss
              </Button>
            ) : null}
            {error ? <span className="text-xs text-loss">{error}</span> : null}
          </div>
        </div>
      </div>
    </li>
  );
}

/** Events older than the scan window that no agent has looked into; pick the ones to run now. */
function MissedPanel({
  count,
  onQueued,
}: {
  count: number;
  onQueued: () => void;
}) {
  const [events, setEvents] = useState<MissedEvent[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/inbox/missed", { params: { query: { limit: 50 } } })
      .then(({ data }) => {
        if (!cancelled) setEvents(data ?? []);
      })
      .catch(() => {
        if (!cancelled) setEvents([]);
      });
    return () => {
      cancelled = true;
    };
  }, [count]);

  function toggle(key: string) {
    setPicked((p) => {
      const next = new Set(p);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function run() {
    setBusy(true);
    setMessage(null);
    try {
      const { data, response } = await getBrowserApiClient().POST(
        "/inbox/missed/run",
        {
          body: { keys: [...picked] },
        },
      );
      if (data) {
        setMessage(
          data.queued.length > 0
            ? `${data.queued.length} queued; the items appear here when the runs finish.`
            : "Nothing was queued: those events already have a run.",
        );
        setPicked(new Set());
        onQueued();
      } else setMessage(`HTTP ${response.status}`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="missed-heading"
      className="border-b border-border px-5 py-4"
    >
      <h3 id="missed-heading" className="text-[15px] font-semibold">
        {count} older {count === 1 ? "event was" : "events were"} never looked
        into
      </h3>
      <p className="mt-1 text-sm text-text-muted">
        They are older than the scan window. Choose the ones an agent should
        look into now.
      </p>
      {events === null ? (
        <p className="mt-2 text-sm text-text-muted" role="status">
          Loading…
        </p>
      ) : (
        <ul className="m-0 mt-2 flex max-h-56 list-none flex-col overflow-y-auto p-0">
          {events.map((e) => (
            <li
              key={e.key}
              className="border-t border-border-soft first:border-t-0"
            >
              <label className="flex cursor-pointer items-start gap-2 py-1.5 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={picked.has(e.key)}
                  onChange={() => toggle(e.key)}
                />
                <span className="min-w-0 flex-1">
                  <span className="mr-2 font-mono text-xs font-semibold">
                    {e.ticker ?? "—"}
                  </span>
                  {e.headline}
                  <span className="ml-2 text-xs text-text-muted">
                    {kindLabel(e.kind)},{" "}
                    {new Date(e.created_at).toLocaleDateString()}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex items-center gap-3">
        <Button
          size="sm"
          disabled={busy || picked.size === 0}
          onClick={() => void run()}
        >
          Look into {picked.size > 0 ? picked.size : "selected"}
        </Button>
        {message ? (
          <span className="text-xs text-text-muted" role="status">
            {message}
          </span>
        ) : null}
      </div>
    </section>
  );
}

/** The agent's suggested working note on an item: keep it as a note, or drop it. */
function NoteSuggestionBlock({
  item,
  onUpdated,
}: {
  item: InboxItem;
  onUpdated: (u: InboxItem) => void;
}) {
  const suggestion = noteSuggestionOf(item);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!suggestion) return null;

  async function decide(accept: boolean) {
    setBusy(true);
    setError(null);
    try {
      const path = { item_id: item.id };
      const { data, response } = accept
        ? await getBrowserApiClient().POST("/inbox/{item_id}/note/accept", {
            params: { path },
          })
        : await getBrowserApiClient().POST("/inbox/{item_id}/note/decline", {
            params: { path },
          });
      if (data) onUpdated(data.item);
      else setError(`HTTP ${response.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 border border-border px-3 py-2.5">
      <p className="text-xs text-text-muted">
        {suggestion.status === "suggested"
          ? "The agent suggests this working note:"
          : suggestion.status === "accepted"
            ? "Kept as a note:"
            : "Suggested note, not kept:"}
      </p>
      <p className="mt-0.5 max-w-prose text-sm">{suggestion.text}</p>
      {suggestion.status === "suggested" ? (
        <div className="mt-2 flex items-center gap-1.5">
          <Button size="sm" disabled={busy} onClick={() => void decide(true)}>
            Keep as note
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => void decide(false)}
          >
            Not now
          </Button>
          {error ? <span className="text-xs text-loss">{error}</span> : null}
        </div>
      ) : null}
    </div>
  );
}

/** What a note said before each change, newest first (kept after the note is deleted). */
function NoteHistory({ noteId }: { noteId: number }) {
  const [rows, setRows] = useState<NoteVersion[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/agent-notes/{note_id}/history", {
        params: { path: { note_id: noteId } },
      })
      .then(({ data }) => {
        if (!cancelled) setRows(data ?? []);
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [noteId]);
  if (rows === null)
    return <p className="mt-1 text-xs text-text-muted">Loading…</p>;
  return (
    <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0 text-xs text-text-muted">
      {rows.map((v) => (
        <li key={v.id}>
          <time dateTime={v.changed_at} className="num">
            {new Date(v.changed_at).toLocaleString()}
          </time>{" "}
          {v.change}
          {v.active ? "" : " (off)"}: {v.text}
        </li>
      ))}
    </ul>
  );
}

/** The notes the agents read before judging an event. You write them; the agents never do. */
function NotesPanel() {
  const [notes, setNotes] = useState<AgentNote[] | null>(null);
  const [text, setText] = useState("");
  const [ticker, setTicker] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<number | null>(null);

  const refresh = useCallback(() => {
    getBrowserApiClient()
      .GET("/agent-notes")
      .then(({ data }) => setNotes(data ?? []))
      .catch(() => setNotes([]));
  }, []);
  useEffect(refresh, [refresh]);

  async function add() {
    setError(null);
    try {
      const { response } = await getBrowserApiClient().POST("/agent-notes", {
        body: {
          text: text.trim(),
          ticker: ticker.trim() || null,
          active: true,
        },
      });
      if (response.ok) {
        setText("");
        setTicker("");
        refresh();
      } else setError(`HTTP ${response.status}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function toggle(note: AgentNote) {
    await getBrowserApiClient().PATCH("/agent-notes/{note_id}", {
      params: { path: { note_id: note.id } },
      body: { active: !note.active },
    });
    refresh();
  }

  async function remove(note: AgentNote) {
    await getBrowserApiClient().DELETE("/agent-notes/{note_id}", {
      params: { path: { note_id: note.id } },
    });
    refresh();
  }

  return (
    <Blueprint
      aria-labelledby="notes-heading"
      className="flex flex-col gap-3 px-5 py-5"
    >
      <PanelHeading
        kicker="Your instructions"
        title={<span id="notes-heading">Notes for the agents</span>}
      />
      <p className="text-sm text-text-muted">
        Context the agents read before judging an event, for one holding or for
        all. A note switched off is kept but not read.
      </p>
      {notes === null ? (
        <p className="text-sm text-text-muted" role="status">
          Loading notes…
        </p>
      ) : notes.length === 0 ? (
        <p className="text-sm text-text-muted">No notes yet.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col p-0">
          {notes.map((n) => (
            <li
              key={n.id}
              className="flex items-start gap-2 border-t border-border-soft py-2 first:border-t-0"
            >
              <div
                className={cn(
                  "min-w-0 flex-1 text-sm",
                  !n.active && "text-text-muted line-through",
                )}
              >
                <span className="mr-2 font-mono text-xs font-semibold">
                  {n.ticker ?? "All holdings"}
                </span>
                {n.text}
                {history === n.id ? <NoteHistory noteId={n.id} /> : null}
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[13px]"
                aria-expanded={history === n.id}
                onClick={() => setHistory(history === n.id ? null : n.id)}
              >
                History
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[13px]"
                onClick={() => void toggle(n)}
              >
                {n.active ? "Switch off" : "Switch on"}
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="size-7"
                aria-label={`Delete note ${n.id}`}
                onClick={() => void remove(n)}
              >
                <Trash2Icon aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-2 border-t border-border pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void add();
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="kicker">Holding (optional)</span>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="TSM_US_EQ"
            className="h-8 border border-border bg-surface-raised px-2 text-sm text-text"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="kicker">Note</span>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={2000}
            rows={3}
            className="border border-border bg-surface-raised px-2 py-1.5 text-sm text-text"
          />
        </label>
        <div className="flex items-center gap-3">
          <Button type="submit" size="sm" disabled={!text.trim()}>
            Add note
          </Button>
          {error ? <span className="text-xs text-loss">{error}</span> : null}
        </div>
      </form>
    </Blueprint>
  );
}

/** What each kind of work cost and how long it took over the last 30 days. */
function RoutesPanel({ reloadKey }: { reloadKey: number }) {
  const [rows, setRows] = useState<RouteStat[] | null>(null);
  useEffect(() => {
    getBrowserApiClient()
      .GET("/ops/routes", { params: { query: { days: 30 } } })
      .then(({ data }) => setRows(data ?? []))
      .catch(() => setRows([]));
  }, [reloadKey]);

  return (
    <Blueprint
      aria-labelledby="routes-heading"
      className="flex flex-col gap-3 px-5 py-5"
    >
      <PanelHeading
        kicker="Last 30 days"
        title={<span id="routes-heading">Cost and speed by route</span>}
      />
      {rows === null ? (
        <p className="text-sm text-text-muted" role="status">
          Loading…
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-text-muted">No model runs in this window.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <caption className="sr-only">Runs, cost and time per route</caption>
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="py-1.5 pr-2 font-medium">
                  Route
                </th>
                <th
                  scope="col"
                  className="num py-1.5 pr-2 text-right font-medium"
                >
                  Runs
                </th>
                <th
                  scope="col"
                  className="num py-1.5 pr-2 text-right font-medium"
                >
                  Cost (typical / 95th)
                </th>
                <th scope="col" className="num py-1.5 text-right font-medium">
                  Time (typical / 95th)
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.route}
                  className="border-b border-border-soft last:border-0"
                >
                  <th scope="row" className="py-1.5 pr-2 font-mono font-normal">
                    {r.route}
                    {r.failed > 0 ? (
                      <span className="ml-1 text-loss">
                        ({r.failed} failed)
                      </span>
                    ) : null}
                    {r.unpriced_runs > 0 ? (
                      <span
                        className="ml-1 text-text-muted"
                        title="Some steps had no price, so the cost is a floor."
                      >
                        ({r.unpriced_runs} unpriced)
                      </span>
                    ) : null}
                  </th>
                  <td className="num py-1.5 pr-2 text-right">{r.runs}</td>
                  <td className="num py-1.5 pr-2 text-right">
                    {formatUsd(r.cost_median_usd)} / {formatUsd(r.cost_p95_usd)}
                  </td>
                  <td className="num py-1.5 text-right">
                    {formatSeconds(r.seconds_median)} /{" "}
                    {formatSeconds(r.seconds_p95)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Blueprint>
  );
}
