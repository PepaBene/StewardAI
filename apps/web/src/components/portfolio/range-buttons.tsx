"use client";

import { cn } from "@/lib/utils";

/**
 * A segmented range/period switch (1M 3M YTD 1Y …): square, hairline-bordered, the pressed
 * option filled. `inverse` draws it in the accent's foreground for the solid-accent hero.
 */
export function RangeButtons<T extends string>({
  options,
  value,
  onChange,
  label,
  inverse = false,
  className,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
  inverse?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("inline-flex shrink-0 border text-xs font-semibold", inverse ? "border-accent-foreground/40" : "border-border", className)}
    >
      {options.map((o, i) => {
        const on = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.id)}
            className={cn(
              "px-2.5 py-1.5 outline-offset-2 focus-visible:outline-2",
              i > 0 && (inverse ? "border-l border-accent-foreground/40" : "border-l border-border"),
              inverse
                ? on
                  ? "bg-accent-foreground text-accent focus-visible:outline-accent-foreground"
                  : "text-accent-foreground hover:bg-accent-foreground/15 focus-visible:outline-accent-foreground"
                : on
                  ? "bg-accent text-accent-foreground focus-visible:outline-focus"
                  : "text-text hover:bg-surface-muted focus-visible:outline-focus",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
