"use client";

import { CheckIcon, ClockIcon, LoaderCircleIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import {
  claimTypeLabel,
  confidenceLabel,
  nextReviewState,
  reviewLabel,
  type LedgerClaim,
  type ReviewState,
} from "@/lib/research/ledger";

import { Tag } from "./report-bits";

/** One ledger claim: type, confidence with its reason, stale flag, review state and the confirm/reject buttons. */
export function LedgerClaimRow({ reportId, claim }: { reportId: number; claim: LedgerClaim }) {
  const [review, setReview] = useState<ReviewState>(claim.review);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function press(action: "confirm" | "reject") {
    const state = nextReviewState(review, action);
    setPending(true);
    setError(null);
    try {
      const { response } = await getBrowserApiClient().POST("/research/{report_id}/claims/{claim_id}/review", {
        params: { path: { report_id: reportId, claim_id: claim.id } },
        body: { state },
      });
      if (response.ok) setReview(state);
      else setError(`Review not saved (HTTP ${response.status}).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <li className="border-t border-border-soft py-2 text-sm">
      <p className="font-serif">{claim.text}</p>
      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
        <span className="font-mono">{claim.id}</span>
        <Tag>{claimTypeLabel(claim.type)}</Tag>
        <Tag title={claim.why}>{confidenceLabel(claim.confidence)}</Tag>
        {claim.stale ? (
          <Tag className="border-warning-signal/40 bg-warning-signal-subtle text-text">
            <ClockIcon aria-hidden="true" className="size-3" />
            Stale source
          </Tag>
        ) : null}
        <Tag>{reviewLabel(review)}</Tag>
        {claim.period ? <span>as of {claim.period}</span> : null}
        {claim.rule ? <span>rule: {claim.rule}</span> : null}
      </p>
      <p className="mt-0.5 text-xs text-text-muted">{claim.why}</p>
      {claim.evidence.length > 0 ? (
        <p className="mt-0.5 text-xs text-text-muted">
          Evidence: {claim.evidence.map((e) => `${e.source_id}${e.exists ? "" : " (missing)"}`).join(", ")}
        </p>
      ) : null}
      <div className="mt-1.5 flex flex-wrap items-center gap-2" data-print-hide>
        <Button size="sm" variant="outline" aria-pressed={review === "owner_confirmed"} disabled={pending} onClick={() => void press("confirm")}>
          <CheckIcon aria-hidden="true" />
          Confirm<span className="sr-only"> claim {claim.id}</span>
        </Button>
        <Button size="sm" variant="outline" aria-pressed={review === "owner_rejected"} disabled={pending} onClick={() => void press("reject")}>
          <XIcon aria-hidden="true" />
          Reject<span className="sr-only"> claim {claim.id}</span>
        </Button>
        {pending ? <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" /> : null}
        <span role="status" aria-live="polite" className="text-xs">
          {error}
        </span>
      </div>
    </li>
  );
}
