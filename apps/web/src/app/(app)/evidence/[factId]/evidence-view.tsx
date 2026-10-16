"use client";

import { ArchiveIcon, ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { EvidenceRelationBadge } from "@/components/evidence/evidence-relation-badge";
import { ProvenanceTag } from "@/components/evidence/provenance-tag";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EvidenceLink } from "@/lib/api/client";
import { humanizeMetric } from "@/lib/evidence/confidence";
import { factProvenance } from "@/lib/evidence/provenance";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { isExternalUrl } from "@/lib/sources/sources";

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
      <PageHeader
        kicker={
          <>
            <Link href={`/companies/${encodeURIComponent(link.ticker)}`} className="font-mono hover:underline">
              {link.ticker}
            </Link>{" "}
            · Evidence <span className="num">#{link.fact_id}</span>
          </>
        }
        title={
          <>
            {humanizeMetric(link.metric)} {link.period}{" "}
            <span className="num font-mono text-[0.55em] font-medium tracking-normal">{link.value_as_written}</span>
          </>
        }
        description={
          <div className="mt-1 space-y-2">
            <p className="flex flex-wrap items-center gap-2">
              <ConfidenceBadge confidence={link.confidence} />
              <EvidenceRelationBadge relation={link.relation} />
              <ProvenanceTag provenance={factProvenance(link)} />
            </p>
            <p className="flex flex-wrap items-center gap-2">
              Is this figure right?
              <FeedbackControl target={{ kind: "fact", id: link.fact_id }} label={`figure ${humanizeMetric(link.metric)} ${link.period}`} />
            </p>
          </div>
        }
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Blueprint aria-labelledby="quote-heading" className="space-y-3 p-5">
          <PanelHeading level={2} kicker="Snippet" title={<span id="quote-heading">Quoted from the source</span>} />
          <blockquote className="border-l-2 border-accent bg-surface-muted p-3 font-mono text-sm break-words">
            {link.snippet}
            {link.unit_snippet ? <span className="mt-2 block text-text-muted">{link.unit_snippet}</span> : null}
          </blockquote>
        </Blueprint>

        <Blueprint aria-labelledby="context-heading" className="space-y-3 p-5">
          <PanelHeading level={2} kicker="Archive" title={<span id="context-heading">In the archived document</span>} />
          {link.context ? (
            <pre className="max-h-[32rem] overflow-auto border border-border bg-surface-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
              {link.context}
            </pre>
          ) : (
            <AlertMessage title="Snippet not located in the archived text">
              The archived copy is linked below; the snippet may span a page break.
            </AlertMessage>
          )}
        </Blueprint>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {isExternalUrl(link.evidence_url) ? (
          <Button asChild>
            <a href={link.evidence_url} target="_blank" rel="noreferrer">
              <ArchiveIcon aria-hidden="true" />
              Archived copy
            </a>
          </Button>
        ) : null}
        {isExternalUrl(link.source_url) ? (
          <Button asChild variant="outline">
            <a href={link.source_url} target="_blank" rel="noreferrer">
              <ExternalLinkIcon aria-hidden="true" />
              Original source
            </a>
          </Button>
        ) : (
          // Not an http(s) link: shown as text, never as a clickable href.
          <span className="text-sm break-all text-text-muted">Original source: {link.source_url}</span>
        )}
      </div>
      <p className="text-xs text-text-muted">
        Fetched {formatDate(new Date(link.fetched_at))} · {link.content_type} · SHA-256{" "}
        <span className="font-mono break-all">{link.sha256}</span>. The archived-copy link expires after 10
        minutes; reload this page for a fresh one.
      </p>
    </div>
  );
}
