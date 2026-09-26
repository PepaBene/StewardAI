"use client";

import {
  CalendarOffIcon,
  CheckCircle2Icon,
  ClipboardCheckIcon,
  ExternalLinkIcon,
  LandmarkIcon,
  LoaderCircleIcon,
  ScaleIcon,
  TriangleAlertIcon,
  UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { CitationLink } from "@/components/news/citation";
import { checkedTextClass, ClaimsSummaryLine, SentenceCheckMark } from "@/components/verify/sentence-check";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import {
  getBrowserApiClient,
  type DigestBody,
  type DigestFactSource,
  type DigestSentence,
  type DigestSource,
  type ReportOut,
} from "@/lib/api/client";
import { humanizeMetric } from "@/lib/evidence/confidence";
import { digestOverviewPath, digestSectionPath } from "@/lib/feedback/feedback";
import { formatDate, formatDateTime, formatTime } from "@/lib/format";
import { digestFreshnessKind } from "@/lib/freshness";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { isExternalUrl } from "@/lib/sources/sources";
import {
  citationLinks,
  digestCitationPreview,
  factPreview,
  formatMacroValue,
  hostOf,
  groupHolidays,
  hasStatusSections,
  PORTFOLIO_FLAG_LABELS,
  sortFlags,
  sortMacro,
  sourceAnchor,
  sourceNumbers,
  thesisChangeText,
} from "@/lib/news/digest";
import { eventLabel, TIERS } from "@/lib/news/labels";
import { edgarFilingUrl, isNewPosition, managerChangeText, OWNERSHIP_NOTE, sortManagerChanges } from "@/lib/reference/ownership";

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
  body,
  numbers,
}: {
  sentence: DigestSentence;
  body: DigestBody;
  numbers: Map<number, number>;
}) {
  const links = citationLinks(sentence, body.sources ?? [], numbers);
  return (
    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
      {links.map((link) => (
        <CitationLink
          key={link.key}
          href={link.href}
          label={link.label}
          title={link.title}
          external={link.external}
          preview={digestCitationPreview(body, link.ref)}
        />
      ))}
    </span>
  );
}

/** A figure cited by a verdict or thesis change: the same preview as a sentence citation. */
function FactCitation({ id, facts }: { id: number; facts: Map<number, DigestFactSource> }) {
  const fact = facts.get(id);
  return (
    <CitationLink
      href={`/evidence/${id}`}
      label={`fact ${id}`}
      title={`Extracted figure #${id}: open its source snippet and archived page`}
      external={false}
      preview={fact ? factPreview(fact) : null}
      className="border-transparent px-0.5"
    />
  );
}

/** One written sentence: its text, citations, the claim verifier's result and feedback. */
function SentenceLine({
  sentence,
  report,
  path,
  numbers,
  className,
}: {
  sentence: DigestSentence;
  report: ReportOut;
  path: string;
  numbers: Map<number, number>;
  className?: string;
}) {
  return (
    <p className={className}>
      <span className={checkedTextClass(sentence)}>{sentence.text}</span>
      <Citations sentence={sentence} body={report.body} numbers={numbers} />
      <SentenceCheckMark sentence={sentence} />
      <FeedbackControl
        className="ml-1"
        target={{ kind: "report_sentence", id: report.id, sentencePath: path }}
        label={`sentence “${sentence.text.slice(0, 60)}${sentence.text.length > 60 ? "…" : ""}”`}
      />
    </p>
  );
}

/** Trust scores the owner's feedback has moved, by host (unlisted hosts are at 1). */
function useTrust(): Map<string, { score: number; demoted: boolean }> {
  const [trust, setTrust] = useState<Map<string, { score: number; demoted: boolean }>>(new Map());
  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/sources/trust")
      .then(({ data }) => {
        if (!cancelled && data) setTrust(new Map(data.map((t) => [t.host, { score: t.score, demoted: t.demoted }])));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);
  return trust;
}

function Digest({ report }: { report: ReportOut }) {
  const body = report.body;
  const sources = useMemo(() => body.sources ?? [], [body.sources]);
  const numbers = useMemo(() => sourceNumbers(sources), [sources]);
  const byId = useMemo(() => new Map(sources.map((s) => [s.news_item_id, s])), [sources]);
  const factSources = useMemo(() => body.fact_sources ?? [], [body.fact_sources]);
  const facts = useMemo(() => new Map(factSources.map((f) => [f.fact_id, f])), [factSources]);
  const trust = useTrust();

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
        <AsOfLabel
          className="mt-1"
          at={body.window_end}
          kind={digestFreshnessKind(body.kind)}
          nextDueAt={report.next_due_at}
          prefix="News as of"
        />
        <p className="num mt-1 text-sm text-text-muted">
          Covers {formatWindow(body.window_start)} to {formatWindow(body.window_end)} · {body.alerts_sent} alert
          {body.alerts_sent === 1 ? "" : "s"} sent
          {body.writer ? ` · summary by ${body.writer}` : ""}
          {body.dropped_sentences ? ` · ${body.dropped_sentences} uncited sentence${body.dropped_sentences === 1 ? "" : "s"} removed` : ""}
          {" · "}written <time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time>
        </p>
        <ClaimsSummaryLine className="mt-1" claims={body.claims} />
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
              <SentenceLine key={i} sentence={s} report={report} path={digestOverviewPath(i)} numbers={numbers} className="text-sm" />
            ))}
          </section>

          {(body.sections ?? []).map((section, sectionIndex) => (
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
                <SentenceLine
                  key={i}
                  sentence={s}
                  report={report}
                  path={digestSectionPath(sectionIndex, i)}
                  numbers={numbers}
                  className="max-w-prose text-sm"
                />
              ))}
              {(section.stories ?? []).length > 0 ? (
                <ul className="space-y-2">
                  {(section.stories ?? []).map((story) => {
                    const lead = byId.get(story.news_item_ids[0]);
                    const extra = story.news_item_ids.length - 1;
                    return (
                      <li key={story.cluster_id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border border-border p-3 text-sm">
                        <div className="min-w-0 flex-1">
                          {lead && isExternalUrl(lead.url) ? (
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
                        <div className="flex flex-wrap items-center justify-end gap-1.5">
                          <SeverityBadge severity={story.severity} />
                          <ConfidenceBadge confidence={story.confidence} />
                          <FeedbackControl target={{ kind: "story", id: story.cluster_id }} label={`story “${story.headline}”`} />
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
                        <FactCitation key={f} id={f} facts={facts} />
                      ))}
                      <FeedbackControl target={{ kind: "verdict", id: v.verdict_id }} label={`verdict on KPI ${v.kpi_id}`} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ))}

          {hasStatusSections(body) ? <StatusSections body={body} facts={facts} /> : null}
        </article>

        <aside aria-label="Sources" className="space-y-4">
          <div className="space-y-2">
            <h2 className="text-sm font-medium text-text-muted">Sources ({sources.length})</h2>
            {sources.length === 0 ? <p className="text-sm text-text-muted">No news items in this period.</p> : null}
            <ol className="space-y-2">
              {sources.map((s, i) => (
                <SourceCard key={s.news_item_id} source={s} number={i + 1} trust={trust.get(hostOf(s.url))} />
              ))}
            </ol>
          </div>
          {factSources.length > 0 ? (
            <div className="space-y-2">
              <h2 className="text-sm font-medium text-text-muted">Cited figures ({factSources.length})</h2>
              <ul className="space-y-2">
                {factSources.map((f) => (
                  <li key={f.fact_id} className="rounded-md border border-border bg-surface-raised p-3 text-sm">
                    <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
                      <span className="font-mono">{f.ticker}</span>
                      <span>status: {f.status}</span>
                    </p>
                    <p className="mt-1 font-medium">
                      {humanizeMetric(f.metric)} {f.period} <span className="num font-mono">{f.value_as_written}</span>
                    </p>
                    <blockquote className="mt-1 border-l-2 border-border pl-2 text-xs break-words text-text-muted">{f.snippet}</blockquote>
                    <p className="mt-1 flex flex-wrap gap-3 text-xs">
                      <Link href={`/evidence/${f.fact_id}`} className="text-accent hover:underline">
                        Evidence (figure #{f.fact_id})
                      </Link>
                      {isExternalUrl(f.source_url) ? (
                        <a href={f.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                          {hostOf(f.source_url)}
                          <ExternalLinkIcon aria-hidden="true" className="size-3" />
                        </a>
                      ) : (
                        <span className="break-all text-text-muted">{f.source_url}</span>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function SourceCard({
  source: s,
  number,
  trust,
}: {
  source: DigestSource;
  number: number;
  trust: { score: number; demoted: boolean } | undefined;
}) {
  return (
    <li id={sourceAnchor(number)} className="scroll-mt-24 space-y-1 rounded-md border border-border bg-surface-raised p-3 text-sm">
      <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
        <span className="num">
          [{number}] {s.outlet ?? hostOf(s.url)}
        </span>
        <span title={TIERS[s.tier as 1 | 2 | 3]?.description}>
          <TierBadge tier={s.tier} />
        </span>
      </p>
      {isExternalUrl(s.url) ? (
        <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent hover:underline">
          {s.headline}
          <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
        </a>
      ) : (
        <p>{s.headline}</p>
      )}
      <p className="num text-xs text-text-muted">
        {formatDateTime(s.published_at)} · trust {trust ? Math.round(trust.score * 100) / 100 : 1}
        {trust?.demoted ? <span className="text-text"> · demoted one tier (low trust)</span> : null}
      </p>
      {s.summary ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-muted">Stored text</summary>
          <p className="mt-1 break-words whitespace-pre-line text-text-muted">{s.summary}</p>
        </details>
      ) : null}
    </li>
  );
}

const SECTION = "space-y-2 rounded-lg border border-border bg-surface-raised p-4";
const LINK = "text-accent underline-offset-4 hover:underline";

/** Portfolio flags, thesis status changes, reviews due, macro figures, tracked managers (13F) and market holidays. */
function StatusSections({ body, facts }: { body: DigestBody; facts: Map<number, DigestFactSource> }) {
  const flags = sortFlags(body.portfolio ?? []);
  const changes = body.thesis_changes ?? [];
  const reviews = body.reviews_due ?? [];
  const macro = sortMacro(body.macro ?? []);
  const holidays = groupHolidays(body.holidays ?? []);
  const managers = sortManagerChanges(body.ownership_changes ?? []);
  return (
    <div className="space-y-4">
      {flags.length > 0 ? (
        <section aria-labelledby="digest-portfolio" className={SECTION}>
          <h2 id="digest-portfolio" className="flex items-center gap-1.5 text-lg font-semibold">
            <TriangleAlertIcon aria-hidden="true" className="size-4 text-warning-signal" />
            Portfolio flags
          </h2>
          <p className="text-xs text-text-muted">Out-of-band weights and hard-limit or rule breaches open during the period.</p>
          <ul className="space-y-1.5 text-sm">
            {flags.map((f) => (
              <li key={`${f.kind}:${f.key}`} className="flex flex-wrap items-center gap-2">
                <SeverityBadge severity={f.severity} />
                <span className="font-medium">{PORTFOLIO_FLAG_LABELS[f.kind] ?? f.kind}</span>
                {f.ticker ? (
                  <Link href={`/holdings/${encodeURIComponent(f.ticker)}`} className="font-mono text-xs text-accent hover:underline">
                    {f.ticker}
                  </Link>
                ) : null}
                <span>{f.detail}</span>
                <span className="num text-xs text-text-muted">
                  {f.cleared_at ? `cleared ${formatDateTime(f.cleared_at)}` : `open since ${formatDateTime(f.first_seen)}`}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs">
            <Link href="/config/strategy" className={LINK}>
              Bands and rules in Config: Strategy
            </Link>
          </p>
        </section>
      ) : null}

      {changes.length > 0 ? (
        <section aria-labelledby="digest-thesis" className={SECTION}>
          <h2 id="digest-thesis" className="flex items-center gap-1.5 text-lg font-semibold">
            <ScaleIcon aria-hidden="true" className="size-4 text-text-muted" />
            Thesis status changes
          </h2>
          <ul className="space-y-1.5 text-sm">
            {changes.map((c) => (
              <li key={c.verdict_id} className="flex flex-wrap items-center gap-2">
                <VerdictStatusBadge status={c.to_status} />
                <Link href={`/holdings/${encodeURIComponent(c.ticker)}`} className="font-mono text-xs text-accent hover:underline">
                  {c.ticker}
                </Link>
                <span>
                  KPI <span className="font-mono">{c.kpi_id}</span>
                  {c.period ? ` (${c.period})` : ""}: {thesisChangeText(c)}
                </span>
                {c.fact_ids.map((f) => (
                  <FactCitation key={f} id={f} facts={facts} />
                ))}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {reviews.length > 0 ? (
        <section aria-labelledby="digest-reviews" className={SECTION}>
          <h2 id="digest-reviews" className="flex items-center gap-1.5 text-lg font-semibold">
            <ClipboardCheckIcon aria-hidden="true" className="size-4 text-text-muted" />
            Theses due for review
          </h2>
          <ul className="space-y-1 text-sm">
            {reviews.map((r) => (
              <li key={r.ticker}>
                <Link href={`/config/theses#${encodeURIComponent(r.ticker)}`} className={LINK}>
                  {r.ticker}
                </Link>{" "}
                <span className="num text-text-muted">review date {formatDate(r.review_date)}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {macro.length > 0 ? (
        <section aria-labelledby="digest-macro" className={SECTION}>
          <h2 id="digest-macro" className="flex items-center gap-1.5 text-lg font-semibold">
            <LandmarkIcon aria-hidden="true" className="size-4 text-text-muted" />
            Macro figures
          </h2>
          <p className="text-xs text-text-muted">The latest value of each collected series, with its period, release date and source.</p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[32rem] text-sm">
              <thead>
                <tr className="border-b border-border text-text-muted">
                  <th scope="col" className="py-1.5 pr-3 text-left font-medium">Series</th>
                  <th scope="col" className="py-1.5 pr-3 text-right font-medium">Value</th>
                  <th scope="col" className="py-1.5 pr-3 text-left font-medium">Period</th>
                  <th scope="col" className="py-1.5 text-left font-medium">Released</th>
                </tr>
              </thead>
              <tbody>
                {macro.map((m) => (
                  <tr key={m.series_id} className="border-b border-border last:border-0">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                      {isExternalUrl(m.url) ? (
                        <a href={m.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent hover:underline">
                          {m.label}
                          <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
                        </a>
                      ) : (
                        m.label
                      )}
                      {m.new ? (
                        <span className="ml-1.5 rounded-sm border border-info-signal/40 bg-info-signal-subtle px-1 text-xs">new</span>
                      ) : null}
                    </th>
                    <td className="num py-1.5 pr-3 text-right">{formatMacroValue(m.value, m.unit)}</td>
                    <td className="num py-1.5 pr-3">{formatDate(m.period)}</td>
                    <td className="num py-1.5">{m.release_date ? formatDate(m.release_date) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {managers.length > 0 ? (
        <section aria-labelledby="digest-managers" className={SECTION}>
          <h2 id="digest-managers" className="flex items-center gap-1.5 text-lg font-semibold">
            <UsersIcon aria-hidden="true" className="size-4 text-text-muted" />
            Tracked managers (13F)
          </h2>
          <p className="text-xs text-text-muted">
            New and exited positions of the managers you track, in your holdings, from 13F filings received in the period.{" "}
            {OWNERSHIP_NOTE}
          </p>
          <ul className="space-y-1.5 text-sm">
            {managers.map((c) => {
              const filing = edgarFilingUrl(c.cik, c.accession);
              return (
                <li key={`${c.cik}:${c.ticker}:${c.change}:${c.period}`} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className="rounded-sm border border-border px-1.5 py-0.5 text-xs font-medium">
                    {isNewPosition(c) ? "New position" : c.change === "exited" ? "Exited" : c.change}
                  </span>
                  <span className="font-medium">{c.manager_name ?? `CIK ${c.cik}`}</span>
                  <Link href={`/holdings/${encodeURIComponent(c.ticker)}#ownership-heading`} className="font-mono text-xs text-accent hover:underline">
                    {c.ticker}
                  </Link>
                  <span>{managerChangeText(c)}</span>
                  <span className="num text-xs text-text-muted">
                    quarter ended {formatDate(c.period)} (against {formatDate(c.previous_period)})
                  </span>
                  {filing ? (
                    <a href={filing} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                      filing {c.accession}
                      <ExternalLinkIcon aria-hidden="true" className="size-3" />
                    </a>
                  ) : (
                    <span className="font-mono text-xs text-text-muted">{c.accession}</span>
                  )}
                </li>
              );
            })}
          </ul>
          <p className="text-xs">
            <Link href="/config/jobs#ownership-heading" className={LINK}>
              Tracked managers in Config: Jobs
            </Link>
          </p>
        </section>
      ) : null}

      {holidays.length > 0 ? (
        <section aria-labelledby="digest-holidays" className={SECTION}>
          <h2 id="digest-holidays" className="flex items-center gap-1.5 text-lg font-semibold">
            <CalendarOffIcon aria-hidden="true" className="size-4 text-text-muted" />
            Market holidays
          </h2>
          <p className="text-xs text-text-muted">Public holidays and confirmed exchange closures in the period and the next 7 days.</p>
          <ul className="space-y-1 text-sm">
            {holidays.map((g) => (
              <li key={g.date} className="flex flex-wrap gap-x-2">
                <span className="num font-medium">{formatDate(g.date)}</span>
                <span>{g.entries.map((h) => `${h.name} (${h.calendar})`).join(" · ")}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs">
            <Link href="/config/calendar" className={LINK}>
              Exchange calendar
            </Link>
          </p>
        </section>
      ) : null}
    </div>
  );
}
