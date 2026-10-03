"use client";

import { useMemo, useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatDate } from "@/lib/format";
import { decideEdge } from "@/lib/network/actions";
import {
  EDGE_GROUPS,
  GROUP_ORDER,
  countByStatus,
  directionLabel,
  edgeCoverage,
  edgeGroup,
  filterEdges,
  humanize,
  isFact,
  rowsState,
  validityBar,
  validityDomain,
  validityText,
  type EdgeGroup,
} from "@/lib/network/logic";
import type { NetworkEdge, RowStatus } from "@/lib/network/types";
import { cn } from "@/lib/utils";

import { RowStatusBadge, SourceLink, TierBadge, TIER_LABEL } from "./badges";
import { DecisionControl } from "./decision-control";

type Show = "facts" | "candidates" | "all";

const SHOW: Record<Show, ReadonlySet<RowStatus>> = {
  facts: new Set<RowStatus>(["confirmed"]),
  candidates: new Set<RowStatus>(["suggested", "disputed", "unrecognised"]),
  all: new Set<RowStatus>(["confirmed", "suggested", "disputed", "unrecognised", "rejected"]),
};

/** Validity bar styles: solid = confirmed fact; dashed outline = candidate; hatched-by-border = disputed. */
function barClass(status: RowStatus): string {
  switch (status) {
    case "confirmed":
      return "bg-accent";
    case "disputed":
      return "border border-disputed bg-disputed-subtle";
    case "rejected":
      return "border border-border bg-border-soft";
    default:
      return "border border-dashed border-text-muted bg-transparent";
  }
}

/**
 * The company graph as a time-aware ledger: one row per typed edge on a shared validity axis,
 * grouped so consolidation and control stay apart from ownership. Confirmed edges are solid;
 * suggested and disputed ones are dashed or outlined and labelled; selecting a row opens the
 * source passage and the owner's decision form.
 */
export function EdgeLedger({
  ticker,
  edges,
  asOfDate,
  limitations,
  knownAt,
  onChanged,
}: {
  ticker: string;
  edges: readonly NetworkEdge[];
  /** The as-of day (YYYY-MM-DD) or null for today: the right end of the axis. */
  asOfDate: string | null;
  limitations: readonly string[];
  knownAt: string | null;
  onChanged: () => void;
}) {
  const [show, setShow] = useState<Show>("all");
  const [groups, setGroups] = useState<ReadonlySet<EdgeGroup> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const counts = countByStatus(edges);
  const visible = useMemo(() => filterEdges(edges, { statuses: SHOW[show], groups }), [edges, show, groups]);
  const end = useMemo(() => (asOfDate ? new Date(`${asOfDate}T00:00:00Z`) : new Date()), [asOfDate]);
  const domain = useMemo(() => validityDomain(visible, end), [visible, end]);
  const present = GROUP_ORDER.filter((g) => edges.some((e) => edgeGroup(e.edgeType) === g));
  const chosen = visible.find((e) => e.id === selected) ?? null;

  const coverage = edgeCoverage(edges);
  const factCount = counts.confirmed;
  const takeaway =
    edges.length === 0
      ? "No relationships are recorded for this company at this date."
      : `${factCount} of ${coverage.total} live relationships are confirmed facts; ${counts.suggested + counts.unrecognised} are suggested candidates and ${counts.disputed} disputed${counts.rejected ? `, ${counts.rejected} rejected` : ""}.`;

  function toggleGroup(g: EdgeGroup) {
    const next = new Set(groups ?? present);
    if (next.has(g)) next.delete(g);
    else next.add(g);
    setGroups(next.size === present.length ? null : next);
  }

  return (
    <ChartFrame
      id="network-edges"
      kicker="Entity graph"
      title="Relationships and their validity"
      takeaway={takeaway}
      units={`One row per edge on a shared time axis (${domain ? `${formatDate(new Date(domain.from).toISOString().slice(0, 10))} to ${formatDate(end.toISOString().slice(0, 10))}` : "no edge has a date"}). Solid bar = confirmed fact; dashed outline = suggested candidate; shaded outline = disputed.`}
      provenance={{
        source: "Company graph edges (GLEIF, filings, Diffbot, Wikidata, owner review)",
        asOf: asOfDate,
        knownAt,
        method: "Status as in force at the as-of date; consolidation (parent, subsidiary) is never presented as ownership.",
        state: rowsState(edges),
        reason: edges.length === 0 ? "No edges stored for this holding yet." : null,
        coverage,
      }}
      hasValue={edges.length > 0}
      controls={
        <Segmented
          label="Which relationships"
          value={show}
          onChange={setShow}
          options={[
            { id: "facts", label: `Confirmed (${counts.confirmed})` },
            { id: "candidates", label: `Candidates (${counts.suggested + counts.disputed + counts.unrecognised})` },
            { id: "all", label: "All" },
          ]}
        />
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Relationship groups">
          {present.map((g) => {
            const on = groups === null || groups.has(g);
            return (
              <button
                key={g}
                type="button"
                aria-pressed={on}
                title={EDGE_GROUPS[g].note}
                onClick={() => toggleGroup(g)}
                className={cn(
                  "border px-2 py-0.5 text-xs outline-hidden focus-visible:outline-2 focus-visible:outline-focus",
                  on ? "border-accent bg-accent-soft text-text" : "border-border text-text-muted line-through",
                )}
              >
                {EDGE_GROUPS[g].label}
              </button>
            );
          })}
        </div>
        {visible.length === 0 ? (
          <p className="text-sm text-text-muted">No edge matches this filter. {edges.length > 0 ? "Widen the filter above." : null}</p>
        ) : (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[46rem] text-sm">
              <caption className="sr-only">Typed company-graph edges with status, evidence tier and validity dates</caption>
              <thead className="text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <tr className="border-b border-border text-left">
                  <th scope="col" className="px-2 py-1.5 font-medium">Relationship</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Counterparty</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Status</th>
                  <th scope="col" className="px-2 py-1.5 font-medium">Evidence</th>
                  <th scope="col" className="w-[28%] px-2 py-1.5 font-medium">Valid</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((e, i) => {
                  const prev = visible[i - 1];
                  const g = edgeGroup(e.edgeType);
                  const newGroup = !prev || edgeGroup(prev.edgeType) !== g;
                  const bar = validityBar(e, domain);
                  const isSel = e.id === selected;
                  return (
                    <GroupRows key={e.id} header={newGroup ? EDGE_GROUPS[g] : null}>
                      <tr className={cn("border-b border-border-soft align-top", isSel && "bg-accent-soft", !isFact(e.status) && "border-l-2 border-l-neutral-signal")}>
                        <td className="px-2 py-1.5">
                          <span className="font-medium">{humanize(e.edgeType)}</span>
                          <span className="block text-xs text-text-muted">{directionLabel(e.direction)}</span>
                        </td>
                        <th scope="row" className="px-2 py-1.5 text-left font-normal">
                          <button
                            type="button"
                            aria-expanded={isSel}
                            onClick={() => setSelected(isSel ? null : e.id)}
                            className="text-left text-accent-ink outline-hidden hover:underline focus-visible:outline-2 focus-visible:outline-focus"
                          >
                            {e.objectName}
                          </button>
                          {e.legacy ? <span className="block text-xs text-text-muted">GLEIF / Wikidata record</span> : null}
                        </th>
                        <td className="px-2 py-1.5">
                          <RowStatusBadge status={e.status} />
                        </td>
                        <td className="px-2 py-1.5">
                          <TierBadge tier={e.tier} />
                          <span className="block text-xs text-text-muted">{e.source}</span>
                        </td>
                        <td className="px-2 py-1.5">
                          {bar.kind === "dated" ? (
                            <div className="relative h-3 bg-border-soft" role="img" aria-label={`${humanize(e.edgeType)} ${e.objectName}: ${validityText(e)}`}>
                              <span
                                className={cn("absolute top-0 h-3", barClass(e.status), e.status === "rejected" && "opacity-60")}
                                style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                              />
                            </div>
                          ) : null}
                          <span className="num block text-xs text-text-muted">{validityText(e)}</span>
                        </td>
                      </tr>
                    </GroupRows>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {chosen ? (
          <EdgeDetail ticker={ticker} edge={chosen} onChanged={onChanged} onClose={() => setSelected(null)} />
        ) : visible.length > 0 ? (
          <p className="text-xs text-text-muted">Select a counterparty to open its source passage and decide on it.</p>
        ) : null}
        {limitations.length > 0 ? (
          <ul className="list-disc pl-5 text-xs text-text-muted">
            {limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </ChartFrame>
  );
}

function GroupRows({ header, children }: { header: { label: string; note: string } | null; children: React.ReactNode }) {
  return (
    <>
      {header ? (
        <tr className="bg-surface-muted">
          <th colSpan={5} scope="colgroup" className="px-2 py-1 text-left text-xs font-semibold">
            {header.label} <span className="font-normal text-text-muted">· {header.note}</span>
          </th>
        </tr>
      ) : null}
      {children}
    </>
  );
}

function EdgeDetail({ ticker, edge, onChanged, onClose }: { ticker: string; edge: NetworkEdge; onChanged: () => void; onClose: () => void }) {
  void ticker;
  return (
    <section aria-label={`Evidence for ${edge.objectName}`} className={cn("flex flex-col gap-2 border p-3 text-sm", isFact(edge.status) ? "border-border" : "border-dashed border-neutral-signal")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 text-base font-semibold">
          {humanize(edge.edgeType)}: {edge.objectName}
        </h3>
        <button type="button" onClick={onClose} className="text-xs text-accent-ink hover:underline">
          Close
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <RowStatusBadge status={edge.status} />
        <TierBadge tier={edge.tier} />
        <span className="text-xs text-text-muted">{TIER_LABEL[edge.tier].hint}</span>
      </div>
      {!isFact(edge.status) ? (
        <p className="text-xs text-text">
          This is not a confirmed fact. It does not feed concentration, knock-on exposure or a thesis breach until the owner confirms it against the passage below.
        </p>
      ) : null}
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-text-muted">Direction</dt>
          <dd>{directionLabel(edge.direction)}</dd>
        </div>
        <div>
          <dt className="text-text-muted">Valid</dt>
          <dd className="num">{validityText(edge)}</dd>
        </div>
        <div>
          <dt className="text-text-muted">Source</dt>
          <dd>
            <SourceLink href={edge.sourceUrl}>{edge.source}</SourceLink>
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">Known since</dt>
          <dd className="num">{edge.knownAt ? formatDate(edge.knownAt.slice(0, 10)) : "not stated"}</dd>
        </div>
        {edge.evidenceId !== null ? (
          <div>
            <dt className="text-text-muted">Evidence document</dt>
            <dd className="num">#{edge.evidenceId}</dd>
          </div>
        ) : null}
        {edge.decisionReason ? (
          <div className="sm:col-span-2">
            <dt className="text-text-muted">Decision reason on record</dt>
            <dd>{edge.decisionReason}</dd>
          </div>
        ) : null}
      </dl>
      <div>
        <h4 className="text-xs font-semibold text-text-muted uppercase">Source passage</h4>
        {edge.passage ? (
          <blockquote className="mt-1 border-l-2 border-border pl-3 text-sm whitespace-pre-wrap">{edge.passage}</blockquote>
        ) : (
          <p className="mt-1 text-xs text-text-muted">
            No passage is stored for this edge.{" "}
            {edge.sourceUrl ? <SourceLink href={edge.sourceUrl}>Open the source page</SourceLink> : "No source link either, so it cannot be confirmed from here."}
          </p>
        )}
      </div>
      {edge.rowId !== null ? (
        <DecisionControl
          status={edge.status}
          supportsAll={!edge.legacy}
          needsReason={!edge.legacy}
          onDecide={(d, reason) => decideEdge(edge, d, reason)}
          onDone={onChanged}
        />
      ) : null}
    </section>
  );
}
