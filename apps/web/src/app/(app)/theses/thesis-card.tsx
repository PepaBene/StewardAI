"use client";

import { CalendarClockIcon, HelpCircleIcon, InboxIcon, PencilIcon, XIcon } from "lucide-react";
import Link from "next/link";

import { Meter, Ring, Sparkline, type ChartTone } from "@/components/charts";
import { ConfidenceBar } from "@/components/evidence/confidence-bar";
import { BlueprintCorners } from "@/components/ui/blueprint";
import type { BoardKpi, BoardRow } from "@/lib/api/client";
import {
  confidenceCounts,
  countsText,
  judgedMeta,
  kpiLabel,
  kpiMeter,
  kpiPanelId,
  latestRationale,
  OVERALL_STATUS,
  shortTicker,
  statusCounts,
  THESIS_STATE,
  type ThesisState,
} from "@/lib/evidence/board";
import { tallyConfidenceLabel } from "@/lib/evidence/confidence";
import { reviewQueueHref } from "@/lib/evidence/review";
import { formatDate, formatPercent, formatRelative } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { cn } from "@/lib/utils";

import { PositionRisk } from "./position-risk";
import { VerdictPanel } from "./verdict-panel";

export const STATE_TONE: Record<ThesisState, ChartTone> = {
  intact: "accent",
  watch: "warning",
  at_risk: "loss",
  unjudged: "muted",
};

const LINK = "text-accent-ink underline-offset-4 hover:underline";

/**
 * One holding's thesis on the board: score ring (coloured by state), one headroom meter per
 * KPI (each opens its verdict in place), the latest verdict's rationale, when it was judged
 * and the score's history. Weight, review date and open review items stay on the card;
 * why it is owned, the status counts and verdict confidence sit behind "Thesis and review".
 */
export function ThesisCard({
  row,
  openKpiId,
  onToggle,
}: {
  row: BoardRow;
  openKpiId: string | null;
  onToggle: (kpiId: string | null) => void;
}) {
  const now = useNow();
  const summary = latestRationale(row);
  const { lastJudged, factCount } = judgedMeta(row);
  const open = openKpiId ? row.kpis.find((c) => c.kpi.id === openKpiId) : undefined;
  const history = row.score_history.map((p) => p.score);
  const confidence = confidenceCounts(row);
  const name = row.name ?? row.ticker;
  const headingId = `thesis-${row.ticker}`.replace(/[^A-Za-z0-9_-]/g, "-");

  return (
    <article aria-labelledby={headingId} className="blueprint flex flex-col">
      <BlueprintCorners />
      <div className="flex flex-col gap-3 px-[18px] pt-4 pb-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 id={headingId} className="font-mono text-[13px] font-medium">
              <Link
                href={`/companies/${encodeURIComponent(row.ticker)}`}
                className="hover:text-accent-ink hover:underline"
                title={row.ticker}
              >
                {shortTicker(row.ticker)}
                <span className="sr-only"> ({name})</span>
              </Link>
            </h3>
            <p className="truncate text-xs text-text-muted" title={name}>
              {name}
            </p>
          </div>
          <Ring
            value={row.score}
            size={52}
            tone={STATE_TONE[row.state]}
            ariaLabel={`Thesis score, ${THESIS_STATE[row.state].label}`}
          />
        </div>

        {row.kpis.length === 0 ? (
          <p className="text-xs text-text-muted">
            No KPIs in this thesis.{" "}
            <Link href="/config/theses" className={LINK}>
              Add KPIs
            </Link>
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5" aria-label="KPIs; each opens its verdict">
            {row.kpis.map((cell) => (
              <li key={cell.kpi.id}>
                <KpiRow
                  ticker={row.ticker}
                  cell={cell}
                  expanded={open?.kpi.id === cell.kpi.id}
                  onToggle={() => onToggle(open?.kpi.id === cell.kpi.id ? null : cell.kpi.id)}
                />
              </li>
            ))}
          </ul>
        )}

        <p className="line-clamp-3 text-[13px] leading-snug" title={summary ?? undefined}>
          {summary ?? <span className="text-text-muted">No verdict yet: no KPI has a verified figure.</span>}
        </p>

        <CardFlags row={row} />
      </div>

      {open ? <VerdictPanel row={row} cell={open} onClose={() => onToggle(null)} /> : null}

      <details className="group border-t border-border-soft px-[18px] py-2 text-xs">
        <summary className="cursor-pointer text-text-muted select-none hover:text-text">Thesis and review</summary>
        <div className="mt-2 space-y-2">
          {row.why_owned ? <p className="text-text">{row.why_owned}</p> : null}
          <p className="num text-text-muted">
            {OVERALL_STATUS[row.overall].label} · {countsText(statusCounts(row))}
          </p>
          <div className="flex items-center gap-2 text-text-muted">
            <span>Confidence</span>
            <ConfidenceBar counts={confidence} label={tallyConfidenceLabel(confidence) || "No judged KPIs yet"} />
            <span>{tallyConfidenceLabel(confidence) || "no judged KPIs yet"}</span>
          </div>
          <p className="text-text-muted">
            {row.review_date ? (
              <>
                Review by <span className="num">{formatDate(row.review_date)}</span>
              </>
            ) : (
              "No review date"
            )}
            {" · "}
            <Link href={`/config/theses#${encodeURIComponent(row.ticker)}`} className={LINK}>
              <PencilIcon aria-hidden="true" className="mr-0.5 inline size-3" />
              Edit this thesis
            </Link>
          </p>
        </div>
      </details>

      {row.held ? <PositionRisk ticker={row.ticker} /> : null}

      <div className="mt-auto flex items-center justify-between gap-3 border-t border-border-soft px-[18px] py-2 text-xs text-text-muted">
        <span className="num">
          {lastJudged ? (
            <>
              Judged <time dateTime={lastJudged}>{formatRelative(new Date(lastJudged), now)}</time> · {factCount} fact
              {factCount === 1 ? "" : "s"}
            </>
          ) : (
            "Never judged"
          )}
        </span>
        <span className="w-[70px] shrink-0">
          <Sparkline values={history} height={18} className="h-[18px]" label={`${shortTicker(row.ticker)} score history`} formatValue={(v) => String(v)} />
        </span>
      </div>
    </article>
  );
}

function KpiRow({
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
  const meter = kpiMeter(cell);
  const never = cell.verdict === null;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-controls={expanded ? kpiPanelId(ticker, cell.kpi.id) : undefined}
      title={cell.kpi.description || undefined}
      className={cn(
        "grid w-full grid-cols-[minmax(0,1fr)_80px] items-center gap-2.5 px-1 py-1 text-left text-xs hover:bg-surface-muted",
        expanded && "bg-accent-soft",
      )}
    >
      <span className="flex min-w-0 items-center gap-1 text-text-muted">
        {cell.status === "breach" ? <XIcon aria-hidden="true" className="size-3 shrink-0 text-loss" /> : null}
        {cell.status === "unknown" ? <HelpCircleIcon aria-hidden="true" className="size-3 shrink-0" /> : null}
        <span className="truncate">{kpiLabel(cell.kpi)}</span>
        {never ? <span className="shrink-0 text-text-faint">· never judged</span> : null}
        {cell.verdict?.stale ? <span className="shrink-0 text-text-faint">· stale</span> : null}
      </span>
      <Meter
        value={meter.fill}
        target={0.5}
        tone={meter.breach ? "loss" : cell.status === "unknown" ? "muted" : "accent"}
        label={meter.label}
        formatValue={(v) => (v === 0.5 ? "the threshold (mid-scale)" : `headroom ${Math.round(v * 100)} of 100`)}
      />
    </button>
  );
}

function CardFlags({ row }: { row: BoardRow }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
      <span className="num">{row.held ? `Weight ${formatPercent(row.weight)}` : "Not held"}</span>
      {row.review_overdue && row.review_date ? (
        <span className="inline-flex items-center gap-1 border border-warning-signal/40 bg-warning-signal-subtle px-1.5 py-0.5 font-medium text-text">
          <CalendarClockIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
          Review overdue <span className="num">(since {formatDate(row.review_date)})</span>
        </span>
      ) : null}
      {row.open_review_items > 0 ? (
        <Link href={reviewQueueHref(row.ticker)} className={`inline-flex items-center gap-1 ${LINK}`}>
          <InboxIcon aria-hidden="true" className="size-3.5" />
          <span className="num">{row.open_review_items}</span> figure{row.open_review_items === 1 ? "" : "s"} to review
        </Link>
      ) : null}
    </div>
  );
}
