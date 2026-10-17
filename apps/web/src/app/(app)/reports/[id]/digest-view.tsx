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
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { AskAboutSelection } from "@/components/chat/ask-about-selection";
import { Ring, Sparkline } from "@/components/charts";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { CitationLink } from "@/components/news/citation";
import { SeverityBadge, TierBadge } from "@/components/news/severity-badge";
import { DigestIntradaySpark, ON_ACCENT } from "@/components/reports/digest-intraday";
import { DigestShell } from "@/components/reports/digest-shell";
import { usePortfolioHistory } from "@/components/reports/reports-shell";
import { Blueprint, BlueprintCorners, PanelHeading } from "@/components/ui/blueprint";
import { checkedTextClass, ClaimsSummaryLine, SentenceCheckMark } from "@/components/verify/sentence-check";
import { useTrackOpen } from "@/lib/use-track-open";
import {
  getBrowserApiClient,
  type AlertOut,
  type ContributionReport,
  type DigestBody,
  type DigestFactSource,
  type DigestSentence,
  type DigestSource,
  type PortfolioHistory,
  type ReportOut,
} from "@/lib/api/client";
import { formatPct, formatSigned } from "@/lib/charts/format";
import { humanizeMetric } from "@/lib/evidence/confidence";
import { formatDate, formatDateTime, formatTime } from "@/lib/format";
import { digestFreshnessKind } from "@/lib/freshness";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  citationLinks,
  digestCitationPreview,
  factPreview,
  formatMacroValue,
  groupHolidays,
  hasStatusSections,
  hostOf,
  PORTFOLIO_FLAG_LABELS,
  sortFlags,
  sortMacro,
  sourceAnchor,
  sourceNumbers,
  thesisChangeText,
} from "@/lib/news/digest";
import { eventLabel, TIERS } from "@/lib/news/labels";
import { shortDay } from "@/lib/news/stats";
import { EntityName } from "@/components/entity-name";
import { SourcesByDomain } from "@/components/research/sources-by-domain";
import { TocRail, type TocItem } from "@/components/research/toc-rail";
import { edgarFilingUrl, isNewPosition, managerChangeText, OWNERSHIP_NOTE, sortManagerChanges } from "@/lib/reference/ownership";
import {
  digestChecks,
  digestDays,
  historyUpTo,
  telegramTimes,
  whatHappened,
  windowReturn,
  type Ratio,
  type WindowReturn,
} from "@/lib/reports/digest-overview";
import { moversSentence, splitMovers, type Mover } from "@/lib/verdicts/movers";
import { shortTicker } from "@/lib/portfolio/ticker";
import { isExternalUrl } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";
import { decodeEntities } from "@/lib/news/entities";
import { indexName } from "@/lib/portfolio/ticker";

/** /reports/[id]: the digest list of its period beside the digest. */
export function DigestView({ id }: { id: number }) {
  useTrackOpen("report", id);
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
  return (
    <DigestShell period={report.body.kind === "weekly" ? "weekly" : "daily"} selectedId={report.id}>
      {() => <Digest report={report} />}
    </DigestShell>
  );
}

/** One digest by id, without the page frame (the /reports landing shows the latest). */
export function DigestDetail({ id }: { id: number }) {
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
  if (state.kind === "error" && !state.previous) return <AlertMessage title={state.title}>{state.detail}</AlertMessage>;
  return <Digest report={state.kind === "ok" ? state.data : state.previous!} />;
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

/** A GET whose answer the digest can live without (null when it fails). */
function useOptional<T>(fetcher: () => Promise<{ data?: T }>): T | null | undefined {
  const [value, setValue] = useState<T | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    fetcher()
      .then(({ data }) => !cancelled && setValue(data ?? null))
      .catch(() => !cancelled && setValue(null));
    return () => {
      cancelled = true;
    };
  }, [fetcher]);
  return value;
}

const fetchTrust = () => getBrowserApiClient().GET("/sources/trust");
const fetchHoldings = () => getBrowserApiClient().GET("/portfolio/holdings");
const fetchAlerts = () => getBrowserApiClient().GET("/alerts", { params: { query: { limit: 100 } } });

/**
 * Each holding's contribution over the digest's own window (the closes the hero's portfolio
 * move spans), not the latest session. Undefined while loading, null when there is none.
 */
function useWindowMovers(window: WindowReturn | null | undefined): ContributionReport | null | undefined {
  const [value, setValue] = useState<ContributionReport | null | undefined>(undefined);
  const key = window === undefined ? "" : window ? `${window.from}/${window.to}` : "none";
  useEffect(() => {
    let cancelled = false;
    if (key === "" || key === "none") return;
    const [start, end] = key.split("/");
    getBrowserApiClient()
      .GET("/portfolio/contribution", { params: { query: { start, end } } })
      .then(({ data }) => !cancelled && setValue(data ?? null))
      .catch(() => !cancelled && setValue(null));
    return () => {
      cancelled = true;
    };
  }, [key]);
  return key === "none" ? null : value;
}

/** Trust scores the owner's feedback has moved, by host (unlisted hosts are at 1). */
function useTrust(): Map<string, { score: number; demoted: boolean }> {
  const rows = useOptional(fetchTrust);
  return useMemo(() => new Map((rows ?? []).map((t) => [t.host, { score: t.score, demoted: t.demoted }])), [rows]);
}

function Digest({ report }: { report: ReportOut }) {
  const body = report.body;
  const sources = useMemo(() => body.sources ?? [], [body.sources]);
  const numbers = useMemo(() => sourceNumbers(sources), [sources]);
  const byId = useMemo(() => new Map(sources.map((s) => [s.news_item_id, s])), [sources]);
  const factSources = useMemo(() => body.fact_sources ?? [], [body.fact_sources]);
  const facts = useMemo(() => new Map(factSources.map((f) => [f.fact_id, f])), [factSources]);
  const trust = useTrust();
  const holdings = useOptional(fetchHoldings);
  const alerts = useOptional(fetchAlerts);
  const history = usePortfolioHistory();
  const days = digestDays(body);
  const own = history ? windowReturn(history.points, days.start, days.end) : history === null ? null : undefined;
  const bench = history ? windowReturn(history.benchmark, days.start, days.end) : null;
  const movers = useWindowMovers(own);
  const moverRows = useMemo(
    () =>
      (movers?.holdings ?? [])
        .filter((h) => h.contribution_pct !== null)
        .map((h) => ({ ticker: h.ticker, name: h.name || shortTicker(h.ticker), pct: h.contribution_pct ?? 0 })),
    [movers],
  );
  const benchName = history?.benchmark_symbol ? indexName(history.benchmark_symbol) : "benchmark";
  const moved = own && movers && !movers.gap ? moversSentence(own.pct, bench ? { pct: bench.pct, name: benchName } : null, moverRows) : null;
  const happened = useMemo(() => whatHappened(body), [body]);
  const checks = digestChecks(body, holdings ? holdings.holdings.map((h) => h.ticker) : null);

  // Laid out by its own width (container queries), not the viewport's: the digest also sits in
  // the reports page's right-hand pane, where viewport breakpoints squeezed it into slivers.
  return (
    <div className="@container flex flex-col gap-5">
      <Hero report={report} alerts={alerts ?? null} history={history} own={own ?? null} bench={bench} moved={moved} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
        <AsOfLabel at={body.window_end} kind={digestFreshnessKind(body.kind)} nextDueAt={report.next_due_at} prefix="News as of" />
        <span className="num">
          Covers {formatWindow(body.window_start)} to {formatWindow(body.window_end)}
          {body.writer ? ` · summary by ${body.writer}` : ""}
          {body.dropped_sentences ? ` · ${body.dropped_sentences} uncited sentence${body.dropped_sentences === 1 ? "" : "s"} removed` : ""}
          {" · "}written <time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time>
        </span>
      </div>

      <AskAboutSelection>
        <div className="grid grid-cols-1 gap-5 @4xl:grid-cols-2">
          <Movers movers={movers} rows={moverRows} />
          <Blueprint aria-labelledby="happened-heading" className="flex flex-col gap-3 px-6 py-5">
            <PanelHeading
              kicker="What happened"
              title={
                <span id="happened-heading" className="num">
                  {body.nothing_material
                    ? "Nothing material"
                    : `${happened.length} ${happened.length === 1 ? "sentence" : "sentences"}, ${sources.length + factSources.length} cited ${
                        sources.length + factSources.length === 1 ? "source" : "sources"
                      }`}
                </span>
              }
            />
            <ClaimsSummaryLine claims={body.claims} />
            {body.nothing_material ? (
              <p className="flex items-start gap-2 text-sm text-text-muted">
                <CheckCircle2Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-neutral-signal" />
                Nothing material happened to your holdings in this window.
              </p>
            ) : null}
            {happened.length > 0 ? (
              <ol className="m-0 list-none divide-y divide-border-soft border-t border-border-soft p-0">
                {happened.map((item) => (
                  <li key={item.path} className="grid grid-cols-[10px_minmax(0,1fr)] gap-x-3 py-3">
                    <span
                      aria-hidden="true"
                      className={cn(
                        "mt-1.5 size-2",
                        item.severity === 3 ? "bg-warning-signal" : item.severity === 2 ? "bg-accent" : "bg-neutral-signal",
                      )}
                    />
                    <div className="min-w-0">
                      <SentenceLine
                        sentence={item.sentence}
                        report={report}
                        path={item.path}
                        numbers={numbers}
                        className="font-serif text-[15px] leading-relaxed"
                      />
                      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
                        {item.tickers.map((t) => (
                          <Link key={t} href={`/companies/${encodeURIComponent(t)}`} className="font-mono hover:text-accent-ink hover:underline">
                            {t}
                          </Link>
                        ))}
                        <span>{item.context}</span>
                        {item.severity !== null ? <span>· highest severity {item.severity}</span> : null}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            ) : null}
          </Blueprint>
        </div>
      </AskAboutSelection>

      <Blueprint aria-label="Checks" className="grid grid-cols-1 gap-5 px-6 py-5 @xl:grid-cols-3">
        <CheckRing
          label="Claims supported"
          ratio={checks.claims}
          caption={() => "sentences passed the claim check"}
          empty="The claim verifier did not run on this digest."
        />
        <CheckRing label="Tier-1 sources" ratio={checks.tier1} percent caption={(r) => `${r.value} of ${r.total} cited items from tier-1 sources`} empty="No news items cited." />
        <CheckRing
          label="Holdings covered"
          ratio={checks.holdings}
          caption={() => "holdings with their own section in this digest"}
          empty="Holdings unavailable."
        />
      </Blueprint>

      <div className="grid grid-cols-1 gap-5 @4xl:grid-cols-[minmax(0,1fr)_18rem] @6xl:grid-cols-[9rem_minmax(0,1fr)_18rem]">
        <TocRail items={tocItems(body)} className="lg:hidden @6xl:block" />
        <AskAboutSelection>
          <article className="flex flex-col gap-5" aria-label="Stories and status">
            {(body.sections ?? []).some((s) => (s.stories ?? []).length > 0 || (s.verdicts ?? []).length > 0) ? (
              <Blueprint aria-labelledby="digest-stories" className="flex flex-col gap-4 px-6 py-5">
                <PanelHeading kicker="By holding" title={<span id="digest-stories">Stories and verdicts</span>} />
                {(body.sections ?? []).map((section) =>
                  (section.stories ?? []).length === 0 && (section.verdicts ?? []).length === 0 ? null : (
                    <section key={section.ticker ?? "macro"} aria-labelledby={`sec-${section.ticker ?? "macro"}`} className="space-y-2">
                      <h3 id={`sec-${section.ticker ?? "macro"}`} className="scroll-mt-24 font-heading text-lg leading-tight font-semibold">
                        {section.ticker ? (
                          <Link href={`/companies/${encodeURIComponent(section.ticker)}`} className="hover:underline">
                            <EntityName id={section.ticker} name={section.name !== section.ticker ? section.name : null} />
                          </Link>
                        ) : (
                          section.name
                        )}
                      </h3>
                      {(section.stories ?? []).length > 0 ? (
                        <ul className="space-y-2">
                          {(section.stories ?? []).map((story) => {
                            const lead = byId.get(story.news_item_ids[0]);
                            const extra = story.news_item_ids.length - 1;
                            return (
                              <li key={story.cluster_id} className="space-y-1.5 border border-border-soft p-3 text-sm">
                                {lead && isExternalUrl(lead.url) ? (
                                  <a href={lead.url} target="_blank" rel="noreferrer" className="block font-medium hover:text-accent-ink hover:underline">
                                    {decodeEntities(story.headline)}
                                  </a>
                                ) : (
                                  <span className="block font-medium">{decodeEntities(story.headline)}</span>
                                )}
                                <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-text-muted">
                                  <SeverityBadge severity={story.severity} />
                                  <ConfidenceBadge confidence={story.confidence} />
                                  <span>
                                    {eventLabel(story.event_type)} · {lead?.outlet ?? "source"}
                                    {extra > 0 ? ` +${extra} more` : ""} ·{" "}
                                    <Link href={`/news?story=${story.cluster_id}`} className="text-accent-ink hover:underline">
                                      all sources
                                    </Link>
                                  </span>
                                  <FeedbackControl target={{ kind: "story", id: story.cluster_id }} label={`story “${decodeEntities(story.headline)}”`} />
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
                  ),
                )}
              </Blueprint>
            ) : null}

            {hasStatusSections(body) ? <StatusSections body={body} facts={facts} /> : null}
          </article>
        </AskAboutSelection>

        <aside aria-label="Sources" className="space-y-4">
          <div className="space-y-2">
            <h2 id="digest-sources" className="scroll-mt-24 font-heading text-lg font-semibold">
              Sources ({sources.length})
            </h2>
            {sources.length === 0 ? <p className="text-sm text-text-muted">No news items in this period.</p> : null}
            <SourcesByDomain
              items={sources}
              urlOf={(s) => s.url}
              renderItem={(s, i) => <SourceCard source={s} number={i + 1} trust={trust.get(hostOf(s.url))} />}
            />
          </div>
          {factSources.length > 0 ? (
            <div className="space-y-2">
              <h2 className="font-heading text-lg font-semibold">Cited figures ({factSources.length})</h2>
              <ul className="space-y-2">
                {factSources.map((f) => (
                  <li key={f.fact_id} className="border border-border p-3 text-sm">
                    <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
                      <span className="font-mono">{f.ticker}</span>
                      <span>status: {f.status}</span>
                    </p>
                    <p className="mt-1 font-medium">
                      {humanizeMetric(f.metric)} {f.period} <span className="num font-mono">{f.value_as_written}</span>
                    </p>
                    <blockquote className="mt-1 border-l-2 border-border pl-2 text-xs break-words text-text-muted">{f.snippet}</blockquote>
                    <p className="mt-1 flex flex-wrap gap-3 text-xs">
                      <Link href={`/evidence/${f.fact_id}`} className="text-accent-ink hover:underline">
                        Evidence (figure #{f.fact_id})
                      </Link>
                      {isExternalUrl(f.source_url) ? (
                        <a href={f.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent-ink hover:underline">
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

/** The accent hero: headline, the portfolio's move over the digest window, vs benchmark, alerts. */
function Hero({
  report,
  alerts,
  history,
  own,
  bench,
  moved,
}: {
  report: ReportOut;
  alerts: AlertOut[] | null;
  history: PortfolioHistory | null | undefined;
  own: WindowReturn | null;
  bench: WindowReturn | null;
  moved: string | null;
}) {
  const body = report.body;
  const days = digestDays(body);
  const spark = history ? historyUpTo(history.points, days.end, 10) : [];
  const times = alerts ? telegramTimes(alerts, body.window_start, body.window_end) : [];
  const vs = own && bench ? (own.pct - bench.pct) * 100 : null;
  const label = body.kind === "weekly" ? "Weekly digest" : "Daily digest";

  return (
    <section aria-labelledby="digest-headline" className="blueprint border-accent bg-accent px-6 py-6 text-accent-foreground">
      <BlueprintCorners />
      <div className="grid grid-cols-1 gap-6 @xl:grid-cols-3 @xl:items-end @5xl:grid-cols-[minmax(0,1.5fr)_repeat(3,minmax(0,1fr))]">
        <div className="min-w-0 @xl:col-span-3 @5xl:col-span-1">
          <p className="text-xs font-semibold opacity-85">
            {label} · <span className="num">{shortDay(new Date(report.created_at))} {new Date(report.created_at).getFullYear()}</span>
          </p>
          <h2 id="digest-headline" className="mt-1.5 flex items-start gap-2 text-[32px] leading-[1.05] font-semibold">
            {body.nothing_material ? <CheckCircle2Icon aria-hidden="true" className="mt-1.5 size-6 shrink-0" /> : null}
            {decodeEntities(body.headline)}
          </h2>
          {moved ? <p className="mt-2 max-w-[62ch] text-sm leading-snug">{moved}</p> : null}
          <p className="mt-1 text-xs opacity-85">{report.title}</p>
        </div>
        <HeroStat
          label={own ? `Portfolio, ${shortDay(new Date(`${own.from}T12:00:00Z`))} → ${shortDay(new Date(`${own.to}T12:00:00Z`))}` : "Portfolio"}
          value={own ? formatPct(own.pct, 2, true) : "—"}
          sub={history === undefined ? "Loading…" : own ? "Flow-adjusted index, daily closes" : "No index points around this window"}
        >
          {spark.length > 1 ? (
            <div className="mt-2 w-32" style={ON_ACCENT}>
              <Sparkline values={spark} width={128} height={26} label="Portfolio index, 10 days to the digest" className="h-[26px]" />
            </div>
          ) : null}
          {body.kind !== "weekly" ? <DigestIntradaySpark until={body.window_end} /> : null}
        </HeroStat>
        <HeroStat
          label="vs benchmark"
          value={vs !== null ? `${formatSigned(vs, 2)} pp` : "—"}
          sub={bench && history?.benchmark_symbol ? `${indexName(history.benchmark_symbol)} ${formatPct(bench.pct, 2, true)}` : "No benchmark closes for this window"}
        />
        <HeroStat
          label="Alerts sent"
          value={String(body.alerts_sent)}
          sub={times.length > 0 ? `Telegram · ${times.map((t) => formatTime(new Date(t))).join(", ")}` : body.alerts_sent > 0 ? "In this window" : "None in this window"}
        />
      </div>
    </section>
  );
}

function HeroStat({ label, value, sub, children }: { label: string; value: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-xs opacity-85">{label}</p>
      <p className="num mt-1 font-heading text-[34px] leading-none font-semibold">{value}</p>
      {children}
      {sub ? <p className="mt-1.5 text-xs opacity-85">{sub}</p> : null}
    </div>
  );
}

/** Each holding's contribution over the digest's own days (`/portfolio/contribution?start&end`). */
function Movers({ movers, rows }: { movers: ContributionReport | null | undefined; rows: Mover[] }) {
  const { up, down, still } = splitMovers(rows, 5);
  const max = Math.max(1e-9, ...[...up, ...down].map((m) => Math.abs(m.pct)));
  return (
    <Blueprint aria-labelledby="movers-heading" className="flex flex-col gap-3 px-6 py-5">
      <PanelHeading kicker="Movers" title={<span id="movers-heading">What moved the portfolio</span>} />
      {movers === undefined ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {movers === null ? <p className="text-sm text-text-muted">Contribution is unavailable for this digest&rsquo;s days.</p> : null}
      {movers && movers.gap ? <p className="text-sm text-text-muted">{movers.gap}</p> : null}
      {movers && !movers.gap ? (
        <p className="num text-xs text-text-muted">
          {movers.period_start && movers.as_of ? `Closes ${formatDate(movers.period_start)} → ${formatDate(movers.as_of)}, ` : null}
          in percentage points of the portfolio.
        </p>
      ) : null}
      {movers && !movers.gap && up.length + down.length === 0 ? (
        <p className="text-sm text-text-muted">No holding moved the portfolio by 0.01 points or more.</p>
      ) : null}
      {up.length + down.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 @xl:grid-cols-2">
          <MoverList title="Pulled it down" movers={down} max={max} />
          <MoverList title="Lifted it" movers={up} max={max} />
        </div>
      ) : null}
      {movers && !movers.gap && still > 0 && up.length + down.length > 0 ? (
        <p className="text-xs text-text-muted">
          {still} other holding{still === 1 ? "" : "s"} moved it less than 0.01 points.
        </p>
      ) : null}
    </Blueprint>
  );
}

function MoverList({ title, movers, max }: { title: string; movers: Mover[]; max: number }) {
  return (
    <div className="min-w-0">
      <p className="text-xs font-semibold text-text-muted">{title}</p>
      {movers.length === 0 ? (
        <p className="mt-1.5 text-xs text-text-muted">No holding.</p>
      ) : (
        <ul className="m-0 mt-1.5 flex list-none flex-col gap-1.5 p-0" aria-label={title}>
          {movers.map((m) => (
            <li key={m.ticker} className="grid grid-cols-[minmax(0,1fr)_5rem_4.5rem] items-center gap-3 text-xs">
              <Link href={`/companies/${encodeURIComponent(m.ticker)}`} className="min-w-0 truncate hover:underline">
                <EntityName id={m.ticker} name={m.name} chip={false} />
              </Link>
              <span aria-hidden="true" className="relative h-2.5 bg-surface-muted">
                <span className={cn("absolute inset-y-0 left-0", m.pct > 0 ? "bg-gain" : "bg-loss")} style={{ width: `${(Math.abs(m.pct) / max) * 100}%` }} />
              </span>
              <span className={cn("num text-right", m.pct > 0 ? "text-gain" : "text-loss")}>{formatSigned(m.pct * 100, 2)} pp</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CheckRing({
  label,
  ratio,
  percent = false,
  caption,
  empty,
}: {
  label: string;
  ratio: Ratio | null;
  percent?: boolean;
  caption: (r: Ratio) => string;
  empty: string;
}) {
  const has = ratio !== null && ratio.total > 0;
  const text = !has ? "—" : percent ? `${Math.round((ratio.value / ratio.total) * 100)}%` : `${ratio.value} / ${ratio.total}`;
  return (
    <div className="flex items-center gap-4">
      <Ring value={has ? ratio.value : null} max={has ? ratio.total : 1} label="" size={50} thickness={5} ariaLabel={label} />
      <div className="min-w-0">
        <p className="num font-heading text-2xl leading-none font-semibold">{text}</p>
        <p className="mt-1 text-xs text-text-muted">{has ? caption(ratio) : empty}</p>
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
    <div id={sourceAnchor(number)} className="scroll-mt-24 space-y-1 rounded-lg border border-border p-3 text-sm">
      <p className="flex items-center justify-between gap-2 text-xs text-text-muted">
        <span className="num">
          [{number}] {s.outlet ?? hostOf(s.url)}
        </span>
        <span title={TIERS[s.tier as 1 | 2 | 3]?.description}>
          <TierBadge tier={s.tier} />
        </span>
      </p>
      {isExternalUrl(s.url) ? (
        <a href={s.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent-ink hover:underline">
          {decodeEntities(s.headline)}
          <ExternalLinkIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-text-muted" />
        </a>
      ) : (
        <p>{decodeEntities(s.headline)}</p>
      )}
      <p className="num text-xs text-text-muted">
        {formatDateTime(s.published_at)} · trust {trust ? Math.round(trust.score * 100) / 100 : 1}
        {trust?.demoted ? <span className="text-text"> · demoted one tier (low trust)</span> : null}
      </p>
      {s.summary ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-muted">Stored text</summary>
          <p className="mt-1 break-words whitespace-pre-line text-text-muted">{decodeEntities(s.summary)}</p>
        </details>
      ) : null}
    </div>
  );
}

/** The contents list: one entry per company section that has stories or verdicts, then the sources. */
function tocItems(body: DigestBody): TocItem[] {
  const items: TocItem[] = (body.sections ?? [])
    .filter((s) => (s.stories ?? []).length > 0 || (s.verdicts ?? []).length > 0)
    .map((s) => ({
      id: `sec-${s.ticker ?? "macro"}`,
      label: s.ticker ? <EntityName id={s.ticker} name={s.name !== s.ticker ? s.name : null} chip={false} /> : s.name,
    }));
  return [...items, { id: "digest-sources", label: "Sources" }];
}

const SECTION ="blueprint space-y-2 px-6 py-5";
const LINK = "text-accent-ink underline-offset-4 hover:underline";

/** Portfolio flags, thesis status changes, reviews due, macro figures, tracked managers (13F) and market holidays. */
function StatusSections({ body, facts }: { body: DigestBody; facts: Map<number, DigestFactSource> }) {
  const flags = sortFlags(body.portfolio ?? []);
  const changes = body.thesis_changes ?? [];
  const reviews = body.reviews_due ?? [];
  const macro = sortMacro(body.macro ?? []);
  const holidays = groupHolidays(body.holidays ?? []);
  const managers = sortManagerChanges(body.ownership_changes ?? []);
  return (
    <div className="flex flex-col gap-5">
      {flags.length > 0 ? (
        <section aria-labelledby="digest-portfolio" className={SECTION}>
          <BlueprintCorners />
          <h2 id="digest-portfolio" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
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
                  <Link href={`/companies/${encodeURIComponent(f.ticker)}`} className="font-mono text-xs text-accent-ink hover:underline">
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
          <BlueprintCorners />
          <h2 id="digest-thesis" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
            <ScaleIcon aria-hidden="true" className="size-4 text-text-muted" />
            Thesis status changes
          </h2>
          <ul className="space-y-1.5 text-sm">
            {changes.map((c) => (
              <li key={c.verdict_id} className="flex flex-wrap items-center gap-2">
                <VerdictStatusBadge status={c.to_status} />
                <Link href={`/companies/${encodeURIComponent(c.ticker)}`} className="font-mono text-xs text-accent-ink hover:underline">
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
          <BlueprintCorners />
          <h2 id="digest-reviews" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
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
          <BlueprintCorners />
          <h2 id="digest-macro" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
            <LandmarkIcon aria-hidden="true" className="size-4 text-text-muted" />
            Macro figures
          </h2>
          <p className="text-xs text-text-muted">The latest value of each collected series, with its period, release date and source.</p>
          <div className="relative overflow-x-auto">
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
                        <a href={m.url} target="_blank" rel="noreferrer" className="inline-flex items-start gap-1 hover:text-accent-ink hover:underline">
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
          <BlueprintCorners />
          <h2 id="digest-managers" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
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
                  <Link href={`/companies/${encodeURIComponent(c.ticker)}/governance#ownership-heading`} className="font-mono text-xs text-accent-ink hover:underline">
                    {c.ticker}
                  </Link>
                  <span>{managerChangeText(c)}</span>
                  <span className="num text-xs text-text-muted">
                    quarter ended {formatDate(c.period)} (against {formatDate(c.previous_period)})
                  </span>
                  {filing ? (
                    <a href={filing} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent-ink hover:underline">
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
          <BlueprintCorners />
          <h2 id="digest-holidays" className="flex items-center gap-1.5 text-[22px] leading-tight font-semibold">
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
