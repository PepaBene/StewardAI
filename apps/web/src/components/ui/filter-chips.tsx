"use client";

import { cn } from "@/lib/utils";

/*
 * FilterChips (visual spec B40): one row of pills, each with a count and an optional unread
 * dot; the selected chip fills with the accent. Scrolls sideways below md with no scrollbar.
 */

export type FilterChip<K extends string> = {
  key: K;
  label: string;
  count?: number;
  /** Something new inside: a small accent dot. */
  unread?: boolean;
};

export function FilterChips<K extends string>({
  chips,
  value,
  onChange,
  label,
  className,
}: {
  chips: readonly FilterChip<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
  className?: string;
}) {
  return (
    <div role="toolbar" aria-label={label} className={cn("-mx-4 overflow-x-auto px-4 [scrollbar-width:none] md:mx-0 md:px-0", className)}>
      <ul className="flex gap-2 py-0.5 md:flex-wrap">
        {chips.map((c) => {
          const on = c.key === value;
          return (
            <li key={c.key} className="shrink-0">
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onChange(c.key)}
                className={cn(
                  "relative inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[13px] font-medium transition-colors",
                  on ? "bg-accent text-accent-foreground" : "bg-surface-muted text-text-muted hover:text-text",
                )}
              >
                {c.label}
                {c.count != null ? <span className={cn("num", on ? "opacity-85" : "text-text-faint")}>{c.count}</span> : null}
                {c.unread && !on ? (
                  <span aria-label="new items" className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-accent ring-2 ring-page" />
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
