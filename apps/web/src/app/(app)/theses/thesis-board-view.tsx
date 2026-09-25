"use client";

import {
  ArrowRightIcon,
  CalendarClockIcon,
  ClockIcon,
  FileSearchIcon,
  HistoryIcon,
  InboxIcon,
  LoaderCircleIcon,
  MinusIcon,
  PencilIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type BoardKpi, type BoardRow, type ThesisBoard } from "@/lib/api/client";
import {
  boardSummary,
  kpiCellLabel,
  kpiLabel,
  kpiPanelId,
  NEVER_JUDGED,
  observedVsTarget,
  OVERALL_STATUS,
  statusCounts,
  type OverallStatus,
} from "@/lib/evidence/board";
import { formatTarget } from "@/lib/evidence/confidence";
import { reviewQueueHref } from "@/lib/evidence/review";
import { formatDate, formatDateTime, formatPercent, formatRelative } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

const fetchBoard = () => getBrowserApiClient().GET("/theses/board");

export function ThesisBoardView() {
  const { state, reload } = useApiResource(fetchBoard);
  const board = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Thesis board</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Every holding with a thesis, worst first. Open any KPI status for its verdict and the evidence behind it.
            Unknown means no verified figure yet; it is never left out.
          </p>
        </div>
        {board ? <VerdictsAsOf asOf={board.as_of} /> : null}
      </div>
      <HoldingsTabs />

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

function OverallBadge({ overall }: { overall: OverallStatus }) {
  if (overall !== "no_kpis") return <VerdictStatusBadge status={overall} />;
  return (
    <span
      title={OVERALL_STATUS.no_kpis.description}
      className="inline-flex items-center gap-1 rounded-sm border border-dashed border-border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-text-muted"
    >
      <MinusIcon aria-hidden="true" className="size-3.5" />
      {OVERALL_STATUS.no_kpis.label}
    </span>
  );
}

type Open = { ticker: string; kpiId: string } | null;

function Board({ board }: { board: ThesisBoard }) {
  const [open, setOpen] = useState<Open>(null);

  if (board.rows.length === 0) {
    return (
      <EmptyState title="No theses configured">
        Write why you own each holding and the KPIs that must hold in the{" "}
        <Link className="text-accent underline-offset-4 hover:underline" href="/config/theses">
          theses editor
        </Link>
        . Saving queues a verdict run, and every holding with a thesis appears here.
      </EmptyState>
    );
  }

  const summary = boardSummary(board.rows);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <p className="num">
          <span className="font-medium">
            {summary.holdings} holding{summary.holdings === 1 ? "" : "s"}
          </span>
          <span className="text-text-muted">
            {" "}
            · {summary.byOverall.breach} breach · {summary.byOverall.unknown} unknown · {summary.byOverall.pass} pass
            {summary.byOverall.no_kpis > 0 ? ` · ${summary.byOverall.no_kpis} without KPIs` : ""}
          </span>
        </p>
        {summary.openReviewItems > 0 ? (
          <Link href="/review" className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline">
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
        <Link
          href="/config/theses"
          className="ml-auto inline-flex items-center gap-1 text-text-muted hover:text-text hover:underline"
        >
          <PencilIcon aria-hidden="true" className="size-3.5" />
          Edit theses
        </Link>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
        <table className="w-full min-w-[60rem] text-sm">
          <caption className="sr-only">
            Thesis status per holding, breaches first. Each KPI button opens its verdict and evidence below the row.
          </caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-text-muted">
              <th scope="col" className="px-3 py-2 text-left font-medium">Holding</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Overall</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Breach</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Unknown</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Pass</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">KPIs</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Review</th>
            </tr>
          </thead>
          <tbody>
            {board.rows.map((row) => {
              const openCell = open?.ticker === row.ticker ? row.kpis.find((k) => k.kpi.id === open.kpiId) : undefined;
              return (
                <Fragment key={row.ticker}>
                  <BoardRowView
                    row={row}
                    openKpiId={openCell?.kpi.id ?? null}
                    onToggle={(kpiId) =>
                      setOpen((o) => (o?.ticker === row.ticker && o.kpiId === kpiId ? null : { ticker: row.ticker, kpiId }))
                    }
                  />
                  {openCell ? (
                    <tr className="border-b border-border bg-surface-muted/60">
                      <td colSpan={7} id={kpiPanelId(row.ticker, openCell.kpi.id)} className="px-4 py-3">
                        <VerdictPanel row={row} cell={openCell} />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BoardRowView({
  row,
  openKpiId,
  onToggle,
}: {
  row: BoardRow;
  openKpiId: string | null;
  onToggle: (kpiId: string) => void;
}) {
  const counts = statusCounts(row);
  return (
    <tr className="border-b border-border align-top last:border-0">
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <Link href={`/holdings/${encodeURIComponent(row.ticker)}`} className="group block rounded-sm">
          <span className="block font-medium text-text group-hover:text-accent group-hover:underline">
            {row.name ?? row.ticker}
          </span>
          <span className="block font-mono text-xs text-text-muted">{row.ticker}</span>
        </Link>
        <span className="num block text-xs text-text-muted">
          {row.held ? `weight ${formatPercent(row.weight)}` : "Not held"}
        </span>
        {row.why_owned ? (
          <span className="mt-1 line-clamp-2 block max-w-xs text-xs text-text-muted" title={row.why_owned}>
            {row.why_owned}
          </span>
        ) : null}
      </th>
      <td className="px-3 py-2">
        <OverallBadge overall={row.overall} />
      </td>
      <td className="num px-3 py-2 text-right">{counts.breach}</td>
      <td className="num px-3 py-2 text-right">{counts.unknown}</td>
      <td className="num px-3 py-2 text-right">{counts.pass}</td>
      <td className="px-3 py-2">
        {row.kpis.length === 0 ? (
          <span className="text-text-muted">No KPIs in this thesis</span>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {row.kpis.map((cell) => (
              <li key={cell.kpi.id}>
                <KpiCell
                  ticker={row.ticker}
                  cell={cell}
                  expanded={openKpiId === cell.kpi.id}
                  onToggle={() => onToggle(cell.kpi.id)}
                />
              </li>
            ))}
          </ul>
        )}
      </td>
      <td className="px-3 py-2 text-xs">
        <ReviewCell row={row} />
      </td>
    </tr>
  );
}

function KpiCell({
  ticker,
  cell,
  expanded,
  onToggle,
}: {
  ticker: string;
  cell: BoardKpi;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={expanded ? kpiPanelId(ticker, cell.kpi.id) : undefined}
      aria-label={kpiCellLabel(cell)}
      title={cell.kpi.description || undefined}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-1.5 py-1 text-left text-xs hover:bg-surface-muted",
        expanded ? "border-accent bg-surface-muted" : "border-border",
        cell.verdict === null && "border-dashed",
      )}
    >
      <VerdictStatusBadge status={cell.status} />
      <span>
        <span className="block font-medium text-text">{kpiLabel(cell.kpi)}</span>
        <span className="num block text-text-muted">
          {formatTarget(cell.kpi.operator, cell.kpi.threshold, cell.kpi.unit)}
          {cell.verdict === null ? " · never judged" : null}
          {cell.verdict?.stale ? " · stale" : null}
        </span>
      </span>
    </button>
  );
}

function ReviewCell({ row }: { row: BoardRow }) {
  return (
    <div className="space-y-1">
      {row.review_date ? (
        row.review_overdue ? (
          <span className="inline-flex items-center gap-1 rounded-sm border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 font-medium whitespace-nowrap text-text">
            <CalendarClockIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
            Review overdue <span className="num">(since {formatDate(row.review_date)})</span>
          </span>
        ) : (
          <span className="num block text-text-muted">Review by {formatDate(row.review_date)}</span>
        )
      ) : (
        <span className="block text-text-muted">No review date</span>
      )}
      {row.open_review_items > 0 ? (
        <Link
          href={reviewQueueHref(row.ticker)}
          className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline"
        >
          <InboxIcon aria-hidden="true" className="size-3.5" />
          <span className="num">{row.open_review_items}</span> figure{row.open_review_items === 1 ? "" : "s"} to review
        </Link>
      ) : (
        <span className="block text-text-muted">No figure held</span>
      )}
    </div>
  );
}

function VerdictPanel({ row, cell }: { row: BoardRow; cell: BoardKpi }) {
  const { kpi, verdict } = cell;
  const { observed, target } = observedVsTarget(cell);
  const holdingHref = `/holdings/${encodeURIComponent(row.ticker)}`;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        <p className="font-semibold">
          {kpiLabel(kpi)} · {row.name ?? row.ticker}
        </p>
        <span className="font-mono text-xs text-text-muted">
          {kpi.id} · {kpi.period} · {kpi.basis}
        </span>
      </div>
      {kpi.description ? <p className="text-text-muted">{kpi.description}</p> : null}

      {verdict === null ? (
        <div className="space-y-2">
          <p className="flex flex-wrap items-center gap-2">
            <VerdictStatusBadge status="unknown" />
            <span className="font-medium">{NEVER_JUDGED}</span>
          </p>
          <p className="text-text-muted">
            Target <span className="num">{target}</span>. Run an earnings check from the{" "}
            <Link href={holdingHref} className="text-accent underline-offset-4 hover:underline">
              holding page
            </Link>{" "}
            to read the latest results.
            {row.open_review_items > 0 ? (
              <>
                {" "}
                A held figure is used in no verdict until you accept it:{" "}
                <Link href={reviewQueueHref(row.ticker)} className="text-accent underline-offset-4 hover:underline">
                  review {row.open_review_items} held figure{row.open_review_items === 1 ? "" : "s"}
                </Link>
                .
              </>
            ) : null}
          </p>
        </div>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
            <div>
              <dt className="text-xs text-text-muted">Verdict</dt>
              <dd className="mt-0.5">
                <VerdictStatusBadge status={verdict.status} />
              </dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Observed</dt>
              <dd className="num mt-0.5 font-medium">{observed}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Threshold</dt>
              <dd className="num mt-0.5">{target}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Period</dt>
              <dd className="mt-0.5">{verdict.period ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Confidence</dt>
              <dd className="mt-0.5">
                <ConfidenceBadge confidence={verdict.confidence} />
              </dd>
            </div>
            <div>
              <dt className="text-xs text-text-muted">Judged</dt>
              <dd className="num mt-0.5">
                <time dateTime={verdict.created_at}>{formatDateTime(verdict.created_at)}</time>
              </dd>
            </div>
          </dl>
          {verdict.stale ? (
            <p className="flex items-center gap-1.5 text-text-muted">
              <HistoryIcon aria-hidden="true" className="size-4 text-warning-signal" />
              <span>
                <span className="font-medium text-text">Stale:</span> judged under an older theses config. Re-run verdicts
                from the holding page.
              </span>
            </p>
          ) : null}
          <p>{verdict.rationale}</p>
          <div className="space-y-1">
            <p className="text-xs font-medium tracking-wide text-text-muted uppercase">Evidence</p>
            {verdict.fact_ids.length === 0 ? (
              <p className="text-text-muted">No verified figure behind this verdict.</p>
            ) : (
              <ul className="flex flex-wrap gap-3">
                {verdict.fact_ids.map((id) => (
                  <li key={id}>
                    <Link
                      href={`/evidence/${id}`}
                      className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline"
                    >
                      <FileSearchIcon aria-hidden="true" className="size-4" />
                      Figure #{id}: snippet and source
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      <Link href={holdingHref} className="inline-flex items-center gap-1 text-accent underline-offset-4 hover:underline">
        KPI history on the holding page <ArrowRightIcon aria-hidden="true" className="size-3.5" />
      </Link>
    </div>
  );
}
