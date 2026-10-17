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
import { SparkArea } from "@/components/charts";
import { EntityName } from "@/components/entity-name";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { VerdictLine } from "@/components/insight/verdict-line";
import { JobStatusBadge } from "@/components/jobs/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type JobSummary, type ThesisBoard } from "@/lib/api/client";
import { usePageSelection } from "@/lib/chat/page-selection-store";
import {
  boardSummary,
  groupByState,
  scoreChange,
  stateCounts,
  THESIS_STATE,
  type ThesisState,
} from "@/lib/evidence/board";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useJobStatus } from "@/lib/hooks/use-job-status";
import { useNow } from "@/lib/hooks/use-now";
import { jobHref } from "@/lib/jobs/job-detail";
import { cn } from "@/lib/utils";
import { boardVerdict, weakest } from "@/lib/verdicts/theses";

import { GuidedStart } from "./guided-start";
import { RankingSection } from "./ranking-section";
import { ThesisCard } from "./thesis-card";

const fetchBoard = () => getBrowserApiClient().GET("/theses/board");

const LINK = "text-accent-ink underline-offset-4 hover:underline";

export function ThesisBoardView() {
  const { state, reload } = useApiResource(fetchBoard);
  const board = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHeader
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
  usePageSelection(open ? `thesis ${open.ticker} ${open.kpiId}` : null);

  if (board.rows.length === 0) {
    return (
      <>
        <GuidedStart />
      </>
    );
  }

  const summary = boardSummary(board.rows);
  const groups = groupByState(board.rows);
  const verdict = boardVerdict(board.rows);

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
      <AnswerStrip board={board} overdue={summary.overdue} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <VerdictsAsOf asOf={board.as_of} />
        <p className="num text-text-muted">
          <span className="font-medium text-text">
            {summary.holdings} holding{summary.holdings === 1 ? "" : "s"}
          </span>
          : {summary.byOverall.breach} with a KPI in breach, {summary.byOverall.unknown} not yet known, {summary.byOverall.pass} passing
          {summary.byOverall.no_kpis > 0 ? `, ${summary.byOverall.no_kpis} without KPIs` : ""}
        </p>
        {summary.openReviewItems > 0 ? (
          <Link href="/inbox?kind=figures" className={`inline-flex items-center gap-1 ${LINK}`}>
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

      <VerdictLine verdict={verdict.tone} className="max-w-4xl">
        {verdict.text}
      </VerdictLine>

      <StatRow board={board} />

      {ORDER.map((state) => (
        <Column key={state} state={state} count={groups[state].length}>
          {groups[state].map(card)}
        </Column>
      ))}

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

/** Breaking theses first: what needs a look comes before what is fine. */
const ORDER: ThesisState[] = ["at_risk", "watch", "intact", "unjudged"];

const DOT: Record<ThesisState, string> = {
  intact: "bg-gain",
  watch: "bg-warning-signal",
  at_risk: "bg-loss",
  unjudged: "bg-text-faint",
};

/** Portfolio score, the three state counts and overdue reviews: the board's answer in one row. */
function AnswerStrip({ board, overdue }: { board: ThesisBoard; overdue: number }) {
  const counts = stateCounts(board.rows);
  const change = scoreChange(board.portfolio_score, board.portfolio_score_history);
  return (
    <KpiStrip
      label="Theses summary"
      tiles={[
        {
          label: "Weighted thesis score",
          value: board.portfolio_score == null ? "–" : Math.round(board.portfolio_score),
          note:
            board.portfolio_score == null
              ? "No held company has a score yet"
              : change
                ? `${change.delta > 0 ? "+" : change.delta < 0 ? "−" : "±"}${Math.abs(change.delta)} since ${formatDate(change.since)}`
                : "Out of 100, by position weight",
        },
        { label: "Intact", value: counts.intact, signed: counts.intact > 0 ? "gain" : undefined },
        { label: "Watch", value: counts.watch },
        { label: "At risk", value: counts.at_risk, signed: counts.at_risk > 0 ? "loss" : undefined },
        {
          label: "Reviews overdue",
          value: overdue,
          note: overdue > 0 ? "Past their review date" : "All reviews on time",
        },
      ]}
    />
  );
}

function Column({ state, count, children }: { state: ThesisState; count: number; children: React.ReactNode }) {
  const id = `column-${state}`;
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 pb-1">
        <span aria-hidden="true" className={cn("size-2.5 rounded-full", DOT[state])} />
        <h2 id={id} className="panel-title">
          {THESIS_STATE[state].label}
        </h2>
        <span className="num rounded-full bg-surface-muted px-2 py-px text-xs text-text-muted">{count}</span>
        <span className="text-xs text-text-muted">{THESIS_STATE[state].note}</span>
      </div>
      {count === 0 ? (
        <p className="-mt-2 text-xs text-text-faint">No thesis is {THESIS_STATE[state].label.toLowerCase()}.</p>
      ) : (
        <div className="grid items-start gap-3.5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">{children}</div>
      )}
    </section>
  );
}

/** Above the cards: where the theses are weakest, and the verdicts recorded over 30 days. */
function StatRow({ board }: { board: ThesisBoard }) {
  const weak = weakest(board.rows);
  const perDay = board.verdicts_30d.per_day.map((d) => d.count);

  return (
    <div className="grid items-start gap-5 md:grid-cols-[1.6fr_1fr]">
      <Blueprint className="flex flex-col gap-2 px-5 py-[18px]">
        <h2 className="panel-title">Where the theses are weakest</h2>
        {weak.length === 0 ? (
          <p className="text-sm text-text-muted">
            Every scored thesis is at 100: each of its KPIs passes the latest reported figure. A score this uniform usually means the KPI limits sit far from today&apos;s figures; tighten
            them in{" "}
            <Link href="/config/theses" className={LINK}>
              Edit theses
            </Link>{" "}
            so a weakening business shows here before the price does.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {weak.map(({ row, reason }) => (
              <li key={row.ticker} className="grid grid-cols-[minmax(0,10rem)_3rem_minmax(0,1fr)] items-center gap-3 text-sm">
                <Link href={`/companies/${encodeURIComponent(row.ticker)}`} className="truncate hover:text-accent-ink hover:underline">
                  <EntityName id={row.ticker} name={row.name} chip={false} />
                </Link>
                <span className="num text-right font-semibold">{Math.round(row.score ?? 0)}</span>
                <span className="flex min-w-0 items-center gap-2">
                  <span aria-hidden="true" className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-surface-muted">
                    <span className={cn("block h-full rounded-full", DOT[row.state])} style={{ width: `${Math.max(4, row.score ?? 0)}%` }} />
                  </span>
                  <span className="truncate text-xs text-text-muted" title={reason}>
                    {reason}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Blueprint>

      <Blueprint className="flex flex-col gap-1.5 px-5 py-[18px]">
        <h2 className="panel-title">Verdicts, last 30 days</h2>
        <p className="font-heading text-[32px] leading-none font-semibold">
          <span className="num">{board.verdicts_30d.count}</span>{" "}
          <span className="font-sans text-sm font-normal text-text-muted">
            from <span className="num">{board.verdicts_30d.fact_count}</span> reported figure
            {board.verdicts_30d.fact_count === 1 ? "" : "s"}
          </span>
        </p>
        <p className="text-xs text-text-muted">
          A verdict is one KPI checked against one reported figure. A new one is recorded only when the outcome, the figure or the thesis changed, so a quiet month means nothing moved.
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
