"use client";

import { useEffect, type ReactNode } from "react";

import { ProvenanceFooter } from "@/components/data-state";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { DataProvenance } from "@/lib/data-state/provenance";
import { cn } from "@/lib/utils";

/** ISO date or instant -> "YYYY-MM-DD" (the day it names); anything else is shown as stored. */
export function day(value: string | null | undefined): string {
  if (!value) return "not reported";
  return /^\d{4}-\d{2}-\d{2}/.test(value) ? value.slice(0, 10) : value;
}

/** A framed sub-section of the holding page with an anchor id, a takeaway sentence and provenance. */
export function HoldingPanel({
  id,
  kicker,
  title,
  takeaway,
  action,
  provenance,
  children,
  className,
}: {
  id: string;
  kicker: ReactNode;
  title: ReactNode;
  takeaway?: ReactNode;
  action?: ReactNode;
  provenance?: DataProvenance | readonly DataProvenance[];
  children: ReactNode;
  className?: string;
}) {
  const footers = provenance === undefined ? [] : Array.isArray(provenance) ? provenance : [provenance as DataProvenance];
  return (
    <Blueprint id={id} aria-labelledby={`${id}-heading`} className={cn("flex min-w-0 scroll-mt-4 flex-col gap-3 px-5 py-5", className)}>
      <PanelHeading kicker={kicker} title={<span id={`${id}-heading`}>{title}</span>} action={action} />
      {takeaway ? <p className="max-w-prose text-sm text-text">{takeaway}</p> : null}
      {children}
      {footers.map((p, i) => (
        <ProvenanceFooter key={i} provenance={p} />
      ))}
    </Blueprint>
  );
}

const PROVENANCE_LABEL: Record<string, string> = { primary: "Primary", vendor: "Vendor", owner: "Owner", community: "Community", unclassified: "Unclassified" };

/** Primary / vendor / owner / community / unclassified, spelled out (never colour alone). */
export function ProvenanceClass({ cls }: { cls: string }) {
  return (
    <span className="inline-flex items-center border border-border px-1 font-mono text-[10px] tracking-wide whitespace-nowrap text-text uppercase" title="Provenance class of the source, assigned from the exact source name">
      {PROVENANCE_LABEL[cls] ?? cls}
    </span>
  );
}

export function TableWrap({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[34rem] text-xs">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

export const TH = "px-3 py-1.5 text-left text-[11px] font-medium tracking-[0.08em] text-text-muted uppercase";
export const TD = "px-3 py-1.5 align-top";

export function SourceLink({ url, label }: { url: string | null | undefined; label: string }) {
  if (!url || !/^https?:\/\//i.test(url)) return <span>{label}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="text-accent-ink hover:underline">
      {label}
    </a>
  );
}

/**
 * Sections of the holding page mount only after their request finishes, so the browser's one-shot
 * hash scroll (`#evidence`) misses them. Rendered inside a loaded section, this scrolls to the
 * element the URL hash names once it exists.
 */
export function ScrollToHash() {
  useEffect(() => {
    try {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (id) document.getElementById(id)?.scrollIntoView();
    } catch {
      // A malformed hash is not worth a crash.
    }
  }, []);
  return null;
}
