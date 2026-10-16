"use client";

import { TriangleAlertIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Sparkline } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  latestChange,
  parseFinancialHistory,
  RECONCILIATION_LABEL,
  STATEMENTS,
  type LineSeries,
  type StatementKind,
} from "@/lib/reference/financial-history";
import { cn } from "@/lib/utils";

/** GET /holdings/{ticker}/financial-history: as-filed annual lines, one small series each. */
export function FinancialHistoryPanel({ ticker }: { ticker: string }) {
  const [statement, setStatement] = useState<StatementKind>("income");
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/holdings/{ticker}/financial-history", { params: { path: { ticker }, query: {} } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  const raw = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const all = useMemo(() => parseFinancialHistory(raw), [raw]);
  const present = STATEMENTS.filter((s) => all.some((l) => l.statement === s.id));
  const shown = present.some((s) => s.id === statement) ? statement : (present[0]?.id ?? statement);
  const lines = all.filter((l) => l.statement === shown);

  return (
    <Blueprint aria-labelledby="fin-history-heading" className="flex min-w-0 flex-col gap-3 px-[22px] py-5">
      <PanelHeading
        kicker="Financials · as filed"
        title={<span id="fin-history-heading">Financial history</span>}
        action={
          present.length > 1 ? (
            <div role="group" aria-label="Statement" className="inline-flex border border-border text-xs font-semibold">
              {present.map((s, i) => (
                <button
                  key={s.id}
                  type="button"
                  aria-pressed={shown === s.id}
                  onClick={() => setStatement(s.id)}
                  className={cn(
                    "px-2.5 py-1.5",
                    i > 0 && "border-l border-border",
                    shown === s.id ? "bg-accent text-accent-foreground" : "text-text hover:bg-surface-muted",
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          ) : null
        }
      />
      {state.kind === "loading" && !raw ? <p className="text-sm text-text-muted">Loading…</p> : null}
      {state.kind === "error" && !raw ? (
        <p className="text-sm text-text-muted">
          Financial history unavailable: {state.title}
          {state.detail ? `. ${state.detail}` : ""}
        </p>
      ) : null}
      {raw && all.length === 0 ? (
        <p className="max-w-[68ch] text-sm text-text-muted">
          No annual statement facts are stored for this holding. They come from SEC and ESEF filings, so a holding
          without either has no history here.
        </p>
      ) : null}

      {lines.length > 0 ? (
        <>
          <p className="max-w-[68ch] text-xs text-text-muted">
            Annual periods, latest filed revision of each. Currency and accounting basis are as filed; lines are never
            converted or adjusted.
          </p>
          <ul className="grid grid-cols-1 gap-px border border-border bg-border sm:grid-cols-2 xl:grid-cols-3">
            {lines.map((l) => (
              <LineCard key={`${l.key}${l.basis}${l.currency}`} line={l} />
            ))}
          </ul>
        </>
      ) : null}
    </Blueprint>
  );
}

function LineCard({ line }: { line: LineSeries }) {
  const last = line.points[line.points.length - 1];
  const change = latestChange(line);
  return (
    <li className="flex flex-col gap-1 bg-surface px-3 py-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-medium" title={line.label}>
          {line.label}
        </span>
        <span className="num text-xs text-text-muted">{last.periodEnd.slice(0, 4)}</span>
      </div>
      <div className="flex items-end justify-between gap-3">
        <div>
          <div className="num text-lg leading-tight">
            {line.currency ? `${line.currency} ` : ""}
            {formatCompact(last.value)}
          </div>
          <div className="num text-xs text-text-muted">
            {change === null ? "No prior year" : `${formatPct(change, 1, true)} on prior year`}
          </div>
        </div>
        <Sparkline
          values={line.points.map((p) => p.value)}
          tone="accent"
          width={96}
          height={32}
          className="h-8 w-24 shrink-0"
          label={`${line.label}, ${line.points.length} years`}
          formatValue={formatCompact}
        />
      </div>
      {line.reconciliation !== "reconciled" || line.restated ? (
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-muted">
          {line.reconciliation !== "reconciled" ? (
            <span className="inline-flex items-center gap-1">
              <TriangleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
              {RECONCILIATION_LABEL[line.reconciliation]}
            </span>
          ) : null}
          {line.restated ? <span>Restated: the latest filing replaced an earlier value</span> : null}
        </p>
      ) : null}
    </li>
  );
}
