"use client";

import { CheckIcon, LoaderCircleIcon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type ResearchBody } from "@/lib/api/client";
import { apiErrorDetail } from "@/lib/evidence/review";
import {
  acceptOutcome,
  diffLines,
  fieldLabel,
  modeLabel,
  resolveEvidence,
  type AcceptOutcome,
  type FieldChange,
  type ProposalField,
} from "@/lib/research/proposal";

import { NoteList, Panel, Tag } from "./report-bits";

type Props = {
  reportId: number;
  proposal: ResearchBody["thesis_proposal"];
  review: ResearchBody["thesis_review"];
  sources: ResearchBody["sources"];
};

const MARK = { removed: "Removed", kept: "Unchanged", added: "Added" } as const;

/** The thesis proposal of a report: per field a diff against the active thesis, evidence per line, and a per-field accept. */
export function ThesisProposalPanel({ reportId, proposal, review, sources }: Props) {
  const [applied, setApplied] = useState<ProposalField[]>([]);
  const [pending, setPending] = useState<ProposalField | null>(null);
  const [outcome, setOutcome] = useState<AcceptOutcome | null>(null);

  if (!proposal && !review) return null;

  async function accept(field: ProposalField) {
    setPending(field);
    setOutcome(null);
    try {
      const { data, error, response } = await getBrowserApiClient().POST("/research/{report_id}/thesis-proposal/accept", {
        params: { path: { report_id: reportId } },
        body: { fields: [field] },
      });
      const detail = apiErrorDetail(error) || null;
      const result = acceptOutcome(response.status, detail, data?.applied ?? [field]);
      setOutcome(result);
      if (result.kind === "applied") setApplied((a) => [...a, field]);
    } catch (err) {
      setOutcome({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    } finally {
      setPending(null);
    }
  }

  return (
    <Panel
      id="thesis-proposal"
      title={proposal ? `Thesis proposal · ${modeLabel(proposal.mode)}` : "Thesis review"}
      hint="Nothing changes until you accept a field. Accepting saves a new thesis version; the earlier one stays in the history."
    >
      {proposal ? (
        <>
          {proposal.changes.length === 0 ? <p className="text-sm text-text-muted">This report proposes no thesis changes.</p> : null}
          {proposal.changes.map((change) => (
            <FieldDiff
              key={change.field}
              change={change}
              sources={sources}
              done={applied.includes(change.field)}
              busy={pending !== null}
              loading={pending === change.field}
              onAccept={() => void accept(change.field)}
            />
          ))}
          {proposal.unchanged.length > 0 ? (
            <p className="text-xs text-text-muted">Unchanged: {proposal.unchanged.map(fieldLabel).join(", ")}.</p>
          ) : null}
          {proposal.left_to_owner.length > 0 ? (
            <div>
              <h3 className="text-xs font-medium text-text-muted">Left to you</h3>
              <NoteList lines={proposal.left_to_owner} />
            </div>
          ) : null}
          <NoteList lines={proposal.notes ?? []} className="text-text-muted" />
        </>
      ) : null}
      {review ? (
        <div className="space-y-1 text-sm">
          {review.why_owned ? <p>Why owned: {review.why_owned}</p> : null}
          <NoteList lines={review.exit_conditions} />
          <NoteList lines={review.notes ?? []} className="text-text-muted" />
        </div>
      ) : null}
      <div role="status" aria-live="polite">
        {outcome ? (
          outcome.kind === "applied" ? (
            <p className="text-sm">{outcome.message}</p>
          ) : (
            <AlertMessage title={outcome.kind === "stale" ? "Run the research again" : "Not applied"}>{outcome.message}</AlertMessage>
          )
        ) : null}
      </div>
    </Panel>
  );
}

function FieldDiff({
  change,
  sources,
  done,
  busy,
  loading,
  onAccept,
}: {
  change: FieldChange;
  sources: ResearchBody["sources"];
  done: boolean;
  busy: boolean;
  loading: boolean;
  onAccept: () => void;
}) {
  const lines = diffLines(change);
  return (
    <section aria-label={fieldLabel(change.field)} className="border-t border-border-soft pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">
          {fieldLabel(change.field)} <Tag>{change.kind === "add" ? "new" : "changed"}</Tag>
        </h3>
        <Button size="sm" variant="outline" disabled={busy || done} onClick={onAccept} data-print-hide>
          {loading ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <CheckIcon aria-hidden="true" />}
          {done ? "Accepted" : "Accept"}
          <span className="sr-only"> {fieldLabel(change.field)}</span>
        </Button>
      </div>
      <ul className="mt-2 space-y-1.5 text-sm">
        {lines.map((line, i) => (
          <li key={`${line.kind}-${i}`} className="grid grid-cols-[72px_minmax(0,1fr)] gap-2">
            <span className="text-xs font-medium text-text-muted">{MARK[line.kind]}</span>
            <div className="min-w-0">
              <p className={line.kind === "removed" ? "text-text-muted line-through" : undefined}>{line.text}</p>
              {line.kind === "added" ? <Evidence ids={line.evidenceIds} sources={sources} /> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Evidence({ ids, sources }: { ids: string[]; sources: ResearchBody["sources"] }) {
  const evidence = resolveEvidence(ids, sources);
  if (evidence.length === 0) return <p className="text-xs text-text-muted">No evidence cited for this line.</p>;
  return (
    <p className="text-xs text-text-muted">
      Evidence:{" "}
      {evidence.map((e, i) => (
        <span key={e.id}>
          {i > 0 ? "; " : ""}
          <span className="font-mono">{e.id}</span> {e.listed ? e.title : "(not in the source list)"}
        </span>
      ))}
    </p>
  );
}
