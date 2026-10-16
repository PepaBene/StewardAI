"use client";

import { Fragment, useMemo, useState, type ReactNode } from "react";

import { groupByDomain } from "@/lib/research/sources-rail";
import { cn } from "@/lib/utils";

/**
 * Sources grouped by website with a count each. The list is capped at about 480px with a
 * "Show all" button, so a long source list never pushes the rest of the page down.
 */
export function SourcesByDomain<T>({
  items,
  urlOf,
  renderItem,
  label = "Sources",
  itemIsListItem = false,
}: {
  items: readonly T[];
  urlOf: (item: T) => string;
  /** `index` is the item's original position, so citation numbers stay right. */
  renderItem: (item: T, index: number) => ReactNode;
  label?: string;
  /** `renderItem` already returns an `<li>` (e.g. SourceCard), so it is not wrapped again. */
  itemIsListItem?: boolean;
}) {
  const [all, setAll] = useState(false);
  const groups = useMemo(() => groupByDomain(items, urlOf), [items, urlOf]);
  return (
    <div className="space-y-2">
      <div
        className={cn("space-y-4", !all && "max-h-[480px] overflow-y-auto pr-1")}
        tabIndex={all ? undefined : 0}
        role={all ? undefined : "region"}
        aria-label={all ? undefined : `${label}, scrollable`}
      >
        {groups.map((g) => (
          <section key={g.domain} aria-label={`${g.domain}, ${g.items.length} ${g.items.length === 1 ? "source" : "sources"}`} className="space-y-2">
            <h3 className="flex items-baseline justify-between gap-2 text-sm font-semibold">
              <span className="min-w-0 truncate">{g.domain}</span>
              <span className="num text-xs font-normal text-text-muted">{g.items.length}</span>
            </h3>
            <ol className="m-0 list-none space-y-2 p-0">
              {g.items.map(({ item, index }) =>
                itemIsListItem ? <Fragment key={index}>{renderItem(item, index)}</Fragment> : <li key={index}>{renderItem(item, index)}</li>,
              )}
            </ol>
          </section>
        ))}
      </div>
      {items.length > 6 ? (
        <button type="button" onClick={() => setAll((a) => !a)} aria-expanded={all} className="text-sm text-accent-ink hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none">
          {all ? "Show fewer" : `Show all ${items.length}`}
        </button>
      ) : null}
    </div>
  );
}
