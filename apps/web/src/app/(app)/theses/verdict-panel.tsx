"use client";

import { ArrowRightIcon, FileSearchIcon, HistoryIcon, XIcon } from "lucide-react";
import Link from "next/link";

import { AskAboutSelection } from "@/components/chat/ask-about-selection";
import { ConfidenceBadge } from "@/components/evidence/confidence-badge";
import { VerdictStatusBadge } from "@/components/evidence/verdict-status-badge";
import { FeedbackControl } from "@/components/feedback/feedback-control";
import { Button } from "@/components/ui/button";
import type { BoardKpi, BoardRow } from "@/lib/api/client";
import { kpiLabel, kpiPanelId, NEVER_JUDGED, observedVsTarget } from "@/lib/evidence/board";
import { reviewQueueHref } from "@/lib/evidence/review";
import { formatDateTime } from "@/lib/format";

const LINK = "text-accent-ink underline-offset-4 hover:underline";

/** One KPI's verdict and the evidence behind it, opened in place from a thesis card. */
export function VerdictPanel({ row, cell, onClose }: { row: BoardRow; cell: BoardKpi; onClose: () => void }) {
  const { kpi, verdict } = cell;
  const { observed, target } = observedVsTarget(cell);
  const holdingHref = `/companies/${encodeURIComponent(row.ticker)}`;
  return (
    <div
      id={kpiPanelId(row.ticker, kpi.id)}
      role="region"
      aria-label={`Verdict on ${kpiLabel(kpi)} for ${row.name ?? row.ticker}`}
      className="@container space-y-3 border-t border-border bg-surface-muted/60 px-4 py-3 text-sm"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold">{kpiLabel(kpi)}</p>
          <p className="font-mono text-xs break-all text-text-muted">
            {kpi.id} · {kpi.period} · {kpi.basis}
          </p>
        </div>
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="Close the verdict">
          <XIcon aria-hidden="true" />
        </Button>
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
            <Link href={holdingHref} className={LINK}>
              holding page
            </Link>{" "}
            to read the latest results.
            {row.open_review_items > 0 ? (
              <>
                {" "}
                A held figure is used in no verdict until you accept it:{" "}
                <Link href={reviewQueueHref(row.ticker)} className={LINK}>
                  review {row.open_review_items} held figure{row.open_review_items === 1 ? "" : "s"}
                </Link>
                .
              </>
            ) : null}
          </p>
        </div>
      ) : (
        <>
          <AskAboutSelection className="space-y-2">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 @lg:grid-cols-3">
              <div>
                <dt className="text-xs text-text-muted">Verdict</dt>
                <dd className="mt-0.5 flex flex-wrap items-center gap-1">
                  <VerdictStatusBadge status={verdict.status} />
                  <FeedbackControl
                    target={{ kind: "verdict", id: verdict.id }}
                    label={`verdict on ${kpiLabel(kpi)} for ${row.name ?? row.ticker}`}
                  />
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
                  <ConfidenceBadge confidence={verdict.confidence} since={verdict.created_at} />
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
              <p className="flex items-start gap-1.5 text-text-muted">
                <HistoryIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
                <span>
                  <span className="font-medium text-text">Stale:</span> judged under an older theses config. Re-run verdicts
                  from the holding page.
                </span>
              </p>
            ) : null}
            <p>{verdict.rationale}</p>
          </AskAboutSelection>
          <div className="space-y-1">
            <p className="kicker">Evidence</p>
            {verdict.fact_ids.length === 0 ? (
              <p className="text-text-muted">No verified figure behind this verdict.</p>
            ) : (
              <ul className="flex flex-wrap gap-x-3 gap-y-1">
                {verdict.fact_ids.map((id) => (
                  <li key={id}>
                    <Link href={`/evidence/${id}`} className={`inline-flex items-center gap-1 ${LINK}`}>
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

      <Link href={holdingHref} className={`inline-flex items-center gap-1 ${LINK}`}>
        KPI history on the holding page <ArrowRightIcon aria-hidden="true" className="size-3.5" />
      </Link>
    </div>
  );
}
