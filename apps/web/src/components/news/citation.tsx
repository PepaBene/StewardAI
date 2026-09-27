"use client";

import { ExternalLinkIcon, FileSearchIcon } from "lucide-react";
import Link from "next/link";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { EvidenceRelationBadge } from "@/components/evidence/evidence-relation-badge";
import { ProvenanceTag } from "@/components/evidence/provenance-tag";
import type { CitationPreview } from "@/lib/news/digest";
import { TIERS } from "@/lib/news/labels";
import { useAnchoredPosition } from "@/lib/hooks/use-anchored-position";
import { cn } from "@/lib/utils";

const CHIP = "num rounded-sm border border-border px-1 text-xs text-accent hover:border-accent";

/**
 * A claim-level citation ("[2]", "fact 12"). Hovering it, or focusing it with the keyboard,
 * shows the exact stored text it links to (a news item's headline and summary, a figure's
 * snippet, an impact note source's snippet) with the source's tier. Escape hides it.
 */
export function CitationLink({
  href,
  label,
  title,
  external,
  preview,
  className,
}: {
  href: string;
  label: string;
  title: string;
  external: boolean;
  preview: CitationPreview | null;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLAnchorElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const position = useAnchoredPosition(open && preview !== null, anchorRef, panelRef);
  const tipId = useId();

  const handlers = {
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Escape" && open) {
        e.stopPropagation();
        setOpen(false);
      }
    },
  };
  const described = preview && open ? tipId : undefined;

  return (
    <>
      {external ? (
        <a
          ref={anchorRef}
          href={href}
          target="_blank"
          rel="noreferrer"
          aria-describedby={described}
          className={cn(CHIP, className)}
          {...handlers}
        >
          {label}
          <span className="sr-only">: {title}</span>
        </a>
      ) : (
        <Link ref={anchorRef} href={href} aria-describedby={described} className={cn(CHIP, className)} {...handlers}>
          {label}
          <span className="sr-only">: {title}</span>
        </Link>
      )}
      {open && preview
        ? createPortal(
            <div
              ref={panelRef}
              id={tipId}
              role="tooltip"
              style={position ? { top: position.top, left: position.left } : { top: 0, left: 0, visibility: "hidden" }}
              className="pointer-events-none fixed z-50 w-96 max-w-[calc(100vw-1rem)] space-y-1.5 rounded-lg border border-border bg-surface-raised p-3 text-left text-sm font-normal text-text shadow-lg"
            >
              <PreviewBody preview={preview} />
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export function PreviewBody({ preview }: { preview: CitationPreview }) {
  const tier = preview.tier;
  const tierInfo = tier === 1 || tier === 2 || tier === 3 ? TIERS[tier] : null;
  return (
    <>
      <p className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
        <span className="inline-flex items-center gap-1">
          {preview.kind === "fact" ? <FileSearchIcon aria-hidden="true" className="size-3.5" /> : <ExternalLinkIcon aria-hidden="true" className="size-3.5" />}
          {preview.source}
        </span>
        <span className="font-medium text-text">
          {preview.tierLabel}
          {tierInfo ? <span className="font-normal text-text-muted"> · {tierInfo.description.split(":")[0]}</span> : null}
        </span>
      </p>
      <p className="flex flex-wrap items-center gap-1.5 font-medium">
        {preview.title}
        {preview.relation ? <EvidenceRelationBadge relation={preview.relation} /> : null}
        {preview.provenance ? <ProvenanceTag provenance={preview.provenance} /> : null}
      </p>
      {preview.snippet ? (
        <blockquote className="border-l-2 border-border pl-2 text-xs break-words whitespace-pre-line text-text-muted">
          {preview.snippet}
        </blockquote>
      ) : (
        <p className="text-xs text-text-muted">
          {preview.kind === "fact" ? "No snippet stored for this figure." : "No stored text beyond the headline for this item."}
        </p>
      )}
    </>
  );
}
