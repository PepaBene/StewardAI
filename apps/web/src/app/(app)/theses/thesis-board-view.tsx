"use client";

import {
  CalendarClockIcon,
  ClockIcon,
  InboxIcon,
  InfoIcon,
  LoaderCircleIcon,
  PencilIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { BarChart, Gauge, SparkArea, StackedBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobSummary, type ThesisBoard } from "@/lib/api/client";
import {
  boardSummary,
  groupByState,
  scoreChange,
  shortTicker,
  stateCounts,
  THESIS_STATE,
  THESIS_STATE_ORDER,
  type ThesisState,
} from "@/lib/evidence/board";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { useNow } from "@/lib/hooks/use-now";
import { jobHref } from "@/lib/jobs/job-detail";
import { cn } from "@/lib/utils";

import { RankingSection } from "./ranking-section";
import { ThesisCard } from "./thesis-card";

const fetchBoard = () => getBrowserApiClient().GET("/theses/board");

/** Holdings under this score step back to the lighter bar in "Score by holding". */
const SCORE_THRESHOLD = 60;
const LINK = "text-accent-ink underline-offset-4 hover:underline";

export function ThesisBoardView() {
  const { state, reload } = useApiResource(fetchBoard);
  const board = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
        kicker="Research & Verification"
        title="Theses"
        description="Each holding's thesis, scored from its KPIs against the latest verified figures."
        aside={<RejudgeAll onDone={reload} />}
      />

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the thesis board…
        </p>
      ) : null}
      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      {board ? <Board board={board} /> : null}
    </div>
  );
}

/** "Edit theses" and "Re-judge all" (every holding's verdicts, as a job), with the job's progress. */
function RejudgeAll({ onDone }: { onDone: () => void }) {
  const [job, setJob] = useState<JobSummary | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const watched = useJobStatus(job);

  const succeeded = watched?.status === "succeeded";
  useEffect(() => {
    if (succeeded) onDone();
  }, [succeeded, onDone]);

  async function start() {
    setPending(true);
    setError(null);
    try {
      const { data, response } = await getBrowserApiClient().POST("/jobs/thesis-verdicts", { body: { ticker: null } });
      if (data) setJob(data);
      else setError(`Not queued (HTTP ${response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex gap-2">
        <Button asChild variant="outline">
          <Link href="/config/theses">
            <PencilIcon aria-hidden="true" />
            Edit theses
          </Link>
        </Button>
        <Button onClick={() => void start()} disabled={pending || (watched !== null && !watched.gaveUp && !["succeeded", "failed"].includes(watched.status))}>
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <RefreshCwIcon aria-hidden="true" />}
          Re-judge all
        </Button>
      </div>
      <div role="status" aria-live="polite" className="text-xs">
        {error ? <span className="text-text">Re-judging not started: {error}</span> : null}
        {job && watched ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            Re-judging every holding
            <JobStatusBadge status={watched.status} />
            <Link href={jobHref(job.id)} className={LINK}>
              job #{job.id}
            </Link>
            {watched.status === "failed" && watched.error ? <span>{watched.error}</span> : null}
            {watched.gaveUp ? <span>Still running; follow the job page.</span> : null}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** "Verdicts as of 25 Sep 2026, 14:05 · 3 h ago": the newest verdict on the board. */
function VerdictsAsOf({ asOf }: { asOf: string | null }) {
  const now = useNow();
  return (
    <p className="flex items-center gap-1.5 text-sm text-text-muted">
      <ClockIcon aria-hidden="true" className="size-4" />
      {asOf ? (
        <span className="num">
          Verdicts as of <time dateTime={asOf}>{formatDateTime(asOf)}</time> · {formatRelative(new Date(asOf), now)}
        </span>
      ) : (
        <span>No verdict yet</span>
      )}
    </p>
  );
}

type Open = { ticker: string; kpiId: string } | null;

function Board({ board }: { board: ThesisBoard }) {
  const [open, setOpen] = useState<Open>(null);

  if (board.rows.length === 0) {
    return (
      <>
        <VerdictsAsOf asOf={board.as_of} />
        <EmptyState title="No theses configured">
          Write why you own each holding and the KPIs that must hold in the{" "}
          <Link className={LINK} href="/config/theses">
            theses editor
          </Link>
          . Saving queues a verdict run, and every holding with a thesis appears here.
        </EmptyState>
      </>
    );
  }

  const summary = boardSummary(board.rows);
  const groups = groupByState(board.rows);

  const card = (row: ThesisBoard["rows"][number]) => (
    <ThesisCard
      key={row.ticker}
      row={row}
      openKpiId={open?.ticker === row.ticker ? open.kpiId : null}
      onToggle={(kpiId) => setOpen(kpiId ? { ticker: row.ticker, kpiId } : null)}
    />
  );

  return (
    <>
      <StatRow board={board} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <VerdictsAsOf asOf={board.as_of} />
        <p className="num text-text-muted">
          <span className="font-medium text-text">
            {summary.holdings} holding{summary.holdings === 1 ? "" : "s"}
          </span>{" "}
          · {summary.byOverall.breach} breach · {summary.byOverall.unknown} unknown · {summary.byOverall.pass} pass
          {summary.byOverall.no_kpis > 0 ? ` · ${summary.byOverall.no_kpis} without KPIs` : ""}
        </p>
        {summary.openReviewItems > 0 ? (
          <Link href="/review" className={`inline-flex items-center gap-1 ${LINK}`}>
            <InboxIcon aria-hidden="true" className="size-4" />
            <span className="num">{summary.openReviewItems}</span> figure{summary.openReviewItems === 1 ? "" : "s"} wait
            {summary.openReviewItems === 1 ? "s" : ""} for review
          </Link>
        ) : null}
        {summary.overdue > 0 ? (
          <span className="inline-flex items-center gap-1">
            <CalendarClockIcon aria-hidden="true" className="size-4 text-warning-signal" />
            <span className="num">{summary.overdue}</span> thesis review{summary.overdue === 1 ? "" : "s"} overdue
          </span>
        ) : null}
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-[2fr_1fr_1fr]">
        <Column state="intact" count={groups.intact.length} wide>
          {groups.intact.map(card)}
        </Column>
        <Column state="watch" count={groups.watch.length}>
          {groups.watch.map(card)}
        </Column>
        <Column state="at_risk" count={groups.at_risk.length}>
          {groups.at_risk.map(card)}
        </Column>
      </div>
      {groups.unjudged.length > 0 ? (
        <Column state="unjudged" count={groups.unjudged.length} wide>
          {groups.unjudged.map(card)}
        </Column>
      ) : null}

      <p id="score-method" className="flex max-w-4xl items-start gap-1.5 text-xs text-text-muted">
        <InfoIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        <span>
          <span className="font-medium text-text">How scores work.</span> {board.score_method}
        </span>
      </p>

      <RankingSection />
    </>
  );
}

const RULE: Record<ThesisState, string> = {
  intact: "border-accent",
  watch: "border-warning-signal",
  at_risk: "border-loss",
  unjudged: "border-border",
};

function Column({
  state,
  count,
  wide = false,
  children,
}: {
  state: ThesisState;
  count: number;
  wide?: boolean;
  children: React.ReactNode;
}) {
  const id = `column-${state}`;
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-3">
      <div className={cn("flex items-center gap-2.5 border-b-2 pb-2.5", RULE[state])}>
        <h2 id={id} className="text-[22px] leading-tight font-semibold">
          {THESIS_STATE[state].label}
        </h2>
        <span className="num bg-border-soft px-2 py-px text-xs">{count}</span>
        <span className="ml-auto text-xs text-text-muted">{THESIS_STATE[state].note}</span>
      </div>
      {count === 0 ? (
        <p className="border border-dashed border-border px-4 py-6 text-center text-xs text-text-faint">None</p>
      ) : (
        <div className={cn("grid items-start gap-3.5", wide && "sm:grid-cols-2", wide && state === "unjudged" && "lg:grid-cols-4")}>
          {children}
        </div>
      )}
    </section>
  );
}

function StatRow({ board }: { board: ThesisBoard }) {
  const counts = stateCounts(board.rows);
  const segments = THESIS_STATE_ORDER.filter((s) => s !== "unjudged" || counts.unjudged > 0)
    .map((s) => ({ state: s, label: THESIS_STATE[s].label, value: counts[s] }))
    .filter((s) => s.value > 0);
  const scored = board.rows
    .filter((r) => r.score !== null)
    .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const change = scoreChange(board.portfolio_score, board.portfolio_score_history);
  const perDay = board.verdicts_30d.per_day.map((d) => d.count);
  const methodHint = "How the score is computed: see the note under the board.";

  return (
    <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-[1fr_1.3fr_1fr_1fr]">
      <Blueprint className="flex flex-col justify-center gap-2 px-5 py-[18px]" aria-label="Theses by state">
        <StackedBar
          segments={segments.map((s) => ({ label: s.label, value: s.value }))}
          title="Theses by state"
          note={`${board.rows.length} ${board.rows.length === 1 ? "thesis" : "theses"}`}
          formatShare={(f) => String(Math.round(f * board.rows.length))}
        />
      </Blueprint>

      <Blueprint className="flex flex-col gap-2 px-5 py-[18px]">
        <h2 className="font-sans text-xs font-normal text-text-muted" title={methodHint}>
          Score by holding
        </h2>
        {scored.length === 0 ? (
          <p className="text-xs text-text-faint">No holding has a judged KPI yet.</p>
        ) : (
          <BarChart
            data={scored.map((r) => ({
              label: shortTicker(r.ticker),
              value: r.score,
              highlight: (r.score ?? 0) >= SCORE_THRESHOLD,
            }))}
            threshold={{ value: SCORE_THRESHOLD, label: `Threshold ${SCORE_THRESHOLD}` }}
            height={130}
            maxBarWidth={40}
            formatValue={(v) => String(Math.round(v))}
            label="Thesis score by holding, 0 to 100"
          />
        )}
      </Blueprint>

      <Blueprint className="flex flex-col gap-1 px-5 py-[18px]">
        <h2 className="flex items-center gap-1 font-sans text-xs font-normal text-text-muted">
          Weighted thesis score
          <a href="#score-method" title={methodHint} className="text-text-faint hover:text-text">
            <InfoIcon aria-hidden="true" className="size-3.5" />
            <span className="sr-only">How the score is computed</span>
          </a>
        </h2>
        <Gauge
          value={board.portfolio_score ?? null}
          max={100}
          formatValue={(v) => String(Math.round(v))}
          sublabel={
            change
              ? `${change.delta > 0 ? "▲ +" : change.delta < 0 ? "▼ −" : "±"}${Math.abs(change.delta)} since ${formatDate(change.since)}`
              : board.portfolio_score == null
                ? "No held holding has a score yet"
                : undefined
          }
          ariaLabel="Weighted thesis score, by position weight"
          className="mx-auto w-full max-w-[240px]"
        />
      </Blueprint>

      <Blueprint className="flex flex-col gap-1.5 px-5 py-[18px]">
        <h2 className="font-sans text-xs font-normal text-text-muted">Verdicts, last 30 days</h2>
        <p className="font-heading text-[32px] leading-none font-semibold">
          <span className="num">{board.verdicts_30d.count}</span>{" "}
          <span className="font-sans text-sm font-normal text-text-muted">
            from <span className="num">{board.verdicts_30d.fact_count}</span> fact
            {board.verdicts_30d.fact_count === 1 ? "" : "s"}
          </span>
        </p>
        <div className="mt-auto pt-2">
          {perDay.some((v) => v > 0) ? (
            <SparkArea values={perDay} width={200} height={50} className="h-[50px]" label="Verdicts recorded per day, 30 days" formatValue={(v) => String(v)} />
          ) : (
            <p className="text-xs text-text-faint">No verdict recorded in the window.</p>
          )}
        </div>
      </Blueprint>
    </div>
  );
}
