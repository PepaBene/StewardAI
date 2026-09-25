"use client";

import { ExternalLinkIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { AlertMessage } from "@/components/alert-message";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { EscalationStatusBadge } from "@/components/news/escalation-status-badge";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type EscalationOut } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  ESCALATIONS_HREF,
  escalationsForTicker,
  escalationStatus,
  formatMovePct,
  parsePriceMove,
  sentenceCitations,
  SOURCE_KIND_LABELS,
  thesisLink,
  TRIGGER_LABELS,
} from "@/lib/news/escalation";
import { eventLabel } from "@/lib/news/labels";

const LINK = "text-accent underline-offset-4 hover:underline";

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
  return <Escalation escalation={escalation} />;
}

function Escalation({ escalation: e }: { escalation: EscalationOut }) {
  const { body } = e;
  const status = escalationStatus(body);
  const link = thesisLink(body);
  const move = parsePriceMove(body.price_move);
  const sources = body.sources ?? [];
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href="/reports" className="hover:text-text hover:underline">
              Reports
            </Link>{" "}
            /{" "}
            <Link href={ESCALATIONS_HREF} className="hover:text-text hover:underline">
              Impact notes
            </Link>{" "}
            / <span className="num">#{e.id}</span>
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{body.headline || e.title}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
            {e.ticker ? (
              <Link href={`/holdings/${encodeURIComponent(e.ticker)}`} className="font-mono text-xs text-accent hover:underline">
                {e.ticker}
              </Link>
            ) : null}
            <span>{TRIGGER_LABELS[body.trigger]}</span>
            {body.trigger === "story" ? <span>· {eventLabel(body.event_type)}</span> : null}
            {body.severity !== null ? <SeverityBadge severity={body.severity} /> : null}
            <EscalationStatusBadge body={body} />
          </p>
        </div>
        <AsOfLabel at={e.created_at} kind="escalation" prefix="Written" />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className="space-y-6">
          <section aria-label="What triggered it" className="space-y-1 rounded-lg border border-border bg-surface-muted p-4 text-sm">
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

          {status.kind === "not_assessed" ? (
            <AlertMessage title="Not assessed by a model">
              <p>
                The note states the event and its sources only: {status.reason}.
              </p>
            </AlertMessage>
          ) : null}

          {body.sentences.length > 0 ? (
            <section aria-labelledby="note-heading" className="space-y-2">
              <h2 id="note-heading" className="text-lg font-semibold">
                What happened
              </h2>
              {body.sentences.map((sentence, i) => (
                <p key={i} className="max-w-prose text-sm">
                  {sentence.text}
                  <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
                    {sentenceCitations(sentence, sources).map((c) => (
                      <a
                        key={c.url}
                        href={c.url}
                        target="_blank"
                        rel="noreferrer"
                        title={c.title}
                        className="num rounded-sm border border-border px-1 text-xs text-accent hover:border-accent"
                      >
                        {c.number ?? "src"}
                        <span className="sr-only">: {c.title}</span>
                      </a>
                    ))}
                  </span>
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

          <section aria-labelledby="thesis-link-heading" className="space-y-2 rounded-lg border border-border bg-surface-raised p-4 text-sm">
            <h2 id="thesis-link-heading" className="text-lg font-semibold">
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
          <h2 className="text-sm font-medium text-text-muted">Sources ({sources.length})</h2>
          {sources.length === 0 ? <p className="text-sm text-text-muted">No sources stored.</p> : null}
          <ol className="space-y-2">
            {sources.map((s, i) => (
              <li key={s.url} className="rounded-md border border-border bg-surface-raised p-3 text-sm">
                <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
                  <span className="num">
                    [{i + 1}] {s.outlet ?? "source"}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span>{SOURCE_KIND_LABELS[s.kind ?? "story"]}</span>
                    {typeof s.tier === "number" ? <TierBadge tier={s.tier} /> : null}
                  </span>
                </p>
                <a href={s.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-start gap-1 break-all hover:text-accent hover:underline">
                  {s.headline ?? s.url}
                  <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
                </a>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}
