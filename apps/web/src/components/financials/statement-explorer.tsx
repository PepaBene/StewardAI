"use client";

import { useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatCompact, formatNumber } from "@/lib/charts/format";
import { knownDates, linePoints, restatements } from "@/lib/financials/logic";
import { STATEMENT_LABELS, type StatementKind, type StatementSet, type VersionMode } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";

import { LineItemBars } from "./line-item-bars";

const KINDS = (Object.keys(STATEMENT_LABELS) as StatementKind[]).map((id) => ({ id, label: STATEMENT_LABELS[id] }));
type ModeId = "latest" | "original" | "as_of";

function formatLine(unit: string): (v: number) => string {
  return unit.includes("/share") ? (v) => formatNumber(v, 2) : (v) => formatCompact(v);
}

/**
 * As-filed financial explorer: every statement line as a small multiple across fiscal years,
 * with a version selector (latest, as first filed, or as known on a filing date). A restated
 * value is marked and listed; an old as-of view never shows a value filed later.
 */
export function StatementExplorer({ set }: { set: StatementSet }) {
  const [kind, setKind] = useState<StatementKind>("income");
  const [modeId, setModeId] = useState<ModeId>("latest");
  const dates = knownDates(set.lines);
  const [asOf, setAsOf] = useState(dates[Math.min(2, dates.length - 1)]);
  const mode: VersionMode = modeId === "as_of" ? { kind: "as_of", date: asOf } : { kind: modeId };
  const lines = set.lines.filter((l) => l.statement === kind);
  const restated = lines.flatMap((l) => restatements(l).map((r) => ({ line: l, ...r })));
  const shownRestated = modeId === "latest" ? restated : [];

  return (
    <ChartFrame
      id="statement-explorer"
      kicker={`${set.basis} · ${set.fiscalYearNote}`}
      title={`${set.company}: ${STATEMENT_LABELS[kind].toLowerCase()} as filed`}
      takeaway={
        modeId === "latest"
          ? restated.length > 0
            ? `${restated.length} value${restated.length === 1 ? "" : "s"} in this statement were restated after first filing; the latest figures are shown.`
            : "No value in this statement was restated after first filing."
          : modeId === "original"
            ? "Each value is shown as first filed, before any later restatement."
            : `Each value is shown as known on ${formatDate(asOf)}; periods filed after that date are empty.`
      }
      units={`${set.currency} ${set.scale}, per-share lines in ${set.currency} per share; ${set.basis}; no figure here is adjusted or non-GAAP`}
      controls={
        <>
          <Segmented label="Statement" options={KINDS} value={kind} onChange={setKind} />
          <Segmented
            label="Version"
            value={modeId}
            onChange={setModeId}
            options={[
              { id: "latest", label: "Latest" },
              { id: "original", label: "As first filed" },
              { id: "as_of", label: "As known on…" },
            ]}
          />
          {modeId === "as_of" ? (
            <label className="flex items-center gap-1 text-xs text-text-muted">
              <span className="sr-only">Known on</span>
              <select value={asOf} onChange={(e) => setAsOf(e.target.value)} className="border border-border bg-surface-raised px-2 py-1 text-xs text-text">
                {dates.map((d) => (
                  <option key={d} value={d}>
                    {formatDate(d)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </>
      }
      provenance={{
        source: set.source,
        sourceUrl: set.sourceUrl,
        asOf: lines[0]?.periods[lines[0].periods.length - 1]?.end ?? null,
        knownAt: modeId === "as_of" ? asOf : null,
        method: "Values from filed XBRL statements; each restatement is a new version, never an overwrite",
        state: set.state,
        reason: set.reason,
      }}
      table={{
        caption: `${STATEMENT_LABELS[kind]}, ${modeId === "as_of" ? `as known on ${asOf}` : modeId === "original" ? "as first filed" : "latest"}`,
        columns: ["Line", ...(lines[0]?.periods.map((p) => p.period) ?? [])],
        rows: lines.map((l) => [
          `${l.label} (${l.unit})`,
          ...linePoints(l, mode).map((p) => (p.value === null ? "not filed" : `${formatLine(l.unit)(p.value)}${p.restated ? " R" : ""}`)),
        ]),
      }}
    >
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
        {lines.map((l) => (
          <li key={l.id} className="flex flex-col gap-1 border border-border-soft p-3">
            <span className="flex items-baseline justify-between gap-2">
              <span className="font-medium">{l.label}</span>
              <span className="text-xs text-text-muted">{l.unit}</span>
            </span>
            <LineItemBars points={linePoints(l, mode)} unit={l.unit} label={l.label} format={formatLine(l.unit)} />
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
          <i aria-hidden="true" className="inline-block h-2.5 w-2.5 border border-dashed border-border" /> Not filed under this view
        </span>
      </p>
      {shownRestated.length > 0 ? (
        <section aria-labelledby="restatements-heading" className="flex flex-col gap-1.5">
          <h3 id="restatements-heading" className="font-heading text-lg font-semibold">
            Restatements in this statement
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {shownRestated.map((r) => (
              <li key={`${r.line.id}-${r.period}`} className="num border-b border-border-soft pb-1">
                <span className="font-medium">{r.line.label}</span> {r.period}: {formatLine(r.line.unit)(r.original)} → {formatLine(r.line.unit)(r.latest)} (
                {r.delta > 0 ? "+" : "−"}
                {formatLine(r.line.unit)(Math.abs(r.delta))}), filed {formatDate(r.filedAt)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </ChartFrame>
  );
}
