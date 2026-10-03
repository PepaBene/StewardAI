"use client";

import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { describeAction } from "@/lib/markets/logic";
import { ACTION_GLYPHS, ACTION_LABELS, type ActionStatus, type CorporateAction } from "@/lib/markets/types";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ActionStatus, string> = {
  confirmed: "Confirmed",
  suggested: "Suggested (not used in returns)",
  rejected: "Rejected",
  disputed: "Disputed",
};

type Decision = "confirmed" | "rejected" | "suggested";

/**
 * The corporate-action ledger of a holding with each action's evidence. An owner decision is two
 * steps: pick the decision and state the reason (10+ characters, kept with the action), then
 * submit it. Confirming needs linked evidence: an action without an evidence id asks for one.
 * Nothing changes until the backend accepts the decision; the ledger is then re-read.
 */
export function ActionLedger({ ticker, actions, onChanged }: { ticker: string; actions: readonly CorporateAction[]; onChanged?: () => void }) {
  return (
    <Blueprint as="section" aria-labelledby="actions-ledger-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Corporate-action ledger" title={<span id="actions-ledger-heading">{ticker}: actions and their evidence</span>} />
      {actions.length === 0 ? (
        <p className="text-sm text-text-muted">No corporate action is recorded for this holding. That is not evidence that none happened: coverage is verified separately (see the returns panel).</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
          {actions.map((a) => (
            <ActionRow key={a.id} ticker={ticker} action={a} onChanged={onChanged} />
          ))}
        </ul>
      )}
    </Blueprint>
  );
}

function ActionRow({ ticker, action: a, onChanged }: { ticker: string; action: CorporateAction; onChanged?: () => void }) {
  const [pending, setPending] = useState<Decision | null>(null);
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const evidenceId = a.evidenceId ?? (evidence.trim() !== "" && Number.isInteger(Number(evidence)) && Number(evidence) > 0 ? Number(evidence) : null);
  const needsEvidence = pending === "confirmed" && evidenceId === null;
  const reasonOk = reason.trim().length >= 10;

  async function submit() {
    if (!pending || !reasonOk || needsEvidence) return;
    setBusy(true);
    setError(null);
    try {
      const { response, error: apiError } = await getBrowserApiClient().POST("/holdings/{ticker}/corporate-actions/{action_id}/decision", {
        params: { path: { ticker, action_id: a.id } },
        body: { decision: pending, reason: reason.trim(), evidence_id: a.evidenceId === null ? evidenceId : null },
      });
      if (!response.ok) {
        const detail = apiError && typeof apiError === "object" && "detail" in apiError ? String(apiError.detail) : `HTTP ${response.status}`;
        setError(detail);
        return;
      }
      setPending(null);
      setReason("");
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const decisions: { id: Decision; label: string }[] =
    a.status === "suggested" ? [{ id: "confirmed", label: "Confirm…" }, { id: "rejected", label: "Reject…" }] : [{ id: "suggested", label: "Return to review…" }];

  return (
    <li className="flex flex-col gap-1.5 border-b border-border-soft pb-2 last:border-0">
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span aria-hidden="true" className="inline-flex size-5 items-center justify-center border border-text font-mono text-[11px] font-semibold">
          {ACTION_GLYPHS[a.type]}
        </span>
        <span className="font-medium">{ACTION_LABELS[a.type]}</span>
        <span className="num text-text-muted">ex {formatDay(a.exDate)}</span>
        <span className="num">{describeAction(a) ?? "amount not recorded"}</span>
        <span className={cn("text-xs", a.status === "confirmed" ? "text-text-muted" : "font-medium text-text")}>{STATUS_LABEL[a.status]}</span>
      </div>
      <p className="m-0 min-w-0 text-xs text-text-muted">
        Source {a.sourceUrl ? (
          <a href={a.sourceUrl} target="_blank" rel="noreferrer noopener" className="break-all text-accent-ink hover:underline">
            {a.source}
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : (
          a.source
        )}{" "}
        · symbol {a.symbol || "n/a"} · {a.evidenceId !== null ? `evidence #${a.evidenceId}` : "no linked evidence"}
        {a.recordDate ? ` · record ${formatDay(a.recordDate)}` : ""}
        {a.paymentDate ? ` · paid ${formatDay(a.paymentDate)}` : ""}
        {a.amountBasis === "split_adjusted" ? " · amount already split-adjusted" : ""}
        {a.decidedAt && a.decisionReason ? ` · decided: ${a.decisionReason}` : ""}
      </p>
      {pending === null ? (
        <div className="flex flex-wrap gap-2">
          {decisions.map((d) => (
            <Button key={d.id} variant="outline" size="sm" onClick={() => setPending(d.id)}>
              {d.label}
            </Button>
          ))}
        </div>
      ) : (
        <form
          className="flex max-w-xl flex-col gap-2 border border-border bg-surface-muted p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <p className="m-0 text-sm font-medium">
            {pending === "confirmed" ? "Confirm" : pending === "rejected" ? "Reject" : "Return to review"} the {ACTION_LABELS[a.type].toLowerCase()} of {formatDay(a.exDate)}?
          </p>
          <label className="flex flex-col gap-1 text-xs text-text-muted">
            Reason (at least 10 characters; stored with the decision)
            <Input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} aria-invalid={reason !== "" && !reasonOk} />
          </label>
          {pending === "confirmed" && a.evidenceId === null ? (
            <label className="flex flex-col gap-1 text-xs text-text-muted">
              Evidence id (confirming requires linked source evidence)
              <Input inputMode="numeric" value={evidence} onChange={(e) => setEvidence(e.target.value)} aria-invalid={evidence !== "" && evidenceId === null} />
            </label>
          ) : null}
          {error ? <AlertMessage title="The decision was not saved">{error}</AlertMessage> : null}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" size="sm" disabled={busy || !reasonOk || needsEvidence}>
              {busy ? "Saving…" : "Save decision"}
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setPending(null); setError(null); }}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}
