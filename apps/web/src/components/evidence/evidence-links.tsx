import { ExternalLinkIcon, FileSearchIcon } from "lucide-react";
import Link from "next/link";

import type { FactOut } from "@/lib/api/client";
import { isExternalUrl } from "@/lib/sources/sources";

function sourceHost(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/** The fact's evidence page (archived copy and context) and its original source URL. */
export function EvidenceLinks({ fact, showHost = false }: { fact: Pick<FactOut, "id" | "source_url">; showHost?: boolean }) {
  const host = showHost ? sourceHost(fact.source_url) : null;
  return (
    <span className="inline-flex flex-wrap items-center gap-3 text-xs">
      <Link
        href={`/evidence/${fact.id}`}
        className="inline-flex items-center gap-1 text-accent-ink underline-offset-4 hover:underline"
      >
        <FileSearchIcon aria-hidden="true" className="size-3.5" />
        Evidence
        <span className="sr-only"> for figure #{fact.id}</span>
      </Link>
      {isExternalUrl(fact.source_url) ? (
        <a
          href={fact.source_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-text-muted hover:text-text hover:underline"
        >
          {host ? `Source: ${host}` : "Source"} <ExternalLinkIcon aria-hidden="true" className="size-3" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      ) : (
        // Not an http(s) link: shown as text, never as a clickable href.
        <span className="break-all text-text-muted">Source: {fact.source_url}</span>
      )}
    </span>
  );
}
