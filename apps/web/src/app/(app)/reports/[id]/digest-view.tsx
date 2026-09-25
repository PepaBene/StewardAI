"use client";

import { CheckCircle2Icon, ExternalLinkIcon, LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import {
  getBrowserApiClient,
  type DigestSentence,
  type DigestSource,
  type ReportOut,
} from "@/lib/api/client";
import { formatDate, formatTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { citationLinks, sourceAnchor, sourceNumbers } from "@/lib/news/digest";
import { eventLabel, TIERS } from "@/lib/news/labels";

export function DigestView({ id }: { id: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/reports/{report_id}", { params: { path: { report_id: id } } }),
    [id],
  );
  const { state } = useApiResource(fetcher);
  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading digest…
      </p>
    );
  }
  if (state.kind === "error" && !state.previous) {
    return <AlertMessage title={state.title}>{state.detail}</AlertMessage>;
  }
  const report = state.kind === "ok" ? state.data : state.previous!;
  return <Digest report={report} />;
}

function formatWindow(iso: string): string {
  const d = new Date(iso);
  return `${formatDate(d)}, ${formatTime(d)}`;
}

function Citations({
  sentence,
  sources,
  numbers,
}: {
  sentence: DigestSentence;
  sources: DigestSource[];
  numbers: Map<number, number>;
}) {
  const links = citationLinks(sentence, sources, numbers);
  return (
    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
      {links.map((link) =>
        link.external ? (
          <a
            key={link.key}
            href={link.href}
            target="_blank"
            rel="noreferrer"
            title={link.title}
            className="num rounded-sm border border-border px-1 text-xs text-accent hover:border-accent"
          >
            {link.label}
            <span className="sr-only">: {link.title}</span>
          </a>
        ) : (
          <Link
            key={link.key}
            href={link.href}
            title={link.title}
            className="rounded-sm border border-border px-1 text-xs text-accent hover:border-accent"
          >
            {link.label}
          </Link>
        ),
      )}
    </span>
  );
}

function Digest({ report }: { report: ReportOut }) {
  const body = report.body;
  const sources = useMemo(() => body.sources ?? [], [body.sources]);
  const numbers = useMemo(() => sourceNumbers(sources), [sources]);
  const byId = useMemo(() => new Map(sources.map((s) => [s.news_item_id, s])), [sources]);

  return (
    <div className="space-y-6">
      <div>
        <p className="text-sm text-text-muted">
          <Link href={body.kind === "weekly" ? "/reports?period=weekly" : "/reports"} className="hover:text-text hover:underline">
            Reports
          </Link>{" "}
          / {body.kind} digest
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">{report.title}</h1>
        <p className="num mt-1 text-sm text-text-muted">
          Covers {formatWindow(body.window_start)} to {formatWindow(body.window_end)} · {body.alerts_sent} alert
          {body.alerts_sent === 1 ? "" : "s"} sent
          {body.writer ? ` · summary by ${body.writer}` : ""}
          {body.dropped_sentences ? ` · ${body.dropped_sentences} uncited sentence${body.dropped_sentences === 1 ? "" : "s"} removed` : ""}
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <article className="space-y-6">
          <section
            aria-label="Summary"
            className="space-y-2 rounded-lg border border-border bg-surface-raised p-4"
          >
            <p className="flex items-start gap-2 font-medium">
              {body.nothing_material ? (
                <CheckCircle2Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-neutral-signal" />
              ) : null}
              {body.headline}
            </p>
            {(body.overview ?? []).map((s, i) => (
              <p key={i} className="text-sm">
                {s.text}
                <Citations sentence={s} sources={sources} numbers={numbers} />
              </p>
            ))}
          </section>

          {(body.sections ?? []).map((section) => (
            <section key={section.ticker ?? "macro"} aria-labelledby={`sec-${section.ticker ?? "macro"}`} className="space-y-3">
              <h2 id={`sec-${section.ticker ?? "macro"}`} className="text-lg font-semibold">
                {section.ticker ? (
                  <Link href={`/holdings/${encodeURIComponent(section.ticker)}`} className="hover:underline">
                    {section.name}
                  </Link>
                ) : (
                  section.name
                )}
                {section.ticker ? <span className="ml-2 font-mono text-sm font-normal text-text-muted">{section.ticker}</span> : null}
              </h2>
              {(section.summary ?? []).map((s, i) => (
                <p key={i} className="max-w-prose text-sm">
                  {s.text}
                  <Citations sentence={s} sources={sources} numbers={numbers} />
                </p>
              ))}
              {(section.stories ?? []).length > 0 ? (
                <ul className="space-y-2">
                  {(section.stories ?? []).map((story) => {
                    const lead = byId.get(story.news_item_ids[0]);
                    const extra = story.news_item_ids.length - 1;
                    return (
                      <li key={story.cluster_id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-border p-3 text-sm">
                        <div className="min-w-0 flex-1">
                          {lead ? (
                            <a href={lead.url} target="_blank" rel="noreferrer" className="font-medium hover:text-accent hover:underline">
                              {story.headline}
                            </a>
                          ) : (
                            <span className="font-medium">{story.headline}</span>
                          )}
                          <p className="text-text-muted">
                            {eventLabel(story.event_type)} · {lead?.outlet ?? "source"}
                            {extra > 0 ? ` +${extra} more` : ""} ·{" "}
                            <Link href={`/news?story=${story.cluster_id}`} className="text-accent hover:underline">
                              all sources
                            </Link>
                          </p>
                        </div>
                        <div className="flex items-center gap-1.5">
                          <SeverityBadge severity={story.severity} />
                          <ConfidenceBadge confidence={story.confidence} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
              {(section.verdicts ?? []).length > 0 ? (
                <ul className="space-y-1 text-sm">
                  {(section.verdicts ?? []).map((v) => (
                    <li key={v.verdict_id} className="flex flex-wrap items-center gap-2">
                      <VerdictStatusBadge status={v.status} />
                      <span>{v.text}</span>
                      {v.fact_ids.map((f) => (
                        <Link key={f} href={`/evidence/${f}`} className="text-xs text-accent hover:underline">
                          fact {f}
                        </Link>
                      ))}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ))}
        </article>

        <aside aria-label="Sources" className="space-y-2">
          <h2 className="text-sm font-medium text-text-muted">Sources ({sources.length})</h2>
          {sources.length === 0 ? <p className="text-sm text-text-muted">No news items in this period.</p> : null}
          <ol className="space-y-2">
            {sources.map((s, i) => (
              <li
                key={s.news_item_id}
                id={sourceAnchor(i + 1)}
                className="scroll-mt-24 rounded-md border border-border bg-surface-raised p-3 text-sm"
              >
                <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
                  <span className="num">[{i + 1}] {s.outlet ?? "source"}</span>
                  <span title={TIERS[s.tier as 1 | 2 | 3]?.description}>
                    <TierBadge tier={s.tier} />
                  </span>
                </p>
                <a href={s.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-start gap-1 hover:text-accent hover:underline">
                  {s.headline}
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
