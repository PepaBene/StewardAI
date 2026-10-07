"use client";

import { ExternalLinkIcon, LoaderCircleIcon } from "lucide-react";
import type { ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import { rangePosition, splitReason, toNumber, type Num } from "@/lib/company/company";
import { isExternalUrl } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

/** Loading, error and ready states for one resource; stale data stays visible next to an error. */
export function ResourceGate<T>({
  state,
  label,
  children,
}: {
  state: ResourceState<T>;
  label: string;
  children: (data: T) => ReactNode;
}) {
  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading {label}…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="space-y-4">
        <AlertMessage title={state.title}>{state.detail}</AlertMessage>
        {state.previous ? children(state.previous) : null}
      </div>
    );
  }
  return <>{children(state.data)}</>;
}

/**
 * A value that is not a number: "n/m" (not meaningful, with the reason) or "Unknown" (a source
 * is missing). Never rendered as zero or a blank, so a gap cannot be mistaken for a figure.
 */
export function Absent({ reason, className }: { reason?: string | null; className?: string }) {
  const r = splitReason(reason);
  const label = r.kind === "nm" ? "n/m" : "Unknown";
  return (
    <span
      title={r.text || "no value"}
      className={cn("inline-block border border-dashed border-border px-1 text-[11px] leading-5 text-text-muted", className)}
    >
      {label}
      <span className="sr-only">: {r.text || "no value"}</span>
    </span>
  );
}

/** A source link that opens in a new tab only when external. */
export function SourceLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  if (!href) return <span className="text-text-muted">{children}</span>;
  const external = isExternalUrl(href);
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noreferrer noopener" } : {})}
      className="inline-flex items-center gap-1 text-accent-ink hover:underline focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
    >
      {children}
      {external ? <ExternalLinkIcon aria-hidden="true" className="size-3" /> : null}
    </a>
  );
}

/**
 * Where a value sits among peers: the low–high range as a bar, the peer median as a tick and
 * the company as a dot. Says so in text for anyone not reading the picture.
 */
export function RangeStrip({
  value,
  low,
  high,
  median,
  label,
}: {
  value: Num;
  low: Num;
  high: Num;
  median: Num;
  label: string;
}) {
  const v = toNumber(value);
  const lo = toNumber(low);
  const hi = toNumber(high);
  const md = toNumber(median);
  const at = rangePosition(v, lo, hi);
  const mid = rangePosition(md, lo, hi);
  if (at === null || lo === null || hi === null) return null;
  return (
    <span className="relative inline-block h-3 w-28 align-middle" role="img" aria-label={label}>
      <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
      {mid !== null ? <span className="absolute top-0 h-3 w-px bg-text-muted" style={{ left: `${mid * 100}%` }} /> : null}
      <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 border border-surface bg-accent" style={{ left: `${at * 100}%` }} />
    </span>
  );
}

/** A small framed number with a caption, for strips of key figures. */
export function Stat({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <div className="min-w-0 border-l border-border pl-3">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num mt-0.5 text-lg font-semibold">{children}</dd>
      {note ? <p className="mt-0.5 text-[11px] text-text-muted">{note}</p> : null}
    </div>
  );
}
