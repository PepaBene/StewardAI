"use client";

import { cn } from "@/lib/utils";

/** A small segmented control (radio-like, aria-pressed buttons) used by chart controls. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { id: T; label: string; disabled?: boolean; hint?: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex border border-border text-xs font-semibold">
      {options.map((o, i) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={value === o.id}
          disabled={o.disabled}
          title={o.hint}
          onClick={() => onChange(o.id)}
          className={cn(
            "px-2.5 py-1 outline-hidden focus-visible:outline-2 focus-visible:outline-focus disabled:opacity-40",
            i > 0 && "border-l border-border",
            value === o.id ? "bg-accent text-accent-foreground" : "text-text-muted hover:bg-border-soft",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
