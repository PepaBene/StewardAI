import { ChevronRightIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/*
 * Shared pieces of the Settings master-detail pattern (visual spec C.8): a rounded summary
 * panel that opens each editor's master list, a share bar with a table twin, a stat tile, and a
 * "More settings" disclosure that keeps rarely changed inputs out of the first screens.
 */

/** The summary visual's card: a heading, an optional one-line reading, then the visual. */
export function SummaryPanel({
  title,
  reading,
  action,
  children,
  className,
}: {
  title: string;
  reading?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-label={title} className={cn("min-w-0 space-y-3 rounded-card bg-surface-muted px-5 py-4", className)}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">{title}</h2>
          {reading ? <p className="text-sm text-text-muted">{reading}</p> : null}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export type ShareSegment = { label: string; value: number; tone?: "accent" | "gain" | "loss" | "warning" | "muted" };

const TONE_BG = {
  accent: "bg-accent",
  gain: "bg-gain",
  loss: "bg-loss",
  warning: "bg-warning-signal",
  muted: "bg-chart-track",
} as const;

/** A stacked share bar. The legend names every segment with its count, so it doubles as the table twin. */
export function ShareBar({ segments, label }: { segments: readonly ShareSegment[]; label: string }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (total <= 0) return <p className="text-sm text-text-muted">Nothing to show yet.</p>;
  return (
    <div className="space-y-2">
      <div role="img" aria-label={`${label}: ${segments.map((s) => `${s.label} ${s.value}`).join(", ")}`} className="flex h-3 gap-0.5 overflow-hidden rounded-full bg-chart-track">
        {segments
          .filter((s) => s.value > 0)
          .map((s) => (
            <div key={s.label} className={cn("h-full", TONE_BG[s.tone ?? "accent"])} style={{ width: `${(s.value / total) * 100}%` }} />
          ))}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center gap-1.5">
            <span aria-hidden="true" className={cn("size-2 rounded-full", TONE_BG[s.tone ?? "accent"])} />
            {s.label} <span className="font-medium text-text">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One figure with its caption, for the summary row. */
export function StatTile({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="min-w-32 rounded-control bg-surface px-3 py-2">
      <p className="text-xs text-text-muted">{label}</p>
      <p className="font-serif text-xl tabular-nums">{value}</p>
      {note ? <p className="text-xs text-text-muted">{note}</p> : null}
    </div>
  );
}

/** Section tabs for an editor: one section on screen at a time. */
export function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: readonly { id: T; label: string; badge?: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 rounded-card bg-surface-muted p-1">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={t.id === value}
          onClick={() => onChange(t.id)}
          className={cn(
            "rounded-control px-3 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-focus/30",
            t.id === value ? "bg-surface font-semibold text-text shadow-xs" : "text-text-muted hover:text-text",
          )}
        >
          {t.label}
          {t.badge ? <span className="ml-1.5 text-xs font-normal text-text-muted">{t.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

/** Rarely changed inputs, collapsed by default. */
export function MoreSettings({
  title = "More settings",
  hint,
  defaultOpen = false,
  children,
  className,
}: {
  title?: string;
  hint?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <details open={defaultOpen || undefined} className={cn("group rounded-card bg-surface-muted", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-card px-4 py-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-focus/30">
        <ChevronRightIcon aria-hidden="true" className="size-4 transition-transform group-open:rotate-90" />
        {title}
        {hint ? <span className="font-normal text-text-muted">{hint}</span> : null}
      </summary>
      <div className="space-y-4 px-4 pb-4">{children}</div>
    </details>
  );
}
