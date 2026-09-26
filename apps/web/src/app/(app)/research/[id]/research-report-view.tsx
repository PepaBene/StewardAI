"use client";

import { BotIcon, CalculatorIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo } from "react";

import { AlertMessage } from "@/components/alert-message";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { AsOfLabel } from "@/components/freshness/as-of-label";
import { SourceChips, SourceSidebar } from "@/components/sources/source-list";
import { Button } from "@/components/ui/button";
import { checkedTextClass, ClaimsSummaryLine, SentenceCheckMark } from "@/components/verify/sentence-check";
import { getBrowserApiClient, type CitedSentence, type ResearchReportOut } from "@/lib/api/client";
import { researchSentencePath } from "@/lib/feedback/feedback";
import { formatDateTime } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { jobHref } from "@/lib/jobs/job-detail";
import {
  holdingHref,
  RESEARCH_HREF,
  researchForTicker,
  researchIndex,
  valuationLabel,
  WRITTEN_BY,
  writtenBy,
} from "@/lib/research/report";
import type { CitationIndex } from "@/lib/sources/sources";
import { cn } from "@/lib/utils";

const LINK = "text-accent underline-offset-4 hover:underline";

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
        "inline-flex items-center gap-1 rounded-sm border px-1 text-xs whitespace-nowrap text-text-muted",
        by === "model" ? "border-border bg-surface-muted" : "border-dashed border-border",
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3" />
      {WRITTEN_BY[by].label}
    </span>
  );
}

function Report({ report }: { report: ResearchReportOut }) {
  const { body } = report;
  const index = useMemo(() => researchIndex(body), [body]);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-text-muted">
            <Link href={RESEARCH_HREF} className="hover:text-text hover:underline">
              Research
            </Link>{" "}
            /{" "}
            <Link href={researchForTicker(body.ticker)} className="font-mono hover:text-text hover:underline">
              {body.ticker}
            </Link>{" "}
            / <span className="num">#{report.id}</span>
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">{body.headline || report.title}</h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
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
        <AsOfLabel at={report.created_at} kind="escalation" prefix="Written" />
      </div>

      <div className="space-y-1 text-xs text-text-muted">
        <p className="num">
          Written {formatDateTime(report.created_at)} by <span className="font-mono">{body.model}</span> · language {body.language} ·{" "}
          {body.steps_used} step{body.steps_used === 1 ? "" : "s"} · {body.searches_used} web search
          {body.searches_used === 1 ? "" : "es"}
          {body.dropped_sentences
            ? ` · ${body.dropped_sentences} model sentence${body.dropped_sentences === 1 ? "" : "s"} removed for citing nothing checkable`
            : ""}
          {report.job_id !== null ? (
            <>
              {" · "}
              <Link href={jobHref(report.job_id)} className={LINK}>
                job #{report.job_id}
              </Link>
            </>
          ) : null}
        </p>
        <ClaimsSummaryLine claims={body.claims} />
        <p className="flex flex-wrap items-center gap-2">
          Each sentence is marked <WriterTag by="code" /> (from stored records) or <WriterTag by="model" />; the numbers link to
          the sources on the right.
        </p>
      </div>

      {(body.notes ?? []).length > 0 ? (
        <AlertMessage tone="info" title="Notes on this run">
          <ul className="list-disc space-y-0.5 pl-5">
            {(body.notes ?? []).map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </AlertMessage>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <article className="space-y-6">
          {body.sections.map((section, s) => (
            <section key={`${section.id}-${s}`} aria-labelledby={`research-${section.id}`} className="space-y-2">
              <h2 id={`research-${section.id}`} className="flex flex-wrap items-center gap-2 text-lg font-semibold">
                {section.title}
                <WriterTag by={writtenBy(section.written_by)} />
              </h2>
              {section.note ? <p className="text-xs text-text-muted">{section.note}</p> : null}
              {section.sentences.length === 0 ? <p className="text-sm text-text-muted">Nothing to report from the sources.</p> : null}
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
              {section.id === "valuation" && body.valuation.length > 0 ? <ValuationTable report={report} index={index} /> : null}
            </section>
          ))}
          {body.valuation.length > 0 && !body.sections.some((s) => s.id === "valuation") ? (
            <section aria-labelledby="research-valuation-table" className="space-y-2">
              <h2 id="research-valuation-table" className="text-lg font-semibold">
                Valuation
              </h2>
              <ValuationTable report={report} index={index} />
            </section>
          ) : null}
        </article>

        <SourceSidebar index={index} />
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
    <p className="max-w-prose text-sm">
      {by !== sectionWriter ? <WriterTag by={by} className="mr-1 align-middle" /> : <span className="sr-only">{WRITTEN_BY[by].label}: </span>}
      <span className={checkedTextClass(sentence)}>{sentence.text}</span>
      <SourceChips ids={sentence.source_ids} index={index} />
      {(sentence.source_ids ?? []).length === 0 ? <span className="ml-1 text-xs text-text-muted">(no source)</span> : null}
      <SentenceCheckMark sentence={sentence} />
      <FeedbackControl
        className="ml-1"
        target={{ kind: "report_sentence", id: reportId, sentencePath: path }}
        label={`sentence “${sentence.text.slice(0, 60)}${sentence.text.length > 60 ? "…" : ""}”`}
      />
    </p>
  );
}

/** Valuation figures computed in code, each with its formula and the records it uses. */
function ValuationTable({ report, index }: { report: ResearchReportOut; index: CitationIndex }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
      <table className="w-full min-w-[34rem] text-sm">
        <caption className="px-3 pt-2 text-left text-xs text-text-muted">
          Computed in code from stored figures and prices (no model involved).
        </caption>
        <thead>
          <tr className="border-b border-border text-text-muted">
            <th scope="col" className="px-3 py-2 text-left font-medium">Method</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Value</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Formula</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Sources</th>
          </tr>
        </thead>
        <tbody>
          {report.body.valuation.map((v) => (
            <tr key={v.method} className="border-b border-border align-top last:border-0">
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
