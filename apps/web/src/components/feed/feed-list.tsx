"use client";

import { ChevronDownIcon } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { buildDays, countKinds, similarLabel, type FeedEntry } from "@/lib/feed/feed";
import { cn } from "@/lib/utils";

/*
 * T3 feed list: typed filter chips with counts, an optional unread dot, day group headers, and
 * repeated headlines folded into "n similar". The page supplies the row content; this component
 * owns the filtering, grouping and folding so every feed behaves the same way.
 */

export type FeedListProps<T extends FeedEntry> = {
  items: readonly T[];
  /** The row content for one entry. `folded` is true for entries shown under "n similar". */
  renderItem: (item: T, ctx: { folded: boolean }) => ReactNode;
  /** Plain-language label for a type, e.g. "About a holding". */
  kindLabel: (kind: string) => string;
  /** Name of the list, used for the accessible label. */
  label: string;
  /** Shown when the active chip leaves nothing. */
  emptyText?: string;
  /** Overrides "now" for tests and stories. */
  now?: Date;
  className?: string;
};

export function FeedList<T extends FeedEntry>({ items, renderItem, kindLabel, label, emptyText = "Nothing of this type yet.", now, className }: FeedListProps<T>) {
  const [kind, setKind] = useState<string | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const today = useMemo(() => now ?? new Date(), [now]);

  const counts = useMemo(() => countKinds(items), [items]);
  const unread = useMemo(() => items.filter((i) => i.unread).length, [items]);
  const shown = useMemo(() => items.filter((i) => (kind === null || i.kind === kind) && (!unreadOnly || i.unread)), [items, kind, unreadOnly]);
  const days = useMemo(() => buildDays(shown, today), [shown, today]);

  const toggle = (id: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const chip = (active: boolean) =>
    cn(
      "rounded-full border px-3 py-1 text-xs focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
      active ? "border-accent bg-accent-soft font-medium text-accent-ink" : "border-border text-text-muted hover:text-text",
    );

  return (
    <div className={cn("space-y-4", className)}>
      {counts.length > 1 || unread > 0 ? (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Filter ${label}`}>
          <button type="button" aria-pressed={kind === null} onClick={() => setKind(null)} className={chip(kind === null)}>
            All <span className="num">{items.length}</span>
          </button>
          {counts.map((c) => (
            <button key={c.kind} type="button" aria-pressed={kind === c.kind} onClick={() => setKind(kind === c.kind ? null : c.kind)} className={chip(kind === c.kind)}>
              {kindLabel(c.kind)} <span className="num">{c.count}</span>
            </button>
          ))}
          {unread > 0 ? (
            <button type="button" aria-pressed={unreadOnly} onClick={() => setUnreadOnly((u) => !u)} className={cn(chip(unreadOnly), "ml-auto")}>
              Unread only <span className="num">{unread}</span>
            </button>
          ) : null}
        </div>
      ) : null}

      {days.length === 0 ? <p className="text-sm text-text-muted">{emptyText}</p> : null}

      {days.map((day) => (
        <section key={day.key} aria-label={day.label} className="space-y-1">
          <h3 className="font-heading text-sm font-semibold text-text-muted">{day.label}</h3>
          <ul className="m-0 flex list-none flex-col divide-y divide-border border-y border-border p-0" aria-label={`${label}, ${day.label}`}>
            {day.groups.map((g) => {
              const expanded = open.has(g.head.id);
              return (
                <li key={g.head.id} className="relative py-3 pl-4">
                  {g.head.unread ? <span role="img" aria-label="Unread" className="absolute top-[1.1rem] left-0 size-2 rounded-full bg-accent" /> : null}
                  {renderItem(g.head, { folded: false })}
                  {g.similar.length > 0 ? (
                    <div className="mt-1.5">
                      <button
                        type="button"
                        aria-expanded={expanded}
                        onClick={() => toggle(g.head.id)}
                        className="inline-flex items-center gap-1 rounded-sm text-xs text-accent-ink hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
                      >
                        <ChevronDownIcon aria-hidden="true" className={cn("size-3 transition-transform", expanded && "rotate-180")} />
                        {expanded ? "Hide repeats" : similarLabel(g.similar.length)}
                      </button>
                      {expanded ? (
                        <ul className="m-0 mt-2 list-none space-y-3 border-l border-border p-0 pl-3">
                          {g.similar.map((s) => (
                            <li key={s.id} className="relative">
                              {renderItem(s, { folded: true })}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
