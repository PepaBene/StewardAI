"use client";

import {
  CheckIcon,
  CircleAlertIcon,
  CircleSlashIcon,
  LoaderCircleIcon,
  PencilIcon,
  ReplaceIcon,
  XIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type FactOut, type FeedbackRating, type ReviewAction } from "@/lib/api/client";
import { formatFactValue } from "@/lib/evidence/confidence";
import {
  allowedReviewActions,
  describeReviewFailure,
  REASON_MAX,
  REVIEW_ACTIONS,
  scaleHint,
  validateReview,
  type ReviewErrors,
} from "@/lib/evidence/review";
import { buildFeedback, describeFeedbackFailure, emptyDraft, RATING_LABELS, summarizeEffects } from "@/lib/feedback/feedback";
import { feedbackMarks } from "@/lib/hooks/use-feedback-mark";
import { cn } from "@/lib/utils";

import type { ReviewResolution } from "./fact-review-form";

const TEXTAREA =
  "w-full border border-border bg-surface-raised px-3 py-1.5 text-base text-text outline-none placeholder:text-text-muted md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal aria-invalid:ring-2 aria-invalid:ring-warning-signal/30";

/** A key press meant for a text field, a select or a button with its own keys: not a shortcut. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * The review queue's action bar for one held or quarantined fact: a required reason (the
 * API logs it with the decision), then Wrong / Irrelevant (feedback on the extraction, the
 * reason going in as its note), "Use filed X" (correct to the filing's value), Correct (type
 * the value), Reject, and Approve (accept). Every decision re-judges the holding's verdicts;
 * `onResolved` gets the queued job. With `hotkeys`, "A" approves (still needing a reason).
 */
export function ReviewActionBar({
  fact,
  filed,
  hotkeys = false,
  onResolved,
  onFeedback,
  className,
}: {
  fact: FactOut;
  /** The filing's value, as an exact decimal in the printed scale, and its display text. */
  filed: { value: string; display: string } | null;
  hotkeys?: boolean;
  onResolved: (resolution: ReviewResolution) => void;
  /** After Wrong / Irrelevant was recorded (it may have changed the fact's status). */
  onFeedback?: () => void;
  className?: string;
}) {
  const id = useId();
  const actions = allowedReviewActions(fact.status);
  const [reason, setReason] = useState("");
  const [correcting, setCorrecting] = useState(false);
  const [value, setValue] = useState("");
  const [errors, setErrors] = useState<ReviewErrors>({});
  const [pending, setPending] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [marked, setMarked] = useState<string | null>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  async function decide(action: ReviewAction, correctedValue = "") {
    const checked = validateReview({ action, reason, value: correctedValue });
    if (!checked.ok) {
      setErrors(checked.errors);
      if (checked.errors.reason) reasonRef.current?.focus();
      return;
    }
    setPending(action);
    setFailure(null);
    try {
      const { data, error, response } = await getBrowserApiClient().POST("/facts/{fact_id}/review", {
        params: { path: { fact_id: fact.id } },
        body: checked.body,
      });
      if (data) {
        onResolved({
          fact,
          action: checked.body.action,
          reason: checked.body.reason,
          value: checked.body.value == null ? null : String(checked.body.value),
          result: data,
        });
      } else {
        setFailure(describeReviewFailure(response.status, error));
      }
    } catch (err) {
      setFailure(describeReviewFailure(0, { detail: err instanceof Error ? err.message : String(err) }));
    } finally {
      setPending(null);
    }
  }

  async function mark(rating: FeedbackRating) {
    const built = buildFeedback({ kind: "fact", id: fact.id }, { ...emptyDraft(rating), note: reason });
    if (!built.ok) {
      setFailure(Object.values(built.errors).join(" "));
      return;
    }
    setPending(rating);
    setFailure(null);
    try {
      const { data, error, response } = await getBrowserApiClient().POST("/feedback", { body: built.body });
      if (data) {
        feedbackMarks.put(data);
        const effects = summarizeEffects(data.effects);
        setMarked(`${RATING_LABELS[rating].marked}${effects ? `: ${effects}` : "."}`);
        onFeedback?.();
      } else {
        setFailure(describeFeedbackFailure(response.status, error));
      }
    } catch (err) {
      setFailure(describeFeedbackFailure(0, { detail: err instanceof Error ? err.message : String(err) }));
    } finally {
      setPending(null);
    }
  }

  // "A" approves the selected fact (the reason is still required).
  const approveRef = useRef<() => void>(() => undefined);
  useEffect(() => {
    approveRef.current = () => void decide("accept");
  });
  useEffect(() => {
    if (!hotkeys || !actions.includes("accept")) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "a" || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
      e.preventDefault();
      approveRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hotkeys, actions]);

  if (actions.length === 0) return null;
  const busy = pending !== null;
  const spin = (key: string) => (pending === key ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null);

  return (
    <div className={cn("space-y-3 text-sm", className)}>
      <div className="space-y-1">
        <Label htmlFor={`${id}-reason`}>Reason (required, logged with the decision)</Label>
        <textarea
          ref={reasonRef}
          id={`${id}-reason`}
          rows={2}
          maxLength={REASON_MAX}
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            setErrors((x) => ({ ...x, reason: undefined, action: undefined }));
            setFailure(null);
          }}
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={`${id}-reason-count${errors.reason ? ` ${id}-reason-error` : ""}`}
          placeholder="e.g. Checked against the 10-Q; the release prints the same figure."
          className={TEXTAREA}
        />
        <div className="flex justify-between gap-2 text-xs">
          <FieldError id={`${id}-reason-error`} message={errors.reason} />
          <span id={`${id}-reason-count`} className="num ml-auto text-text-muted">
            {reason.trim().length} / {REASON_MAX}
          </span>
        </div>
      </div>

      {correcting ? (
        <form
          className="space-y-1 border-l-2 border-accent pl-3"
          onSubmit={(e) => {
            e.preventDefault();
            void decide("correct", value);
          }}
        >
          <Label htmlFor={`${id}-value`}>Right value, in the printed scale</Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id={`${id}-value`}
              inputMode="decimal"
              autoComplete="off"
              autoFocus
              value={value}
              placeholder={fact.value_as_written}
              onChange={(e) => {
                setValue(e.target.value);
                setErrors((x) => ({ ...x, value: undefined }));
              }}
              aria-invalid={errors.value ? true : undefined}
              aria-describedby={`${id}-value-help${errors.value ? ` ${id}-value-error` : ""}`}
              className="num max-w-56"
            />
            <Button type="submit" size="sm" disabled={busy}>
              {spin("correct")}
              {REVIEW_ACTIONS.correct.submit}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setCorrecting(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
          <p id={`${id}-value-help`} className="text-xs text-text-muted">
            {scaleHint(fact)} Extracted: <span className="num">{formatFactValue(fact)}</span>.
          </p>
          <FieldError id={`${id}-value-error`} message={errors.value} />
        </form>
      ) : null}

      <div className="flex flex-wrap items-center gap-2.5 border-t border-border pt-4">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void mark("wrong")} title={RATING_LABELS.wrong.description}>
          {spin("wrong") ?? <XIcon aria-hidden="true" />}
          Wrong
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => void mark("irrelevant")}
          title={RATING_LABELS.irrelevant.description}
        >
          {spin("irrelevant") ?? <CircleSlashIcon aria-hidden="true" />}
          Irrelevant
        </Button>
        {filed && actions.includes("correct") ? (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => void decide("correct", filed.value)}
            title={`Correct the figure to the filing's value (${filed.display}); needs a reason.`}
          >
            {spin("correct") ?? <ReplaceIcon aria-hidden="true" />}
            Use filed <span className="num">{filed.display}</span>
          </Button>
        ) : null}
        {actions.includes("correct") ? (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => setCorrecting((c) => !c)} aria-expanded={correcting}>
            <PencilIcon aria-hidden="true" />
            Correct…
          </Button>
        ) : null}
        {actions.includes("reject") ? (
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => void decide("reject")}
            title={REVIEW_ACTIONS.reject.description}
          >
            {spin("reject")}
            Reject
          </Button>
        ) : null}
        <span className="ml-auto hidden text-xs text-text-muted md:inline">
          <kbd className="font-mono">J</kbd> / <kbd className="font-mono">K</kbd> to move
          {actions.includes("accept") ? (
            <>
              {" · "}
              <kbd className="font-mono">A</kbd> to approve
            </>
          ) : null}
        </span>
        {actions.includes("accept") ? (
          <Button type="button" disabled={busy} onClick={() => void decide("accept")} title={REVIEW_ACTIONS.accept.description}>
            {spin("accept") ?? <CheckIcon aria-hidden="true" />}
            Approve fact
          </Button>
        ) : null}
      </div>

      {marked ? (
        <p role="status" className="text-xs text-text-muted">
          {marked}
        </p>
      ) : null}
      {failure ? (
        <p role="alert" className="flex items-start gap-1.5 border border-warning-signal/40 bg-warning-signal-subtle p-2 text-text">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
          <span>
            <span className="font-medium">Not saved.</span> {failure}
          </span>
        </p>
      ) : null}
    </div>
  );
}

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} className="flex items-center gap-1 text-xs text-text">
      <CircleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
      {message}
    </p>
  );
}
