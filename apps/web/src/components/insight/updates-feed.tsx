"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/*
 * F-Feed (insight I13.1): one card for every update — an inbox item, a news story, a what-changed
 * line or a dividend event — with the holding, its last close and one-day move, what changed and
 * how old it is. The same card sits on the dashboard, News and the Inbox; chips filter by type
 * and carry the unread counts.
 */

type Feed = components["schemas"]["UpdatesFeed"];
type Update = Feed["items"][number];
type UpdateType = Update["type"];

export const TYPE_LABEL: Record<UpdateType, string> = {
  news: "News",
  earnings: "Earnings",
  analyst: "Analysts",
  valuation: "Valuation",
  dividend: "Dividends",
  insider: "Insiders",
  risk: "Risk",
  other: "Other",
};

function age(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  return `${Math.round(days / 7)} weeks ago`;
}

export function FeedCard({ u, onOpen }: { u: Update; onOpen?: (u: Update) => void }) {
  const move = u.change_1d;
  return (
    <li className={cn("border-b border-border-soft last:border-0", !u.read && "bg-surface-muted/40")}>
      <Link href={u.href} onClick={() => onOpen?.(u)} className="grid grid-cols-[4px_minmax(0,1fr)_auto] gap-x-3 px-4 py-3 hover:bg-border-soft/40">
        <span aria-hidden="true" className={cn("w-1 self-stretch", u.read ? "bg-transparent" : "bg-accent")} />
        <span className="min-w-0">
          <span className="flex flex-wrap items-baseline gap-x-2 text-xs text-text-muted">
            <span className="font-medium text-text">{TYPE_LABEL[u.type]}</span>
            {u.ticker ? <span>{u.name ?? u.ticker.split("_")[0]}</span> : null}
            <span>{age(u.age_days)}</span>
            {!u.read ? <span className="sr-only">unread</span> : null}
          </span>
          <span className="mt-0.5 block text-sm leading-snug font-medium">{u.headline}</span>
          {u.what_changed ? <span className="mt-0.5 block text-xs leading-snug text-text-muted">{u.what_changed}</span> : null}
        </span>
        {u.price !== null && u.price !== undefined ? (
          <span className="num text-right text-xs">
            <span className="block">{u.price_currency ? formatMoney(u.price, u.price_currency, { decimals: 2 }) : u.price.toFixed(2)}</span>
            {move !== null && move !== undefined ? (
              <span className={cn("block", move > 0 ? "text-gain" : move < 0 ? "text-loss" : "text-text-muted")}>
                {formatPercent(move, { signed: true, decimals: 1 })}
                <span className="sr-only"> in a day</span>
              </span>
            ) : null}
          </span>
        ) : (
          <span />
        )}
      </Link>
    </li>
  );
}

type Load = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; feed: Feed };

/** The feed with type chips. `types` narrows it for a page (News shows news and earnings). */
export function UpdatesFeedPanel({
  title = "Updates",
  kicker,
  ticker,
  days = 14,
  limit = 30,
  types,
  className,
}: {
  title?: string;
  kicker?: string;
  ticker?: string;
  days?: number;
  limit?: number;
  types?: readonly UpdateType[];
  className?: string;
}) {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [type, setType] = useState<UpdateType | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/updates", { params: { query: { days, limit: 300, ...(ticker ? { ticker } : {}) } } })
      .then(({ data, response }) => {
        if (cancelled) return;
        setLoad(data ? { kind: "ok", feed: data } : { kind: "error", message: `The API answered HTTP ${response.status}.` });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [days, ticker]);

  const markRead = useCallback((keys: string[]) => {
    if (keys.length === 0) return;
    setLoad((l) =>
      l.kind !== "ok"
        ? l
        : {
            kind: "ok",
            feed: {
              ...l.feed,
              items: l.feed.items.map((i) => (keys.includes(i.key) ? { ...i, read: true } : i)),
              counts: l.feed.counts.map((c) => ({
                ...c,
                unread: Math.max(0, c.unread - l.feed.items.filter((i) => i.type === c.type && !i.read && keys.includes(i.key)).length),
              })),
              unread: Math.max(0, l.feed.unread - l.feed.items.filter((i) => !i.read && keys.includes(i.key)).length),
            },
          },
    );
    void getBrowserApiClient().POST("/updates/read", { body: { keys: keys.slice(0, 500) } });
  }, []);

  const shown = useMemo(() => {
    if (load.kind !== "ok") return [];
    return load.feed.items
      .filter((i) => !types || types.includes(i.type))
      .filter((i) => !type || i.type === type)
      .filter((i) => !unreadOnly || !i.read)
      .slice(0, limit);
  }, [load, types, type, unreadOnly, limit]);

  const counts = load.kind === "ok" ? load.feed.counts.filter((c) => c.total > 0 && (!types || types.includes(c.type))) : [];
  const unread = counts.reduce((n, c) => n + c.unread, 0);

  return (
    <Blueprint className={cn("py-4", className)}>
      <div className="px-4">
        <PanelHeading
          kicker={kicker ?? `Last ${days} days${unread ? `, ${unread} unread` : ""}`}
          title={title}
          action={
            unread > 0 ? (
              <button
                type="button"
                className="text-xs font-medium text-accent-ink hover:underline"
                onClick={() => markRead(shown.filter((i) => !i.read).map((i) => i.key))}
              >
                Mark shown as read
              </button>
            ) : undefined
          }
        />
        {counts.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter updates by type">
            <button
              type="button"
              aria-pressed={type === null}
              onClick={() => setType(null)}
              className={cn("border px-2.5 py-1 text-xs", type === null ? "border-accent bg-accent text-accent-foreground" : "border-border hover:bg-border-soft")}
            >
              All
            </button>
            {counts.map((c) => (
              <button
                key={c.type}
                type="button"
                aria-pressed={type === c.type}
                onClick={() => setType(type === c.type ? null : c.type)}
                className={cn("border px-2.5 py-1 text-xs", type === c.type ? "border-accent bg-accent text-accent-foreground" : "border-border hover:bg-border-soft")}
              >
                {TYPE_LABEL[c.type]} <span className="num">{c.total}</span>
                {c.unread > 0 ? <span className="num font-semibold"> · {c.unread} new</span> : null}
              </button>
            ))}
            <label className="ml-auto flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
              Unread only
            </label>
          </div>
        ) : null}
      </div>
      <div className="mt-3">
        {load.kind === "loading" ? (
          <p role="status" className="px-4 text-sm text-text-muted">Loading updates…</p>
        ) : load.kind === "error" ? (
          <div className="px-4">
            <AlertMessage title="Could not load the updates">{load.message}</AlertMessage>
          </div>
        ) : shown.length === 0 ? (
          <p className="px-4 text-sm text-text-muted">
            {unreadOnly ? "Nothing unread. Untick “Unread only” to see everything." : `Nothing in the last ${days} days.`}
          </p>
        ) : (
          <ul className="m-0 list-none border-t border-border-soft p-0">
            {shown.map((u) => (
              <FeedCard key={u.key} u={u} onOpen={(x) => (x.read ? undefined : markRead([x.key]))} />
            ))}
          </ul>
        )}
        {load.kind === "ok" && load.feed.notes.length > 0 ? (
          <p className="mt-2 px-4 text-xs text-text-muted">{load.feed.notes.join(" ")}</p>
        ) : null}
      </div>
    </Blueprint>
  );
}
