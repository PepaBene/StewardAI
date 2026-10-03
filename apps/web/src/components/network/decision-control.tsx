"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { MIN_REASON, reasonProblem, type ActionResult, type Decision } from "@/lib/network/actions";
import type { RowStatus } from "@/lib/network/types";

const LABEL: Record<Decision, string> = {
  confirm: "Confirm as fact",
  reject: "Reject",
  dispute: "Mark disputed",
  reopen: "Reopen for review",
};

/** Which decisions make sense from a status (the server enforces the transition rules). */
export function allowedDecisions(status: RowStatus, supportsAll: boolean): Decision[] {
  const base: Decision[] =
    status === "confirmed" ? ["reject", "dispute"] : status === "rejected" ? ["reopen"] : status === "disputed" ? ["confirm", "reject", "reopen"] : ["confirm", "reject", "dispute"];
  return supportsAll ? base : base.filter((d) => d === "confirm" || d === "reject");
}

/**
 * An explicit owner decision with a required written reason. Confirming asks for nothing the
 * server does not check (it refuses a confirmation without a cited passage or primary
 * evidence); nothing is decided until a button is pressed.
 */
export function DecisionControl({
  status,
  supportsAll = true,
  needsReason = true,
  onDecide,
  onDone,
}: {
  status: RowStatus;
  /** False for legacy GLEIF/Wikidata rows: confirm/reject only, no reason is sent. */
  supportsAll?: boolean;
  needsReason?: boolean;
  onDecide: (decision: Decision, reason: string) => Promise<ActionResult>;
  onDone: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<Decision | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);
  const problem = needsReason ? reasonProblem(reason) : null;

  async function go(decision: Decision) {
    if (problem) {
      setResult({ ok: false, message: problem });
      return;
    }
    setPending(decision);
    setResult(null);
    const r = await onDecide(decision, reason);
    setPending(null);
    setResult(r);
    if (r.ok) {
      setReason("");
      onDone();
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-border-soft pt-2">
      {needsReason ? (
        <div className="space-y-1">
          <Label htmlFor={`${id}-reason`}>Reason for the decision (at least {MIN_REASON} characters, kept in the audit trail)</Label>
          <textarea
            id={`${id}-reason`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            maxLength={2000}
            className="w-full min-w-0 border border-border bg-surface-raised px-3 py-1.5 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
          />
        </div>
      ) : (
        <p className="text-xs text-text-muted">This GLEIF/Wikidata relationship is decided without a written reason.</p>
      )}
      <div className="flex flex-wrap gap-2">
        {allowedDecisions(status, supportsAll).map((d) => (
          <Button key={d} type="button" size="sm" variant={d === "confirm" ? "default" : "outline"} disabled={pending !== null} onClick={() => void go(d)}>
            {pending === d ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            {LABEL[d]}
          </Button>
        ))}
      </div>
      {result ? (
        <p role={result.ok ? "status" : "alert"} className="text-xs text-text">
          {result.ok ? "Done. " : "Not applied. "}
          {result.message}
        </p>
      ) : null}
    </div>
  );
}
