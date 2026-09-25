"use client";

import { CheckIcon, CircleAlertIcon, LoaderCircleIcon, PencilIcon, XIcon } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type FactOut, type FactReviewResult, type ReviewAction } from "@/lib/api/client";
import { formatFactValue } from "@/lib/evidence/confidence";
import {
  allowedReviewActions,
  describeReviewFailure,
  REASON_MAX,
  REVIEW_ACTIONS,
  scaleHint,
  validateReview,
  type ReviewDraft,
  type ReviewErrors,
} from "@/lib/evidence/review";
import { cn } from "@/lib/utils";

const ICONS = { accept: CheckIcon, correct: PencilIcon, reject: XIcon } as const satisfies Record<ReviewAction, unknown>;

const TEXTAREA =
  "w-full rounded-md border border-border bg-surface-raised px-3 py-1.5 text-base text-text shadow-xs outline-none placeholder:text-text-muted md:text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal aria-invalid:ring-2 aria-invalid:ring-warning-signal/30";

export type ReviewResolution = {
  fact: FactOut;
  action: ReviewAction;
  reason: string;
  /** The corrected value as sent, for `correct`. */
  value: string | null;
  result: FactReviewResult;
};

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} className="flex items-center gap-1 text-xs text-text">
      <CircleAlertIcon aria-hidden="true" className="size-3.5 text-warning-signal" />
      {message}
    </p>
  );
}

/**
 * Accept, correct or reject one fact, always with a reason (logged with the decision).
 * Choosing an action is one step, confirming it another, so nothing is sent by accident.
 * The API then queues a verdict run for the holding; `onResolved` gets its job.
 */
export function FactReviewForm({
  fact,
  onResolved,
  onCancel,
  className,
}: {
  fact: FactOut;
  onResolved: (resolution: ReviewResolution) => void;
  onCancel?: () => void;
  className?: string;
}) {
  const id = useId();
  const actions = allowedReviewActions(fact.status);
  const [draft, setDraft] = useState<ReviewDraft>({ action: null, reason: "", value: "" });
  const [errors, setErrors] = useState<ReviewErrors>({});
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  if (actions.length === 0) return null;

  function update(patch: Partial<ReviewDraft>) {
    setDraft((d) => ({ ...d, ...patch }));
    setErrors((e) => {
      const next = { ...e };
      for (const key of Object.keys(patch)) delete next[key as keyof ReviewErrors];
      return next;
    });
    setFailure(null);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const checked = validateReview(draft);
    if (!checked.ok) {
      setErrors(checked.errors);
      return;
    }
    setPending(true);
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
      setPending(false);
    }
  }

  const reasonLength = draft.reason.trim().length;
  const chosen = draft.action ? REVIEW_ACTIONS[draft.action] : null;

  return (
    <form onSubmit={(e) => void submit(e)} noValidate className={cn("space-y-3 text-sm", className)}>
      <fieldset aria-describedby={errors.action ? `${id}-action-error` : `${id}-action-help`}>
        <legend className="mb-1.5 font-medium">Your decision on this figure</legend>
        <div className="flex flex-wrap gap-2">
          {actions.map((action) => {
            const Icon = ICONS[action];
            return (
              <label
                key={action}
                className={cn(
                  "inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-border bg-surface-raised px-3 font-medium text-text hover:bg-surface-muted",
                  "has-checked:border-accent has-checked:bg-accent/10 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus",
                )}
              >
                <input
                  type="radio"
                  name={`${id}-action`}
                  value={action}
                  checked={draft.action === action}
                  onChange={() => update({ action })}
                  className="sr-only"
                />
                <Icon aria-hidden="true" className="size-4" />
                {REVIEW_ACTIONS[action].label}
              </label>
            );
          })}
        </div>
        <p id={`${id}-action-help`} className="mt-1 text-xs text-text-muted">
          {chosen ? chosen.description : "Nothing is saved until you confirm."}
        </p>
        <FieldError id={`${id}-action-error`} message={errors.action} />
      </fieldset>

      {draft.action === "correct" ? (
        <div className="space-y-1">
          <Label htmlFor={`${id}-value`}>Right value, in the printed scale</Label>
          <Input
            id={`${id}-value`}
            inputMode="decimal"
            autoComplete="off"
            value={draft.value}
            placeholder={fact.value_as_written}
            onChange={(e) => update({ value: e.target.value })}
            aria-invalid={errors.value ? true : undefined}
            aria-describedby={`${id}-value-help${errors.value ? ` ${id}-value-error` : ""}`}
            className="num max-w-56"
          />
          <p id={`${id}-value-help`} className="text-xs text-text-muted">
            {scaleHint(fact)} Extracted: <span className="num">{formatFactValue(fact)}</span>.
          </p>
          <FieldError id={`${id}-value-error`} message={errors.value} />
        </div>
      ) : null}

      <div className="space-y-1">
        <Label htmlFor={`${id}-reason`}>Reason (required, logged with the decision)</Label>
        <textarea
          id={`${id}-reason`}
          rows={2}
          maxLength={REASON_MAX}
          value={draft.reason}
          onChange={(e) => update({ reason: e.target.value })}
          aria-invalid={errors.reason ? true : undefined}
          aria-describedby={`${id}-reason-count${errors.reason ? ` ${id}-reason-error` : ""}`}
          placeholder="e.g. Checked against the 10-Q; the release prints the same figure."
          className={TEXTAREA}
        />
        <p id={`${id}-reason-count`} className="num text-right text-xs text-text-muted">
          {reasonLength} / {REASON_MAX}
        </p>
        <FieldError id={`${id}-reason-error`} message={errors.reason} />
      </div>

      {failure ? (
        <p role="alert" className="flex items-start gap-1.5 rounded-md border border-warning-signal/40 bg-warning-signal-subtle p-2 text-text">
          <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warning-signal" />
          <span>
            <span className="font-medium">Not saved.</span> {failure}
          </span>
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" size="sm" disabled={pending}>
          {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
          {chosen ? chosen.submit : "Confirm decision"}
        </Button>
        {onCancel ? (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}
