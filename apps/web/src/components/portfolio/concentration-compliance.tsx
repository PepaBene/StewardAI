"use client";

import { CheckIcon, CircleHelpIcon, TriangleAlertIcon } from "lucide-react";
import Link from "next/link";

import { Gauge, Ring } from "@/components/charts";
import { BlueprintCorners, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type DriftReport } from "@/lib/api/client";
import { formatNumber } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { effectiveHoldings, herfindahlIndex } from "@/lib/portfolio/concentration";
import { complianceRows, thesisRow, type ComplianceRow } from "@/lib/portfolio/rule-compliance";
import { cn } from "@/lib/utils";

const fetchStrategy = () => getBrowserApiClient().GET("/config/strategy");
const fetchBoard = () => getBrowserApiClient().GET("/theses/board");

const STATUS = {
  pass: { Icon: CheckIcon, text: "Pass", tone: "accent" as const, cls: "text-text-muted" },
  breach: { Icon: TriangleAlertIcon, text: "Breach", tone: "warning" as const, cls: "text-text" },
  unknown: { Icon: CircleHelpIcon, text: "Unknown", tone: "muted" as const, cls: "text-text-muted" },
};

function RuleItem({ row }: { row: ComplianceRow }) {
  const s = STATUS[row.status];
  return (
    <li className="flex items-center gap-2.5">
      <Ring
        value={row.fraction === null ? null : row.fraction * 100}
        label=""
        size={34}
        tone={s.tone}
        ariaLabel={`${row.label}: checks passed`}
        formatValue={(v) => `${formatNumber(v, 0)}%`}
      />
      <div className="min-w-0">
        <div className="text-[13px] leading-tight font-medium">{row.label}</div>
        <div className={cn("flex items-center gap-1 text-xs", s.cls)}>
          <s.Icon aria-hidden="true" className={cn("size-3", row.status === "breach" && "text-warning-signal")} />
          <span className="sr-only">{s.text}: </span>
          <span className="truncate">{row.detail}</span>
        </div>
      </div>
    </li>
  );
}

/**
 * Two halves of one frame: concentration as effective holdings on a gauge (a fact, not a
 * judgement), and strategy compliance — position and cash bands, every enabled rule
 * evaluated against today's drift, and thesis KPIs from the thesis board.
 */
export function ConcentrationCompliance({
  positions,
  drift,
  hasStrategy,
}: {
  /** Invested position values (cash excluded). */
  positions: readonly number[];
  drift: DriftReport | null;
  hasStrategy: boolean;
}) {
  const strategy = useApiResource(fetchStrategy).state;
  const board = useApiResource(fetchBoard).state;

  const invested = positions.reduce((s, v) => s + v, 0);
  const weights = invested > 0 ? positions.map((v) => v / invested) : positions.map(() => 0);
  const hhi = herfindahlIndex(weights);
  const effective = effectiveHoldings(hhi);
  const n = positions.length;

  const strategyBody = strategy.kind === "ok" ? strategy.data.body : strategy.kind === "error" ? strategy.previous?.body ?? null : null;
  const rows: ComplianceRow[] = drift && hasStrategy ? complianceRows(drift, strategyBody) : [];
  const boardRows = board.kind === "ok" ? board.data.rows : board.kind === "error" ? board.previous?.rows ?? [] : [];
  const thesis = thesisRow(boardRows);
  if (thesis) rows.push(thesis);

  return (
    <section className="blueprint grid min-w-0 grid-cols-1 gap-5 px-6 py-5.5 sm:grid-cols-2" aria-label="Concentration and strategy compliance">
      <BlueprintCorners />
      <div className="flex min-w-0 flex-col gap-2">
        <PanelHeading kicker="Concentration" title="Effective holdings" />
        {n > 0 ? (
          <>
            <Gauge
              value={effective}
              max={n}
              sublabel={`of ${n} position${n === 1 ? "" : "s"}`}
              minLabel="One position"
              maxLabel="Evenly spread"
              ariaLabel="Effective number of holdings"
              className="mx-auto w-full max-w-64"
            />
            <p className="text-xs leading-snug text-text-faint">
              As concentrated as {formatNumber(effective, 1)} equal positions (1 / Herfindahl index of invested weights, cash excluded). How spread
              out the positions are, not whether that&rsquo;s good or bad.
            </p>
          </>
        ) : (
          <p className="text-sm text-text-muted">No invested positions.</p>
        )}
      </div>
      <div className="flex min-w-0 flex-col gap-2.5 border-border sm:border-l sm:pl-5">
        <PanelHeading kicker="Strategy" title="Rule compliance" />
        {!hasStrategy ? (
          <p className="text-sm text-text-muted">
            No strategy saved yet.{" "}
            <Link href="/config/strategy" className="text-accent-ink hover:underline">
              Set rules and targets
            </Link>
            .
          </p>
        ) : null}
        {rows.length ? (
          <ul className="flex flex-col gap-2.5">
            {rows.map((r) => (
              <RuleItem key={r.id} row={r} />
            ))}
          </ul>
        ) : hasStrategy && strategy.kind === "loading" ? (
          <p className="text-sm text-text-muted" role="status">
            Loading rules…
          </p>
        ) : null}
        {strategy.kind === "error" && hasStrategy ? (
          <p className="text-xs text-text-muted">Strategy rules could not be loaded ({strategy.title}); bands shown from drift only.</p>
        ) : null}
        {rows.length ? (
          <p className="text-xs leading-snug text-text-faint">
            Rules evaluated against today&rsquo;s weights, as the monthly review does. Ring = share of checks passed.
          </p>
        ) : null}
      </div>
    </section>
  );
}
