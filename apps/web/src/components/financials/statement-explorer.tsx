"use client";

import { useMemo, useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/financials/format";
import { buildStatement, restatements, linePoints, statementState } from "@/lib/financials/logic";
import { STATEMENT_LABELS, type ParsedFacts, type PeriodBasis, type StatementKind, type VersionMode } from "@/lib/financials/types";

import { LineItemBars } from "./line-item-bars";

const KINDS = (Object.keys(STATEMENT_LABELS) as StatementKind[]).map((id) => ({ id, label: STATEMENT_LABELS[id] }));
const BASES = [
  { id: "annual", label: "Annual" },
  { id: "quarter", label: "Quarterly" },
] as const satisfies readonly { id: PeriodBasis; label: string }[];
type ModeId = VersionMode["kind"];

/**
 * As-filed statement explorer built from the stored XBRL facts: every line as a small
 * multiple across fiscal periods, with a version selector (latest or as first filed). The
 * point-in-time cut-off is the page's "as known on" date, which the API applies (`as_of`):
 * nothing filed later is ever fetched, let alone filtered client-side. A restated value is
 * marked and listed; a period with no filed value is an empty slot, never a zero.
 */
export function StatementExplorer({ name, facts, asOf }: { name: string; facts: ParsedFacts; asOf: string | null }) {
  const [kind, setKind] = useState<StatementKind>("income");
  const [basis, setBasis] = useState<PeriodBasis>("annual");
  const [modeId, setModeId] = useState<ModeId>("latest");
  const view = useMemo(() => buildStatement(facts.rows, kind, basis), [facts.rows, kind, basis]);
  const mode: VersionMode = { kind: modeId };
  const status = statementState(view, facts.dropped);
  const restated = view.lines.flatMap((l) => restatements(l).map((r) => ({ line: l, ...r })));
  const knownAt = facts.rows.map((f) => f.knownAt).sort().pop() ?? null;
  const sources = [...new Set(facts.rows.map((f) => f.source).filter((s): s is string => !!s))];
  const sourceUrl = facts.rows.find((f) => f.sourceUrl)?.sourceUrl ?? null;
  const lastEnd = view.periods[view.periods.length - 1]?.end ?? null;
  const bases = [...new Set(view.lines.map((l) => facts.rows.find((f) => f.lineItem === l.id && f.concept === l.concept)?.basis).filter((b): b is string => !!b))];

  return (
    <ChartFrame
      id="statement-explorer"
      kicker={`${bases.length ? bases.join(" / ").replaceAll("_", " ") : "Filed statements"} · ${basis === "annual" ? "annual" : "quarterly"} periods`}
      title={`${name}: ${STATEMENT_LABELS[kind].toLowerCase()} as filed`}
      takeaway={
        view.lines.length === 0
          ? "No filed value is stored for this statement and period type."
          : modeId === "original"
            ? "Each value is shown as first filed, before any later restatement."
            : restated.length > 0
              ? `${restated.length} value${restated.length === 1 ? "" : "s"} in this statement were restated after first filing; the latest figures are shown.`
              : "No value in this statement was restated after first filing."
      }
      units={`${view.lines.length} lines · ${view.periods.length} periods · per-share lines in currency per share; no figure is adjusted or non-GAAP${asOf ? ` · as known on ${formatDate(asOf)}` : ""}`}
      hasValue={view.lines.length > 0}
      controls={
        <>
          <Segmented label="Statement" options={KINDS} value={kind} onChange={setKind} />
          <Segmented label="Period type" options={BASES} value={basis} onChange={setBasis} />
          <Segmented
            label="Version"
            value={modeId}
            onChange={setModeId}
            options={[
              { id: "latest", label: "Latest" },
              { id: "original", label: "As first filed" },
            ]}
          />
        </>
      }
      provenance={{
        source: sources.length ? sources.join(" · ") : "Stored XBRL statement facts",
        sourceUrl,
        asOf: lastEnd,
        knownAt,
        method: "Facts from filed XBRL; each restatement is a new version, never an overwrite. Facts that failed a reconciliation check are left out.",
        state: status.state,
        reason: status.reason,
        coverage: view.counts.total > 0 ? { covered: view.counts.reconciled, total: view.counts.total, unit: "facts reconciled" } : null,
      }}
      table={{
        caption: `${STATEMENT_LABELS[kind]}, ${basis === "annual" ? "annual" : "quarterly"}, ${modeId === "original" ? "as first filed" : "latest"}`,
        columns: ["Line", ...view.periods.map((p) => p.label)],
        rows: view.lines.map((l) => [
          `${l.label} (${l.unit})`,
          ...linePoints(l, mode).map((p) => (p.value === null ? "not filed" : `${formatAmount(p.value, l.unit)}${p.restated ? " R" : ""}`)),
        ]),
      }}
    >
      <ul className="m-0 grid min-w-0 list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
        {view.lines.map((l) => (
          <li key={l.id} className="flex min-w-0 flex-col gap-1 border border-border-soft p-3">
            <span className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate font-medium" title={l.concept}>
                {l.label}
              </span>
              <span className="shrink-0 text-xs text-text-muted">{l.unit}</span>
            </span>
            <LineItemBars points={linePoints(l, mode)} unit={l.unit} label={l.label} format={(v) => formatAmount(v, l.unit)} />
          </li>
        ))}
      </ul>
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 bg-accent" /> Value as filed
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 border-2 border-accent bg-accent-soft" /> R: restated value
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block h-2.5 w-2.5 border border-dashed border-border" /> Not filed
        </span>
      </p>
      {restated.length > 0 && modeId === "latest" ? (
        <section aria-labelledby="restatements-heading" className="flex flex-col gap-1.5">
          <h3 id="restatements-heading" className="font-heading text-lg font-semibold">
            Restatements in this statement
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {restated.map((r) => (
              <li key={`${r.line.id}-${r.period}`} className="num border-b border-border-soft pb-1">
                <span className="font-medium">{r.line.label}</span> {r.period}: {formatAmount(r.original, r.line.unit)} → {formatAmount(r.latest, r.line.unit)} ({r.delta > 0 ? "+" : "−"}
                {formatAmount(Math.abs(r.delta), r.line.unit)}), filed {formatDate(r.filedAt.slice(0, 10))}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </ChartFrame>
  );
}
