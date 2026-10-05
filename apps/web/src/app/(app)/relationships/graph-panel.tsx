"use client";

import { EmptyState } from "@/components/empty-state";
import { Panel } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { titleCase } from "@/lib/parse-untyped";
import { DECISION_LABEL, decisionBody, decisionsFor, DECISION_RESULT, safeUrl, type Edge, type GraphDecision, type Reconciliation } from "@/lib/relationships/relationships";

import { callApi, DecisionForm } from "./decision-form";
import { Fact, SourceLink, StatusMark } from "./status-mark";

const DIRECTION: Record<string, string> = { outgoing: "from the holding to the other company", incoming: "from the other company to the holding", symmetric: "both ways" };

function EdgeCard({ e, onChanged }: { e: Edge; onChanged: () => void }) {
  return (
    <li className="space-y-2 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusMark status={e.status} />
        <span className="text-sm font-medium">{e.objectName}</span>
        <span className="text-sm text-text-muted">{titleCase(e.edgeType)}</span>
        <span className="num ml-auto text-xs text-text-muted">#{e.id}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-4">
        <Fact label="Direction">{DIRECTION[e.direction] ?? e.direction}</Fact>
        <Fact label="Valid">{e.validFrom || e.validTo ? `${e.validFrom ?? "open start"} to ${e.validTo ?? "open end"}` : null}</Fact>
        <Fact label="Evidence tier">{e.tier ? titleCase(e.tier) : null}</Fact>
        <Fact label="Recorded">{e.knownAt?.slice(0, 10) ?? null}</Fact>
      </dl>
      {e.passage ? <blockquote className="max-w-[76ch] border-l-2 border-border pl-3 text-sm italic">{e.passage}</blockquote> : <p className="text-xs text-text-muted">No cited passage, so this edge cannot be confirmed yet.</p>}
      <p className="text-xs text-text-muted">
        <SourceLink href={safeUrl(e.sourceUrl)} /> {e.source ? `· ${e.source}` : ""}
      </p>
      <p className="text-xs text-text-muted">{e.decisionReason ? `Last decision: ${e.decisionReason}` : "No owner decision recorded yet."}</p>
      <DecisionForm
        options={decisionsFor(e.status).map((v) => ({ value: v, label: DECISION_LABEL[v] }))}
        onSubmit={async (v, reason) => {
          const r = await callApi(
            () => getBrowserApiClient().POST("/company-graph/edges/{edge_id}/decision", { params: { path: { edge_id: e.id } }, body: decisionBody(v as GraphDecision, reason) }),
            `Saved: edge ${e.id} is now ${DECISION_RESULT[v as GraphDecision]}.`,
          );
          if (r.ok) onChanged();
          return r;
        }}
      />
    </li>
  );
}

/** Section 3: graph edges of one holding with their last decision, and parent or successor changes no deal explains. */
export function GraphPanel({ ticker, edges, edgesNote, reconciliations, onChanged }: { ticker: string | null; edges: Edge[] | null; edgesNote: React.ReactNode; reconciliations: Reconciliation[]; onChanged: () => void }) {
  return (
    <Panel
      id="graph"
      title="Company graph edges"
      description="Links between companies with the reason behind the last decision. The API keeps the full status log but returns only the current status and the last reason, so that is what is shown."
    >
      {reconciliations.length > 0 ? (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Changes no closed deal explains ({reconciliations.length})</h3>
          <ul className="space-y-1.5">
            {reconciliations.map((r) => (
              <li key={r.id} className="border border-dashed border-border px-3 py-2 text-sm">
                <span className="num font-semibold">{r.ticker}</span> {titleCase(r.changeKind)}: {r.fromKey ?? "unknown"} to {r.toKey ?? "unknown"}
                <span className="text-text-muted">
                  {" "}
                  · {titleCase(r.outcome)} · seen {r.observedAt?.slice(0, 10) ?? "on an unrecorded date"} via {r.source ?? "an unrecorded source"}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-text-muted">The API offers no way to close a change from here. Record the deal that explains it, or fix the edge below.</p>
        </div>
      ) : null}
      {edgesNote}
      {ticker === null ? (
        <EmptyState title="Choose a holding">Enter a ticker above to list its graph edges.</EmptyState>
      ) : edges && edges.length === 0 ? (
        <EmptyState title={`No graph edges for ${ticker}`}>Edges appear once a source links this holding to another company.</EmptyState>
      ) : edges ? (
        <ul className="space-y-3">
          {edges.map((e) => (
            <EdgeCard key={e.id} e={e} onChanged={onChanged} />
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
