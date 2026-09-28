"use client";

import { ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { CitationLink } from "@/components/news/citation";
import { EscalationActions, EscalationStateBadge, EscalationStatusBadge } from "@/components/news/escalation-status-badge";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint, BlueprintCorners, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { checkedTextClass, ClaimsSummaryLine, SentenceCheckMark } from "@/components/verify/sentence-check";
import { getBrowserApiClient, type EscalationOut } from "@/lib/api/client";
import { noteSentencePath } from "@/lib/feedback/feedback";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { formatCost, jobHref } from "@/lib/jobs/job-detail";
import {
  ESCALATIONS_HREF,
  escalationsForTicker,
  escalationStatus,
  formatMovePct,
  noteCitationPreview,
  parsePriceMove,
  sentenceCitations,
  SOURCE_KIND_LABELS,
  thesisLink,
  TRIGGER_LABELS,
} from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";

const LINK = "text-accent-ink underline-offset-4 hover:underline";

export function EscalationView({ id }: { id: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/escalations/{report_id}", { params: { path: { report_id: id } } }),
    [id],
  );
  const { state, reload } = useApiResource(fetcher);
  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading impact note…
      </p>
    );
  }
  if (state.kind === "error" && !state.previous) {
    return (
      <AlertMessage title={state.status === 404 ? `No impact note #${id}` : state.title}>
        <p>{state.status === 404 ? "The API has no escalation with this number." : state.detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Try again
        </Button>
      </AlertMessage>
    );
  }
  const escalation = state.kind === "ok" ? state.data : state.previous!;
  return <Escalation key={escalation.id} initial={escalation} />;
}

function Escalation({ initial }: { initial: EscalationOut }) {
  const [e, setE] = useState(initial);
  const { body } = e;
  const status = escalationStatus(body);
  const link = thesisLink(body);
  const move = parsePriceMove(body.price_move);
  const sources = body.sources ?? [];
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker={
          <>
            <Link href={ESCALATIONS_HREF} className="hover:underline">
              Oversight / Escalations
            </Link>{" "}
            / <span className="num">E-{e.id}</span>
          </>
        }
        title={body.headline || e.title}
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {e.ticker ? (
              <Link href={`/holdings/${encodeURIComponent(e.ticker)}`} className="font-mono text-xs text-accent-ink hover:underline">
                {e.ticker}
              </Link>
            ) : null}
            <span>{TRIGGER_LABELS[body.trigger]}</span>
            {body.trigger === "story" ? <span>· {eventLabel(body.event_type)}</span> : null}
            {body.severity !== null ? <SeverityBadge severity={body.severity} /> : null}
            <EscalationStatusBadge body={body} />
          </span>
        }
        aside={<AsOfLabel at={e.created_at} kind="escalation" prefix="Written" />}
      />

      <Blueprint aria-label="Status" className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <EscalationStateBadge status={e.status} />
          <span className="num text-text-muted">
            {e.acknowledged_at ? `Acknowledged ${formatDateTime(e.acknowledged_at)}` : "Not acknowledged yet"}
            {e.resolved_at ? ` · resolved ${formatDateTime(e.resolved_at)}` : ""}
          </span>
        </div>
        <EscalationActions escalation={e} onUpdated={setE} />
      </Blueprint>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className="flex flex-col gap-5">
          <section aria-label="What triggered it" className="space-y-1 border border-border bg-surface-muted px-5 py-4 text-sm">
            {body.trigger === "story" && body.cluster_id !== null ? (
              <p>
                A confirmed story.{" "}
                <Link href={`/news?story=${body.cluster_id}`} className={LINK}>
                  Open the story and its sources
                </Link>
              </p>
            ) : null}
            {move ? (
              <p className="num">
                A close-to-close move of <span className="font-medium">{formatMovePct(move.movePct)}</span> on {move.date}
                {move.symbol ? <> (Stooq <span className="font-mono">{move.symbol}</span>)</> : null}, beyond the threshold in{" "}
                <Link href="/config/event-rules#triggers-heading" className={LINK}>
                  Config: Event rules
                </Link>
                .
              </p>
            ) : null}
            {e.ticker ? (
              <p>
                <Link href={escalationsForTicker(e.ticker)} className={LINK}>
                  Every impact note for {e.ticker}
                </Link>
              </p>
            ) : null}
          </section>

          <RunSection escalation={e} />

          {status.kind === "not_assessed" ? (
            <AlertMessage title="Not assessed by a model">
              <p>
                The note states the event and its sources only: {status.reason}.
              </p>
            </AlertMessage>
          ) : null}

          {body.sentences.length > 0 ? (
            <section aria-labelledby="note-heading" className="blueprint space-y-2 px-6 py-5">
              <BlueprintCorners />
              <PanelHeading kicker="Impact note" title={<span id="note-heading">What happened</span>} />
              <ClaimsSummaryLine claims={body.claims} />
              {body.sentences.map((sentence, i) => (
                <p key={i} className="max-w-prose font-serif text-[15px] leading-relaxed">
                  <span className={checkedTextClass(sentence)}>{sentence.text}</span>
                  <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
                    {sentenceCitations(sentence, sources).map((c) => (
                      <CitationLink
                        key={c.url}
                        href={c.url}
                        label={c.number === null ? "src" : String(c.number)}
                        title={c.title}
                        external
                        preview={noteCitationPreview(sources, c.url)}
                      />
                    ))}
                  </span>
                  <SentenceCheckMark sentence={sentence} />
                  <FeedbackControl
                    className="ml-1"
                    target={{ kind: "report_sentence", id: e.id, sentencePath: noteSentencePath(i) }}
                    label={`sentence “${sentence.text.slice(0, 60)}${sentence.text.length > 60 ? "…" : ""}”`}
                  />
                </p>
              ))}
              {body.dropped_sentences ? (
                <p className="text-xs text-text-muted">
                  {body.dropped_sentences} sentence{body.dropped_sentences === 1 ? "" : "s"} removed because{" "}
                  {body.dropped_sentences === 1 ? "it" : "they"} cited nothing given.
                </p>
              ) : null}
            </section>
          ) : null}

          <section aria-labelledby="thesis-link-heading" className="blueprint space-y-2 px-6 py-5 text-sm">
            <BlueprintCorners />
            <h2 id="thesis-link-heading" className="text-[22px] leading-tight font-semibold">
              Thesis link
            </h2>
            {link.kind === "none" ? (
              <p>{link.text}.</p>
            ) : (
              <ul className="list-disc space-y-0.5 pl-5">
                {link.kpis.map((k) => (
                  <li key={k.id}>
                    KPI <span className="font-mono">{k.id}</span>
                    {k.description ? `: ${k.description}` : ""}
                  </li>
                ))}
                {link.exitConditions.map((c) => (
                  <li key={c}>Exit condition: {c}</li>
                ))}
              </ul>
            )}
            {body.rationale && status.kind === "assessed" ? <p className="text-text-muted">{body.rationale}</p> : null}
            {e.ticker ? (
              <p>
                <Link href={`/holdings/${encodeURIComponent(e.ticker)}#thesis-heading`} className={LINK}>
                  Open the thesis of {e.ticker}
                </Link>
              </p>
            ) : null}
          </section>

          <p className="num text-xs text-text-muted">
            Written {formatDateTime(e.created_at)}
            {body.writer ? ` by ${body.writer}` : " without a model"}.
          </p>
        </article>

        <aside aria-label="Sources" className="space-y-2">
          <h2 className="kicker">Sources ({sources.length})</h2>
          {sources.length === 0 ? <p className="text-sm text-text-muted">No sources stored.</p> : null}
          <ol className="space-y-2">
            {sources.map((s, i) => (
              <li key={s.url} className="border border-border p-3 text-sm">
                <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
                  <span className="num">
                    [{i + 1}] {s.outlet ?? "source"}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span>{SOURCE_KIND_LABELS[s.kind ?? "story"]}</span>
                    {typeof s.tier === "number" ? <TierBadge tier={s.tier} /> : null}
                  </span>
                </p>
                <a href={s.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-start gap-1 [overflow-wrap:anywhere] hover:text-accent hover:underline">
                  {s.headline ?? s.url}
                  <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
                </a>
                {s.snippet ? (
                  <details className="mt-1 text-xs">
                    <summary className="cursor-pointer text-text-muted">Stored snippet</summary>
                    <p className="mt-1 break-words whitespace-pre-line text-text-muted">{s.snippet}</p>
                  </details>
                ) : null}
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}

/** The event_escalation run that wrote the note: its status, cost and the models it called. */
function RunSection({ escalation: e }: { escalation: EscalationOut }) {
  return (
    <section aria-labelledby="run-heading" className="blueprint space-y-1 px-6 py-5 text-sm">
      <BlueprintCorners />
      <h2 id="run-heading" className="kicker">
        The run that wrote it
      </h2>
      {e.job_id === null ? (
        <p className="text-text-muted">No job run is recorded for this note.</p>
      ) : (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1">
          <dt className="text-text-muted">Run</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <Link href={jobHref(e.job_id)} className={LINK}>
              Job <span className="num">#{e.job_id}</span>
            </Link>
            {e.job_status ? <JobStatusBadge status={e.job_status} /> : <span className="text-text-muted">status unknown</span>}
          </dd>
          <dt className="text-text-muted">Cost</dt>
          <dd className="num" title={e.cost_usd === null ? undefined : `${e.cost_usd} USD`}>
            {e.cost_usd === null ? "No model or search cost recorded" : formatCost(e.cost_usd)}
          </dd>
          <dt className="text-text-muted">Models</dt>
          <dd className="font-mono text-xs">{e.models.length > 0 ? e.models.join(", ") : <span className="font-sans text-text-muted">None called</span>}</dd>
        </dl>
      )}
    </section>
  );
}
