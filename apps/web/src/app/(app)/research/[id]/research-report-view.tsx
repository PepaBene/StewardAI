"use client";

import {
  BotIcon,
  CalculatorIcon,
  ChevronRightIcon,
  DownloadIcon,
  FileTextIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  TriangleAlertIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Ring, Sparkline } from "@/components/charts";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { TierBadge } from "@/components/news/severity-badge";
import { SourceChips, SourceLinksLine } from "@/components/sources/source-list";
import { Blueprint, BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { checkedTextClass, ClaimsSummaryLine, SentenceCheckMark } from "@/components/verify/sentence-check";
import {
  getBrowserApiClient,
  type CitedSentence,
  type HoldingDetail,
  type JobDetail,
  type JobSummary,
  type ResearchReportOut,
  type ResearchSummary,
} from "@/lib/api/client";
import { researchSentencePath } from "@/lib/feedback/feedback";
import { formatDate, formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { durationBetween, formatCost, formatDuration, jobHref, totalCost } from "@/lib/jobs/job-detail";
import {
  claimTally,
  conclusionSection,
  holdingHref,
  keyFigures,
  kpiFigure,
  RESEARCH_HREF,
  researchForTicker,
  researchHref,
  researchIndex,
  stepLabel,
  triggerLabel,
  valuationLabel,
  WRITTEN_BY,
  writtenBy,
} from "@/lib/research/report";
import { sourceKindLabel, type CitationIndex } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

import { KpiThresholdFigure } from "./kpi-figure";

const LINK = "text-accent-ink underline-offset-4 hover:underline";

/*
 * Print: only the report. Hides the app shell (header, nav, chat) and the page's own
 * controls, drops the frames' registration marks and lays the aside out after the body.
 * Scoped to this page by being rendered only here; no colours of its own.
 */
const PRINT_CSS = `
@media print {
  @page { margin: 14mm; }
  body > div > header, [data-print-hide] { display: none !important; }
  #main { max-width: none !important; padding: 0 !important; }
  [data-print-report] .bp-corner { display: none !important; }
  [data-print-report] [data-print-stack] { display: block !important; }
  [data-print-report] [data-print-stack] > * + * { margin-top: 16px; }
  [data-print-report] section, [data-print-report] figure { break-inside: avoid; }
  [data-print-report] { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
`;

export function ResearchReportView({ id }: { id: number }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/research/{report_id}", { params: { path: { report_id: id } } }),
    [id],
  );
  const { state, reload } = useApiResource(fetcher);
  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading research report…
      </p>
    );
  }
  if (state.kind === "error" && !state.previous) {
    return (
      <AlertMessage title={state.status === 404 ? `No research report #${id}` : state.title}>
        <p>{state.status === 404 ? "The API has no research report with this number." : state.detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Try again
        </Button>
      </AlertMessage>
    );
  }
  const report = state.kind === "ok" ? state.data : state.previous!;
  return <Report report={report} />;
}

/** "Computed" (code) or "Model-written", as a word with an icon. */
export function WriterTag({ by, className }: { by: "code" | "model"; className?: string }) {
  const Icon = by === "model" ? BotIcon : CalculatorIcon;
  return (
    <span
      title={WRITTEN_BY[by].description}
      className={cn(
        "inline-flex items-center gap-1 border px-1 font-sans text-xs font-normal tracking-normal whitespace-nowrap text-text-muted",
        by === "model" ? "border-border bg-surface-muted" : "border-dashed border-border",
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3" />
      {WRITTEN_BY[by].label}
    </span>
  );
}

/** GET a resource once per key; null while loading or when it failed. */
function useOptional<T>(key: string | null, load: () => Promise<{ data?: T }>): T | null {
  const [got, setGot] = useState<{ key: string; data: T | null } | null>(null);
  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    load()
      .then(({ data }) => {
        if (!cancelled) setGot({ key, data: data ?? null });
      })
      .catch(() => {
        if (!cancelled) setGot({ key, data: null });
      });
    return () => {
      cancelled = true;
    };
    // `load` is derived from `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return got && got.key === key ? got.data : null;
}

function Report({ report }: { report: ResearchReportOut }) {
  const { body } = report;
  const index = useMemo(() => researchIndex(body), [body]);
  const job = useOptional<JobDetail>(report.job_id === null ? null : `job:${report.job_id}`, () =>
    getBrowserApiClient().GET("/jobs/{job_id}", { params: { path: { job_id: report.job_id! } } }),
  );
  const holding = useOptional<HoldingDetail>(`holding:${body.ticker}`, () =>
    getBrowserApiClient().GET("/holdings/{ticker}", { params: { path: { ticker: body.ticker } } }),
  );

  const conclusion = conclusionSection(body.sections);
  const sections = body.sections.map((section, s) => ({ section, s })).filter(({ section }) => section !== conclusion);
  const figures = holding ? keyFigures(body.sources, holding.facts) : [];
  const citedFactIds = body.sources.filter((s) => s.kind === "fact").map((s) => Number(s.ref));
  const kpiFig = holding ? kpiFigure(holding.kpis, holding.facts, citedFactIds) : null;
  const duration = job ? durationBetween(job.started_at, job.finished_at) : null;

  return (
    <div data-print-report className="flex flex-col gap-[22px]">
      <style>{PRINT_CSS}</style>
      <nav aria-label="Breadcrumb" className="text-[13px] text-text-muted" data-print-hide>
        <ol className="flex flex-wrap items-center gap-1.5">
          <li>
            <Link href={RESEARCH_HREF} className={LINK}>
              Research
            </Link>
          </li>
          <ChevronRightIcon aria-hidden="true" className="size-3.5" />
          <li>
            <Link href={researchForTicker(body.ticker)} className={cn("font-mono", LINK)}>
              {body.ticker}
            </Link>
          </li>
          <ChevronRightIcon aria-hidden="true" className="size-3.5" />
          <li aria-current="page" className="num">
            R-{String(report.id).padStart(4, "0")}
          </li>
        </ol>
      </nav>

      <div className="flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-[880px] min-w-0">
          <ul className="mb-2.5 flex flex-wrap gap-1.5 text-[11px]" aria-label="Report tags">
            <li className="bg-accent px-2 py-[3px] font-semibold text-accent-foreground capitalize">{body.depth}</li>
            <li className="bg-border-soft px-2 py-[3px] font-mono">{body.ticker}</li>
            <li className="num bg-border-soft px-2 py-[3px]">
              {job?.finished_at ? (
                <>
                  {job.status === "succeeded" ? "Completed" : "Finished"} {formatDate(new Date(job.finished_at))}
                  {duration !== null ? ` · ${formatDuration(duration)}` : ""}
                </>
              ) : (
                <>Written {formatDate(new Date(report.created_at))}</>
              )}
            </li>
          </ul>
          <h1 className="page-title text-balance">{body.headline || report.title}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
            <span className="font-medium text-text">{body.name}</span>
            {body.owned ? (
              <Link href={holdingHref(body.ticker)} className={LINK}>
                Open the holding {body.ticker}
              </Link>
            ) : (
              <span>Not owned</span>
            )}
            <span>
              · profile{" "}
              <Link href="/config/research-profiles" className={LINK}>
                {body.profile_name}
              </Link>{" "}
              (<span className="font-mono text-xs">{body.profile_id}</span>, {body.depth})
            </span>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5" data-print-hide>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => window.print()}>
              <DownloadIcon aria-hidden="true" />
              PDF
            </Button>
            <RerunButton ticker={body.ticker} profile={body.profile_id} />
          </div>
        </div>
      </div>

      {job && job.steps.length > 0 ? <Pipeline job={job} /> : null}

      <div data-print-stack className="grid items-start gap-7 lg:grid-cols-[minmax(0,1fr)_380px]">
        <article className="flex min-w-0 flex-col gap-[22px]">
          {conclusion ? (
            <section className="blueprint border-accent-300 bg-accent-soft px-6 py-[22px]" aria-labelledby="research-conclusion">
              <BlueprintCorners />
              <h2 id="research-conclusion" className="kicker mb-2 flex items-center gap-2 font-sans">
                {conclusion.title || "Conclusion"} <WriterTag by={writtenBy(conclusion.written_by)} />
              </h2>
              <p className="font-serif text-[19px] leading-relaxed">
                {conclusion.sentences.map((sentence, i) => (
                  <InlineSentence
                    key={i}
                    reportId={report.id}
                    sentence={sentence}
                    path={researchSentencePath(body.sections.indexOf(conclusion), i)}
                    index={index}
                  />
                ))}
              </p>
            </section>
          ) : null}

          {(body.notes ?? []).length > 0 ? (
            <AlertMessage tone="info" title="Notes on this run">
              <ul className="list-disc space-y-0.5 pl-5">
                {(body.notes ?? []).map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </AlertMessage>
          ) : null}

          {kpiFig ? (
            <Blueprint className="px-[22px] py-5">
              <KpiThresholdFigure figure={kpiFig} number={1} />
            </Blueprint>
          ) : null}

          <p className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            Each sentence is marked <WriterTag by="code" /> (from stored records) or <WriterTag by="model" />; the numbers
            link to the sources.
          </p>

          {sections.map(({ section, s }, n) => (
            <section key={`${section.id}-${s}`} aria-labelledby={`research-${section.id}`} className="flex max-w-[820px] flex-col gap-3.5">
              <h2 id={`research-${section.id}`} className="flex flex-wrap items-center gap-2.5 text-[28px] leading-tight font-semibold">
                <span>
                  {n + 1}. {section.title}
                </span>
                <WriterTag by={writtenBy(section.written_by)} />
              </h2>
              {section.note ? <p className="text-xs text-text-muted">{section.note}</p> : null}
              {section.sentences.length === 0 ? <p className="text-sm text-text-muted">Nothing to report from the sources.</p> : null}
              <div className="flex flex-col gap-2">
                {section.sentences.map((sentence, i) => (
                  <ResearchSentence
                    key={i}
                    reportId={report.id}
                    sentence={sentence}
                    sectionWriter={writtenBy(section.written_by)}
                    path={researchSentencePath(s, i)}
                    index={index}
                  />
                ))}
              </div>
              {section.id === "valuation" && body.valuation.length > 0 ? <ValuationTable report={report} index={index} /> : null}
            </section>
          ))}
          {body.valuation.length > 0 && !body.sections.some((s) => s.id === "valuation") ? (
            <section aria-labelledby="research-valuation-table" className="flex max-w-[820px] flex-col gap-3.5">
              <h2 id="research-valuation-table" className="text-[28px] leading-tight font-semibold">
                Valuation
              </h2>
              <ValuationTable report={report} index={index} />
            </section>
          ) : null}

          <div className="flex max-w-[820px] flex-col gap-1.5 bg-border-soft px-3 py-2 text-xs text-text-muted">
            <p className="flex items-start gap-2">
              <ShieldCheckIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-accent-ink" />
              Figures in this report are written by code from stored facts; the model writes only the prose, and the claim
              verifier checks its sentences against the cited snippets.
            </p>
            <ClaimsSummaryLine claims={body.claims} />
          </div>
        </article>

        <aside className="flex min-w-0 flex-col gap-5" aria-label="Report details">
          <ClaimsTile report={report} />
          <KeyFigures figures={figures} loaded={holding !== null} />
          <SourcesPanel index={index} />
          <RunDetails report={report} job={job} />
        </aside>
      </div>
    </div>
  );
}

/** The job's real steps as a stepper: one bar per step, coloured by status, with its label. */
function Pipeline({ job }: { job: JobDetail }) {
  return (
    <ol className="flex flex-wrap gap-y-3" aria-label={`Pipeline of job #${job.id}`}>
      {job.steps.map((step) => {
        const ms = durationBetween(step.started_at, step.finished_at);
        const detail = [
          step.status === "running" ? "running" : step.status,
          ms !== null ? formatDuration(ms) : null,
          step.model,
          step.cost_usd !== null ? formatCost(step.cost_usd) : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <li key={step.step_index} className="flex min-w-[120px] flex-1 flex-col gap-1.5" title={step.error ?? detail}>
            <span
              aria-hidden="true"
              className={cn(
                "mr-[3px] h-1",
                step.status === "succeeded" && "bg-accent",
                step.status === "running" && "bg-accent-300",
                step.status === "failed" && "bg-loss",
              )}
            />
            <span className="text-[11px] text-text-muted">
              {step.status === "failed" ? <TriangleAlertIcon aria-hidden="true" className="mr-1 inline size-3 text-loss" /> : null}
              {stepLabel(step.name)}
              <span className="sr-only">: {detail}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ClaimsTile({ report }: { report: ResearchReportOut }) {
  const tally = claimTally(report.body.claims);
  return (
    <Blueprint className="flex flex-col gap-2 px-5 py-[18px]">
      <div className="flex items-center gap-4">
        <Ring
          value={tally ? tally.supported : null}
          max={tally?.total ?? 1}
          size={70}
          thickness={6}
          label=""
          ariaLabel="Sentences the claim verifier supported"
        />
        <div>
          <p className="num font-heading text-[28px] leading-none font-semibold">
            {tally ? `${tally.supported} / ${tally.total}` : "—"}
          </p>
          <p className="text-[13px] text-text-muted">
            {tally ? "sentences verified against cited snippets" : "no sentence went through the claim check"}
          </p>
        </div>
      </div>
      <ClaimsSummaryLine claims={report.body.claims} />
    </Blueprint>
  );
}

function KeyFigures({ figures, loaded }: { figures: ReturnType<typeof keyFigures>; loaded: boolean }) {
  if (loaded && figures.length === 0) return null;
  return (
    <Blueprint className="flex flex-col gap-2.5 px-5 py-[18px]">
      <h2 className="kicker font-sans">Key figures</h2>
      {!loaded ? (
        <p className="text-xs text-text-faint">Loading the stored figures…</p>
      ) : (
        <ul>
          {figures.map((f) => (
            <li key={f.factId} className="flex items-center justify-between gap-3 border-t border-border-soft py-1.5 text-sm">
              <Link href={`/evidence/${f.factId}`} className="min-w-0 truncate text-text-muted hover:text-text hover:underline" title={f.label}>
                {f.label}
              </Link>
              <span className="flex shrink-0 items-center gap-2.5">
                <span className="w-[50px]">
                  <Sparkline values={f.series} width={50} height={16} className="h-4" />
                </span>
                <b className="num min-w-16 text-right font-semibold">{f.value}</b>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Blueprint>
  );
}

/** Every source, numbered as the citations are, with its kind, tier, date, snippet and links. */
function SourcesPanel({ index }: { index: CitationIndex }) {
  return (
    <Blueprint className="flex flex-col gap-2.5 px-5 py-[18px]" aria-labelledby="sources-heading">
      <h2 id="sources-heading" className="kicker font-sans">
        Sources · {index.cards.length}
      </h2>
      {index.unresolved.length > 0 ? (
        <p role="alert" className="flex items-start gap-1.5 border border-warning-signal/40 bg-warning-signal-subtle p-2 text-xs text-text">
          <TriangleAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning-signal" />
          Cited but not in the source list: {index.unresolved.join(", ")}.
        </p>
      ) : null}
      {index.cards.length === 0 ? <p className="text-sm text-text-muted">No sources stored.</p> : null}
      <ol>
        {index.cards.map(({ source: s, number, anchor, citedBy }) => (
          <li
            key={s.id}
            id={anchor}
            tabIndex={-1}
            className="grid scroll-mt-24 grid-cols-[28px_minmax(0,1fr)] gap-2 border-t border-border-soft py-2 text-[13px] outline-none target:bg-accent-soft focus-visible:bg-accent-soft"
          >
            <span className="num font-semibold text-accent-ink">[{number}]</span>
            <div className="min-w-0 space-y-0.5">
              <p className="break-words">{s.title}</p>
              <p className="flex flex-wrap items-center gap-x-1.5 text-[11px] text-text-muted">
                <span className="font-mono">{s.id}</span>· {sourceKindLabel(s.kind)} ·{" "}
                {typeof s.tier === "number" ? <TierBadge tier={s.tier} className="text-[11px]" /> : <span>no tier</span>}
                {s.as_of ? <span>· as of {/^\d{4}-\d{2}-\d{2}$/.test(s.as_of) ? formatDate(s.as_of) : s.as_of}</span> : null}
                <span>· {citedBy === 0 ? "not cited in the text" : `cited ${citedBy}×`}</span>
              </p>
              {s.snippet ? <p className="line-clamp-2 text-xs text-text-muted" title={s.snippet}>{s.snippet}</p> : null}
              <SourceLinksLine source={s} />
            </div>
          </li>
        ))}
      </ol>
    </Blueprint>
  );
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="font-semibold break-words">{children}</dd>
    </div>
  );
}

function RunDetails({ report, job }: { report: ResearchReportOut; job: JobDetail | null }) {
  const { body } = report;
  const cost = job ? totalCost(job.steps) : null;
  const factsUsed = body.sources.filter((s) => s.kind === "fact").length;
  return (
    <Blueprint className="px-5 py-[18px]" aria-label="Run details">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 text-[13px]">
        <Item label="Profile">
          <Link href="/config/research-profiles" className="hover:underline">
            {body.profile_name}
          </Link>{" "}
          · <span className="capitalize">{body.depth}</span>
        </Item>
        <Item label="Cost">
          <span className="num">{job ? formatCost(cost) : "—"}</span>
        </Item>
        <Item label="Model">
          <span className="font-mono text-xs font-normal">{body.model}</span>
        </Item>
        <Item label="Facts used">
          <span className="num">
            {factsUsed} stored record{factsUsed === 1 ? "" : "s"}
          </span>
        </Item>
        <Item label="Requested">{job ? triggerLabel(job.trigger) : "—"}</Item>
        <Item label="Language">{body.language}</Item>
        <Item label="Steps · searches">
          <span className="num">
            {body.steps_used} · {body.searches_used}
          </span>
        </Item>
        <Item label="Job">
          {report.job_id !== null ? (
            <Link href={jobHref(report.job_id)} className={cn("num font-normal", LINK)}>
              job #{report.job_id}
            </Link>
          ) : (
            "—"
          )}
        </Item>
      </dl>
      <p className="mt-3 border-t border-border-soft pt-2 text-xs text-text-muted">
        Written <time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time>
        {body.dropped_sentences
          ? ` · ${body.dropped_sentences} model sentence${body.dropped_sentences === 1 ? "" : "s"} removed for citing nothing checkable`
          : ""}
      </p>
      <AsOfLabel at={report.created_at} kind="escalation" prefix="Written" className="mt-1" />
    </Blueprint>
  );
}

/** Re-run the same profile on the same ticker (`POST /jobs/research`), following the job. */
function RerunButton({ ticker, profile }: { ticker: string; profile: string }) {
  const [job, setJob] = useState<JobSummary | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [next, setNext] = useState<ResearchSummary | null>(null);
  const watched = useJobStatus(job);
  const done = watched?.status === "succeeded";

  useEffect(() => {
    if (!done || !job) return;
    let cancelled = false;
    getBrowserApiClient()
      .GET("/research", { params: { query: { ticker, limit: 20 } } })
      .then(({ data }) => {
        if (!cancelled && data) setNext(data.find((r) => r.job_id === job.id) ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [done, job, ticker]);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/research", { body: { ticker, profile } });
      if (data) setJob(data);
      else setError(`Not queued (HTTP ${response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  const running = watched !== null && !watched.gaveUp && watched.status !== "succeeded" && watched.status !== "failed";
  return (
    <div className="flex flex-col items-end gap-1">
      <Button onClick={() => void start()} disabled={pending || running}>
        {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
        Re-run with new data
      </Button>
      <div role="status" aria-live="polite" className="text-xs">
        {error ? <span>Re-run not started: {error}</span> : null}
        {job && watched ? (
          <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
            <JobStatusBadge status={watched.status} />
            <Link href={jobHref(job.id)} className={LINK}>
              job #{job.id}
            </Link>
            {watched.status === "failed" && watched.error ? <span>{watched.error}</span> : null}
            {next ? (
              <Link href={researchHref(next.id)} className={cn("inline-flex items-center gap-1 font-medium", LINK)}>
                <FileTextIcon aria-hidden="true" className="size-3.5" />
                Open the new report
              </Link>
            ) : null}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ResearchSentence({
  reportId,
  sentence,
  sectionWriter,
  path,
  index,
}: {
  reportId: number;
  sentence: CitedSentence;
  sectionWriter: "code" | "model";
  path: string;
  index: CitationIndex;
}) {
  const by = writtenBy(sentence.written_by);
  return (
    <p className="font-serif text-base leading-relaxed">
      {by !== sectionWriter ? <WriterTag by={by} className="mr-1 align-middle" /> : <span className="sr-only">{WRITTEN_BY[by].label}: </span>}
      <span className={checkedTextClass(sentence)}>{sentence.text}</span>
      <SourceChips ids={sentence.source_ids} index={index} className="font-sans" />
      {(sentence.source_ids ?? []).length === 0 ? <span className="ml-1 font-sans text-xs text-text-muted">(no source)</span> : null}
      <span className="font-sans">
        <SentenceCheckMark sentence={sentence} />
        <FeedbackControl
          className="ml-1 print:hidden"
          target={{ kind: "report_sentence", id: reportId, sentencePath: path }}
          label={`sentence “${sentence.text.slice(0, 60)}${sentence.text.length > 60 ? "…" : ""}”`}
        />
      </span>
    </p>
  );
}

/** A conclusion sentence, inline in the paragraph, with the same chips, check mark and feedback. */
function InlineSentence({
  reportId,
  sentence,
  path,
  index,
}: {
  reportId: number;
  sentence: CitedSentence;
  path: string;
  index: CitationIndex;
}) {
  return (
    <span>
      <span className={checkedTextClass(sentence)}>{sentence.text}</span>
      <SourceChips ids={sentence.source_ids} index={index} className="font-sans" />
      <span className="font-sans">
        <SentenceCheckMark sentence={sentence} />
        <FeedbackControl
          className="ml-1 print:hidden"
          target={{ kind: "report_sentence", id: reportId, sentencePath: path }}
          label={`sentence “${sentence.text.slice(0, 60)}${sentence.text.length > 60 ? "…" : ""}”`}
        />
      </span>{" "}
    </span>
  );
}

/** Valuation figures computed in code, each with its formula and the records it uses. */
function ValuationTable({ report, index }: { report: ResearchReportOut; index: CitationIndex }) {
  return (
    <div className="blueprint overflow-x-auto">
      <BlueprintCorners />
      <table className="w-full min-w-[34rem] text-sm">
        <caption className="px-3 pt-2 text-left text-xs text-text-muted">
          Computed in code from stored figures and prices (no model involved).
        </caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-[11px] tracking-wide text-text-muted uppercase">
            <th scope="col" className="px-3 py-2 text-left font-medium">Method</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Formula</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Sources</th>
          </tr>
        </thead>
        <tbody>
          {report.body.valuation.map((v) => (
            <tr key={v.method} className="border-b border-border-soft align-top last:border-0">
              <th scope="row" className="px-3 py-2 text-left font-medium">
                {valuationLabel(v.method)}
                {v.note ? <span className="block text-xs font-normal text-text-muted">{v.note}</span> : null}
              </th>
              <td className="num px-3 py-2 text-right" title={v.value === null ? "Not computable" : String(v.value)}>
                {v.value === null ? <span className="text-text-muted">{v.display || "n/a"}</span> : v.display}
              </td>
              <td className="px-3 py-2 font-mono text-xs break-words">{v.formula}</td>
              <td className="px-3 py-2">
                <SourceChips ids={v.source_ids} index={index} className="ml-0" />
                {v.source_ids.length === 0 ? <span className="text-xs text-text-muted">none</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
