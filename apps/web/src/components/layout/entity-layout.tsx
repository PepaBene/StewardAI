"use client";

import Link from "next/link";
import { useEffect, useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * EntityLayout (visual spec B39, page type T2): the shared frame of the company and fund pages.
 * At lg a 264 px sticky sidebar carries the identity block and the numbered section list; below
 * lg the identity collapses into a compact header and the sections become a sticky row of
 * horizontally scrolling chips. Each section is a URL, so the active one comes from the router.
 */

export type EntitySectionLink = {
  key: string;
  href: string;
  label: string;
  /** Shown only for sequences that mean something (the nine company sections). */
  number?: number;
  /** A short count such as "3/6". */
  badge?: string | null;
  active: boolean;
};

function SectionList({ sections, navLabel }: { sections: readonly EntitySectionLink[]; navLabel: string }) {
  return (
    <nav aria-label={navLabel}>
      <ul className="space-y-0.5">
        {sections.map((s) => (
          <li key={s.key}>
            <Link
              href={s.href}
              aria-current={s.active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2 border-l-[3px] py-1.5 pr-2 pl-2.5 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
                s.active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
              )}
            >
              {s.number !== undefined ? <span className="num w-4 shrink-0 text-xs text-text-faint">{s.number}</span> : null}
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {s.badge ? <span className="num rounded-control bg-surface-muted px-1.5 text-xs text-text-muted">{s.badge}</span> : null}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function SectionChips({ sections, navLabel }: { sections: readonly EntitySectionLink[]; navLabel: string }) {
  const activeRef = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    activeRef.current?.scrollIntoView?.({ inline: "center", block: "nearest" });
  }, [sections]);
  return (
    <nav aria-label={navLabel} className="sticky top-0 z-10 -mx-1 overflow-x-auto bg-surface px-1 py-2 [scrollbar-width:none] lg:hidden">
      <ul className="flex min-w-max gap-1.5">
        {sections.map((s) => (
          <li key={s.key}>
            <Link
              ref={s.active ? activeRef : undefined}
              href={s.href}
              aria-current={s.active ? "page" : undefined}
              className={cn(
                "block rounded-control px-3 py-1.5 text-sm whitespace-nowrap",
                s.active ? "bg-accent font-semibold text-accent-foreground" : "bg-surface-muted text-text-muted",
              )}
            >
              {s.number !== undefined ? `${s.number} ` : ""}
              {s.label}
              {s.badge ? ` ${s.badge}` : ""}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** A small secondary nav for a section with several routes (Valuation · Analyst targets · Peers). */
export function SectionSubNav({ items, label }: { items: readonly { href: string; label: string; active: boolean }[]; label: string }) {
  if (items.length < 2) return null;
  return (
    <nav aria-label={label} className="-mx-1 overflow-x-auto [scrollbar-width:none]">
      <ul className="flex min-w-max gap-1 px-1">
        {items.map((i) => (
          <li key={i.href}>
            <Link
              href={i.href}
              aria-current={i.active ? "page" : undefined}
              className={cn(
                "block rounded-control px-3 py-1 text-sm whitespace-nowrap",
                i.active ? "bg-surface-muted font-semibold text-text" : "text-text-muted hover:text-text",
              )}
            >
              {i.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function EntityLayout({
  sidebar,
  header,
  sections,
  navLabel,
  children,
}: {
  /** Identity block for the sidebar (lg and up): mark, name, price, chips, actions, position. */
  sidebar: ReactNode;
  /** Compact identity for narrow screens. */
  header: ReactNode;
  sections: readonly EntitySectionLink[];
  navLabel: string;
  children: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 gap-x-6 gap-y-3 lg:grid-cols-[264px_minmax(0,1fr)]">
      <aside className="hidden lg:block">
        <div className="sticky top-20 flex max-h-[calc(100dvh-6rem)] flex-col gap-4 overflow-y-auto pr-1">
          {sidebar}
          <SectionList sections={sections} navLabel={navLabel} />
        </div>
      </aside>
      <div className="min-w-0 lg:hidden">{header}</div>
      <SectionChips sections={sections} navLabel={`${navLabel} (short list)`} />
      <div className="flex min-w-0 flex-col gap-5 lg:col-start-2 lg:row-start-1 lg:row-span-3">{children}</div>
    </div>
  );
}
