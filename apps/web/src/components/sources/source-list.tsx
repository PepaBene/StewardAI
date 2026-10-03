"use client";

import { ExternalLinkIcon, FileSearchIcon, TriangleAlertIcon } from "lucide-react";
import Link from "next/link";

import { CitationLink } from "@/components/news/citation";
import { TierBadge } from "@/components/news/severity-badge";
import type { Source } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import {
  chipsFor,
  hostOf,
  knownAtLabel,
  sourceKindLabel,
  sourceLinks,
  sourcePreview,
  type CitationIndex,
  type SourceCardEntry,
} from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

/**
 * The numbered citations of one sentence (or row). Each chip jumps to its source card in the
 * sidebar and, on hover or keyboard focus, previews the stored snippet and the tier. A
 * citation with no card is shown as such, never dropped.
 */
export function SourceChips({
  ids,
  index,
  className,
}: {
  ids: readonly string[] | null | undefined;
  index: CitationIndex;
  className?: string;
}) {
  const chips = chipsFor(ids, index);
  if (chips.length === 0) return null;
  return (
    <span className={cn("ml-1 inline-flex flex-wrap gap-0.5 align-baseline", className)}>
      {chips.map((chip) =>
        chip.card && chip.href ? (
          <CitationLink
            key={chip.id}
            href={chip.href}
            label={chip.label}
            title={`source ${chip.label}, ${chip.card.source.title}`}
            external={false}
            preview={sourcePreview(chip.card.source)}
          />
        ) : (
          <span
            key={chip.id}
            title="This citation has no source in the report's list"
            className="inline-flex items-center gap-0.5 rounded-sm border border-dashed border-border px-1 text-xs text-text-muted"
          >
            <TriangleAlertIcon aria-hidden="true" className="size-3 text-warning-signal" />
            {chip.id}: no source
          </span>
        ),
      )}
    </span>
  );
}

/**
 * "known <date>" for a cited source: when the app learned of it. A date-only value (13F filing
 * date) reads as a date with no time; no value says so rather than guessing.
 */
export function SourceKnownAt({ known, lead = true }: { known: string | null | undefined; /** Prefix a "·" separator (inline after other metadata). */ lead?: boolean }) {
  const k = knownAtLabel(known);
  if (!k) return <span>{lead ? "· " : ""}known-at not recorded</span>;
  return (
    <span title={k.dateOnly ? "Date only: the source records a date, so no time of day is shown." : undefined}>
      {lead ? "· " : ""}known {k.text}
      {k.dateOnly ? " (date only)" : ""}
    </span>
  );
}

/** The app page and / or the external page of a source. */
export function SourceLinksLine({ source, className }: { source: Source; className?: string }) {
  const links = sourceLinks(source);
  if (!links.internal && !links.external) {
    return <p className={cn("text-xs text-text-muted", className)}>No link stored ({source.ref}).</p>;
  }
  return (
    <p className={cn("flex flex-wrap gap-x-3 gap-y-1 text-xs", className)}>
      {links.internal ? (
        <Link href={links.internal} className="inline-flex items-center gap-1 text-accent-ink hover:underline">
          <FileSearchIcon aria-hidden="true" className="size-3" />
          Open in the app <span className="font-mono">{links.internal}</span>
        </Link>
      ) : null}
      {links.external ? (
        <a href={links.external} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 break-all text-accent-ink hover:underline">
          {hostOf(links.external)}
          <ExternalLinkIcon aria-hidden="true" className="size-3 shrink-0" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      ) : null}
    </p>
  );
}

/** One source card: number, kind, tier, title, as-of date, stored snippet and its links. */
export function SourceCard({ entry }: { entry: SourceCardEntry }) {
  const { source: s, number, anchor, citedBy } = entry;
  return (
    <li id={anchor} tabIndex={-1} className="scroll-mt-24 space-y-1 rounded-md border border-border bg-surface-raised p-3 text-sm outline-none target:border-accent focus-visible:border-accent">
      <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
        <span className="num">
          [{number}] <span className="font-mono">{s.id}</span> · {sourceKindLabel(s.kind)}
        </span>
        {typeof s.tier === "number" ? <TierBadge tier={s.tier} /> : <span title="A record in this app: no outlet tier">no tier</span>}
      </p>
      <p className="font-medium break-words">{s.title}</p>
      <p className="num text-xs text-text-muted">
        {s.as_of ? <>As of {formatAsOf(s.as_of)} · </> : null}
        {citedBy === 0 ? "not cited in the text" : `cited ${citedBy}×`}
      </p>
      {s.snippet ? (
        <blockquote className="border-l-2 border-border pl-2 text-xs break-words whitespace-pre-line text-text-muted">{s.snippet}</blockquote>
      ) : null}
      <SourceLinksLine source={s} />
    </li>
  );
}

function formatAsOf(value: string): string {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? formatDate(value) : value;
}

/** The sidebar list of every source, with a warning for any citation without a card. */
export function SourceSidebar({ index, title = "Sources" }: { index: CitationIndex; title?: string }) {
  return (
    <aside aria-labelledby="sources-heading" className="space-y-2">
      <h2 id="sources-heading" className="text-sm font-medium text-text-muted">
        {title} ({index.cards.length})
      </h2>
      {index.unresolved.length > 0 ? (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-warning-signal/40 bg-warning-signal-subtle p-2 text-xs text-text">
          <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning-signal" />
          Cited but not in the source list: {index.unresolved.join(", ")}.
        </p>
      ) : null}
      {index.cards.length === 0 ? <p className="text-sm text-text-muted">No sources stored.</p> : null}
      <ol className="space-y-2">
        {index.cards.map((entry) => (
          <SourceCard key={entry.source.id} entry={entry} />
        ))}
      </ol>
    </aside>
  );
}
