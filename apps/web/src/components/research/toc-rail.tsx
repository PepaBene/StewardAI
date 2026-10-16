"use client";

import { useEffect, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type TocItem = { id: string; label: ReactNode };

/**
 * A sticky table of contents for long reports. It shows from lg up (below that the sections
 * simply follow one another), and marks the section in view as the reader scrolls.
 */
export function TocRail({ items, label = "On this page", className }: { items: readonly TocItem[]; label?: string; className?: string }) {
  const [active, setActive] = useState<string | null>(items[0]?.id ?? null);
  // Sections that are not on the page (a report without risk notes) drop out once it has rendered.
  const [present, setPresent] = useState<ReadonlySet<string> | null>(null);
  const ids = items.map((i) => i.id).join("|");

  useEffect(() => {
    const targets = ids
      .split("|")
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reads the rendered page once
    setPresent(new Set(targets.map((t) => t.id)));
    if (targets.length === 0 || typeof IntersectionObserver === "undefined") return;
    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id);
          else visible.delete(e.target.id);
        }
        const first = targets.find((t) => visible.has(t.id));
        if (first) setActive(first.id);
      },
      { rootMargin: "0px 0px -60% 0px" },
    );
    targets.forEach((t) => observer.observe(t));
    return () => observer.disconnect();
  }, [ids]);

  const shown = present ? items.filter((i) => present.has(i.id)) : items;
  if (shown.length < 2) return null;
  return (
    <nav aria-label={label} className={cn("sticky top-4 hidden self-start lg:block", className)}>
      <p className="mb-2 font-heading text-sm font-semibold">{label}</p>
      <ol className="m-0 list-none space-y-0.5 border-l border-border p-0 text-sm">
        {shown.map((i) => (
          <li key={i.id}>
            <a
              href={`#${i.id}`}
              aria-current={active === i.id ? "location" : undefined}
              className={cn(
                "-ml-px block border-l-2 py-1 pl-3 focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
                active === i.id ? "border-accent font-medium text-text" : "border-transparent text-text-muted hover:text-text",
              )}
            >
              {i.label}
            </a>
          </li>
        ))}
      </ol>
    </nav>
  );
}
