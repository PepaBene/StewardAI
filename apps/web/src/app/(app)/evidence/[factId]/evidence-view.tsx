"use client";

import { ArchiveIcon, ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EvidenceLink } from "@/lib/api/client";
import { humanizeMetric } from "@/lib/evidence/confidence";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

/*
 * One fact's evidence: the quoted snippet, the archived text around it, the original URL,
 * and a short-lived link to the archived copy (which still resolves after the company's
 * website changes).
 */
export function EvidenceView({ factId }: { factId: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/facts/{fact_id}/evidence", { params: { path: { fact_id: factId } } }),
    [factId],
  );
  const { state, reload } = useApiResource(fetcher);

  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading evidence…
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <AlertMessage title={state.title}>
        <p>{state.detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Try again
        </Button>
      </AlertMessage>
    );
  }
  return <Evidence link={state.data} />;
}

function Evidence({ link }: { link: EvidenceLink }) {
  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-text-muted">
          <Link href={`/holdings/${encodeURIComponent(link.ticker)}`} className="hover:text-text hover:underline">
            {link.ticker}
          </Link>{" "}
          / evidence #{link.fact_id}
        </p>
        <h1 className="flex flex-wrap items-center gap-2 text-2xl font-semibold tracking-tight">
          {humanizeMetric(link.metric)} {link.period}
          <span className="num font-mono text-xl">{link.value_as_written}</span>
          <ConfidenceBadge confidence={link.confidence} />
        </h1>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-text-muted">
          Is this figure right?
          <FeedbackControl target={{ kind: "fact", id: link.fact_id }} label={`figure ${humanizeMetric(link.metric)} ${link.period}`} />
        </p>
      </div>

      <section aria-labelledby="quote-heading" className="space-y-2">
        <h2 id="quote-heading" className="text-sm font-medium text-text-muted">
          Quoted from the source
        </h2>
        <blockquote className="rounded-md border border-border bg-surface-raised p-3 font-mono text-sm break-words">
          {link.snippet}
          {link.unit_snippet ? <span className="mt-2 block text-text-muted">{link.unit_snippet}</span> : null}
        </blockquote>
      </section>

      <section aria-labelledby="context-heading" className="space-y-2">
        <h2 id="context-heading" className="text-sm font-medium text-text-muted">
          In the archived document
        </h2>
        {link.context ? (
          <pre className="overflow-x-auto rounded-md border border-border bg-surface-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
            {link.context}
          </pre>
        ) : (
          <AlertMessage title="Snippet not located in the archived text">
            The archived copy is linked below; the snippet may span a page break.
          </AlertMessage>
        )}
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <Button asChild>
          <a href={link.evidence_url} target="_blank" rel="noreferrer">
            <ArchiveIcon aria-hidden="true" />
            Archived copy
          </a>
        </Button>
        <Button asChild variant="outline">
          <a href={link.source_url} target="_blank" rel="noreferrer">
            <ExternalLinkIcon aria-hidden="true" />
            Original source
          </a>
        </Button>
      </div>
      <p className="text-xs text-text-muted">
        Fetched {formatDate(new Date(link.fetched_at))} · {link.content_type} · SHA-256{" "}
        <span className="font-mono break-all">{link.sha256}</span>. The archived-copy link expires after 10
        minutes; reload this page for a fresh one.
      </p>
    </div>
  );
}
