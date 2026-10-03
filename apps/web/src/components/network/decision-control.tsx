"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

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

/**
 * Which decisions make sense from a status (the server enforces the transition rules).
 * An unrecognised status offers none: the real status is unknown, so nothing is confirmed blind.
 * Legacy GLEIF/Wikidata relationships (`supportsAll` false) are confirmed or rejected only from
 * suggested or disputed; the server refuses to reject a confirmed one.
 */
export function allowedDecisions(status: RowStatus, supportsAll: boolean): Decision[] {
  if (status === "unrecognised") return [];
  if (!supportsAll) return status === "suggested" || status === "disputed" ? ["confirm", "reject"] : [];
  switch (status) {
    case "confirmed":
      return ["reject", "dispute"];
    case "rejected":
      return ["reopen"];
    case "disputed":
      return ["confirm", "reject", "reopen"];
    default:
      return ["confirm", "reject", "dispute"];
  }
}

/**
 * An explicit owner decision with a required written reason. Confirming asks for nothing the
 * server does not check (it refuses a confirmation without a cited passage or primary
 * evidence); nothing is decided until a button is pressed. With `readOnly` (a historical as-of
 * view) the row's status is not the one a decision would apply to, so no button is offered.
 */
export function DecisionControl({
  status,
  supportsAll = true,
  needsReason = true,
  readOnly = false,
  onDecide,
  onDone,
}: {
  status: RowStatus;
  /** False for legacy GLEIF/Wikidata rows: confirm/reject only, no reason is sent. */
  supportsAll?: boolean;
  needsReason?: boolean;
  /** True while a past as-of date is shown: decisions apply now, to a status that may differ. */
  readOnly?: boolean;
  onDecide: (decision: Decision, reason: string) => Promise<ActionResult>;
  onDone: () => void;
}) {
  const id = useId();
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState<Decision | null>(null);
  const [result, setResult] = useState<ActionResult | null>(null);
  const resultRef = useRef<HTMLParagraphElement>(null);
  const problem = needsReason ? reasonProblem(reason) : null;
  const decisions = readOnly ? [] : allowedDecisions(status, supportsAll);

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

  // The pressed button may be replaced once the status changes: keep focus on the outcome message.
  useEffect(() => {
    if (result) resultRef.current?.focus();
  }, [result]);

  const resultNode = result ? (
    <p ref={resultRef} tabIndex={-1} role={result.ok ? "status" : "alert"} className="text-xs text-text outline-hidden focus-visible:outline-2 focus-visible:outline-focus">
      {result.ok ? "Done. " : "Not applied. "}
      {result.message}
    </p>
  ) : null;

  if (readOnly) {
    return <p className="border-t border-border-soft pt-2 text-xs text-text-muted">Read-only: this is the status as known on a past date. Switch to Now to decide; decisions apply to the current status.</p>;
  }
  if (decisions.length === 0) {
    return (
      <div className="border-t border-border-soft pt-2">
        <p className="text-xs text-text-muted">
          {status === "unrecognised"
            ? "The status is not one this screen recognises, so no decision is offered."
            : "No decision is available from this status here (a confirmed GLEIF or Wikidata record cannot be rejected, and a rejected one cannot be reopened)."}
        </p>
        {resultNode}
      </div>
    );
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
        {decisions.map((d) => (
          <Button key={d} type="button" size="sm" variant={d === "confirm" ? "default" : "outline"} disabled={pending !== null} onClick={() => void go(d)}>
            {pending === d ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            {LABEL[d]}
          </Button>
        ))}
      </div>
      {resultNode}
    </div>
  );
}
