"use client";

import { CircleAlertIcon, CircleSlashIcon, LoaderCircleIcon, SendIcon, ThumbsDownIcon, ThumbsUpIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBrowserApiClient, type EventType, type FeedbackOut, type FeedbackRating } from "@/lib/api/client";
import {
  buildFeedback,
  canAddToEval,
  describeFeedbackFailure,
  effectParts,
  emptyDraft,
  NOTE_MAX,
  RATING_LABELS,
  RATINGS,
  summarizeEffects,
  type FeedbackDraft,
  type FeedbackErrors,
  type FeedbackTarget,
} from "@/lib/feedback/feedback";
import { formatDateTime } from "@/lib/format";
import { useAnchoredPosition } from "@/lib/hooks/use-anchored-position";
import { feedbackMarks, useFeedbackMark } from "@/lib/hooks/use-feedback-mark";
import { EVENT_LABELS, EVENT_TYPES } from "@/lib/news/labels";
import { cn } from "@/lib/utils";

const ICONS = { wrong: ThumbsDownIcon, irrelevant: CircleSlashIcon, useful: ThumbsUpIcon } as const;

const TEXTAREA =
  "w-full rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text shadow-xs outline-none placeholder:text-text-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal";

type LabelDefaults = {
  eventType?: string | null;
  affectedTicker?: string | null;
  relevant?: boolean | null;
  rumour?: boolean | null;
};

function FieldError({ id, message }: { id: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <p id={id} className="flex items-center gap-1 text-xs text-text">
      <CircleAlertIcon aria-hidden="true" className="size-3.5 shrink-0 text-warning-signal" />
      {message}
    </p>
  );
}

/**
 * Wrong / irrelevant / useful on one fact, verdict, alert, news item, story or report
 * sentence (`POST /feedback`). Compact: three icon buttons with the current mark in words
 * beside them. Choosing a rating opens a small form (an optional note; for a wrong fact or
 * news item, adding it to the eval set with the right value or label); sending it shows
 * what the mark did ("Quarantined; verdicts re-judged (job #n); ir.test trust 0.8").
 * Escape or Cancel closes the form and returns focus to the button.
 */
export function FeedbackControl({
  target,
  label,
  valueHint,
  labelDefaults,
  onMarked,
  className,
}: {
  target: FeedbackTarget;
  /** What is rated, for screen readers and the form title: "figure Revenue Q3 2026". */
  label: string;
  /** Facts: how to type the corrected value (in the printed scale). */
  valueHint?: string;
  /** News items: the stored classification, to start the corrected label from. */
  labelDefaults?: LabelDefaults;
  onMarked?: (mark: FeedbackOut) => void;
  className?: string;
}) {
  const id = useId();
  const current = useFeedbackMark(target);
  const [draft, setDraft] = useState<FeedbackDraft | null>(null);
  const [errors, setErrors] = useState<FeedbackErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [given, setGiven] = useState<FeedbackOut | null>(null);
  const groupRef = useRef<HTMLSpanElement>(null);
  const panelRef = useRef<HTMLFormElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const open = draft !== null;
  const position = useAnchoredPosition(open, groupRef, panelRef);

  function start(rating: FeedbackRating, button: HTMLButtonElement) {
    openerRef.current = button;
    const next = emptyDraft(rating);
    next.label = {
      eventType: (EVENT_TYPES as readonly string[]).includes(labelDefaults?.eventType ?? "") ? (labelDefaults!.eventType as EventType) : "",
      affectedTicker: labelDefaults?.affectedTicker ?? "",
      relevant: labelDefaults?.relevant ?? true,
      rumour: labelDefaults?.rumour ?? false,
    };
    setDraft(next);
    setErrors({});
    setFailure(null);
  }

  function close() {
    setDraft(null);
    setErrors({});
    setFailure(null);
    openerRef.current?.focus();
  }

  // Focus the form when it opens; close it on a click outside.
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>("textarea, input, select, button")?.focus();
    function onPointer(event: PointerEvent) {
      const node = event.target as Node;
      if (panelRef.current?.contains(node) || groupRef.current?.contains(node)) return;
      setDraft(null);
    }
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  function update(patch: Partial<FeedbackDraft>) {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setFailure(null);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    const built = buildFeedback(target, draft);
    if (!built.ok) {
      setErrors(built.errors);
      return;
    }
    setErrors({});
    setPending(true);
    try {
      const { data, error, response } = await getBrowserApiClient().POST("/feedback", { body: built.body });
      if (data) {
        feedbackMarks.put(data);
        setGiven(data);
        setDraft(null);
        openerRef.current?.focus();
        onMarked?.(data);
      } else {
        setFailure(describeFeedbackFailure(response.status, error));
      }
    } catch (err) {
      setFailure(describeFeedbackFailure(0, { detail: err instanceof Error ? err.message : String(err) }));
    } finally {
      setPending(false);
    }
  }

  // The form is a small dialog: Escape closes it, Tab cycles inside it.
  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const focusable = [...panelRef.current.querySelectorAll<HTMLElement>("input, select, textarea, button, a[href]")].filter(
      (el) => !el.hasAttribute("disabled"),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const mark = given ?? current ?? null;
  const shown = mark ? RATING_LABELS[mark.rating] : null;
  const markTitle = mark
    ? [
        `${shown!.marked} on ${formatDateTime(mark.created_at)}`,
        mark.note ? `Note: ${mark.note}` : null,
        summarizeEffects(mark.effects),
      ]
        .filter(Boolean)
        .join(". ")
    : undefined;
  const evalOk = draft ? canAddToEval(target.kind, draft.rating) : false;

  return (
    <span className={cn("inline-flex flex-wrap items-center gap-1 align-middle", className)}>
      <span ref={groupRef} role="group" aria-label={`Feedback on ${label}`} className="inline-flex items-center gap-0.5">
        {RATINGS.map((rating) => {
          const Icon = ICONS[rating];
          const active = mark?.rating === rating;
          const opened = draft?.rating === rating;
          return (
            <button
              key={rating}
              type="button"
              aria-pressed={active}
              aria-expanded={opened}
              aria-controls={opened ? `${id}-form` : undefined}
              aria-label={`${RATING_LABELS[rating].label}: ${label}`}
              title={`${RATING_LABELS[rating].label}: ${RATING_LABELS[rating].description}`}
              onClick={(e) => (opened ? close() : start(rating, e.currentTarget))}
              className={cn(
                "inline-flex size-6 items-center justify-center rounded-sm border text-text-muted transition-colors outline-none hover:text-text focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
                active ? "border-accent bg-surface-muted text-text" : "border-transparent hover:border-border",
              )}
            >
              <Icon aria-hidden="true" className={cn("size-3.5", active && "fill-current")} />
            </button>
          );
        })}
      </span>
      {mark ? (
        <span className="text-xs text-text-muted" title={markTitle}>
          {shown!.marked}
        </span>
      ) : null}
      <span role="status" aria-live="polite" className="text-xs text-text-muted">
        {given ? <Effects mark={given} /> : null}
      </span>

      {draft
        ? createPortal(
        <form
          ref={panelRef}
          id={`${id}-form`}
          role="dialog"
          aria-label={`${RATING_LABELS[draft.rating ?? "useful"].label}: ${label}`}
          onSubmit={(e) => void submit(e)}
          onKeyDown={onKeyDown}
          noValidate
          style={position ? { top: position.top, left: position.left } : { top: 0, left: 0, visibility: "hidden" }}
          className="fixed z-40 w-80 max-w-[calc(100vw-1rem)] space-y-2 rounded-lg border border-border bg-surface-raised p-3 text-left text-sm font-normal text-text shadow-lg"
        >
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-text-muted">Mark {label} as</legend>
            <div className="flex flex-wrap gap-1">
              {RATINGS.map((rating) => (
                <label
                  key={rating}
                  className={cn(
                    "inline-flex cursor-pointer items-center gap-1 rounded-md border px-2 py-0.5 text-xs has-focus-visible:ring-2 has-focus-visible:ring-focus",
                    draft.rating === rating ? "border-accent bg-surface-muted font-medium" : "border-border text-text-muted",
                  )}
                >
                  <input
                    type="radio"
                    name={`${id}-rating`}
                    className="sr-only"
                    checked={draft.rating === rating}
                    onChange={() => update({ rating })}
                  />
                  {RATING_LABELS[rating].label}
                </label>
              ))}
            </div>
            <p className="mt-1 text-xs text-text-muted">{RATING_LABELS[draft.rating ?? "useful"].description}</p>
            <FieldError id={`${id}-rating-error`} message={errors.rating ?? errors.target} />
          </fieldset>

          <div className="space-y-1">
            <Label htmlFor={`${id}-note`} className="text-xs">
              Note (optional)
            </Label>
            <textarea
              id={`${id}-note`}
              rows={2}
              maxLength={NOTE_MAX}
              value={draft.note}
              onChange={(e) => update({ note: e.target.value })}
              aria-invalid={Boolean(errors.note) || undefined}
              aria-describedby={errors.note ? `${id}-note-error` : undefined}
              className={TEXTAREA}
            />
            <FieldError id={`${id}-note-error`} message={errors.note} />
          </div>

          {evalOk ? (
            <div className="space-y-2 rounded-md border border-border p-2">
              <label className="inline-flex items-center gap-2 text-xs font-medium">
                <input
                  type="checkbox"
                  checked={draft.addToEval}
                  onChange={(e) => update({ addToEval: e.target.checked })}
                  className="size-4 accent-accent"
                />
                Add to the {target.kind === "fact" ? "golden (earnings) " : "labelled (news) "}eval set
              </label>
              {draft.addToEval && target.kind === "fact" ? (
                <>
                  <div className="space-y-1">
                    <Label htmlFor={`${id}-value`} className="text-xs">
                      Right value
                    </Label>
                    <Input
                      id={`${id}-value`}
                      inputMode="decimal"
                      className="h-8"
                      value={draft.correctedValue}
                      onChange={(e) => update({ correctedValue: e.target.value })}
                      aria-invalid={Boolean(errors.correctedValue) || undefined}
                      aria-describedby={`${id}-value-help${errors.correctedValue ? ` ${id}-value-error` : ""}`}
                    />
                    <p id={`${id}-value-help`} className="text-xs text-text-muted">
                      {valueHint ?? "In the scale the figure was printed in."}
                    </p>
                    <FieldError id={`${id}-value-error`} message={errors.correctedValue} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${id}-written`} className="text-xs">
                      Exactly as printed (optional)
                    </Label>
                    <Input
                      id={`${id}-written`}
                      className="h-8"
                      maxLength={40}
                      placeholder="e.g. 94,036"
                      value={draft.correctedValueAsWritten}
                      onChange={(e) => update({ correctedValueAsWritten: e.target.value })}
                      aria-invalid={Boolean(errors.correctedValueAsWritten) || undefined}
                    />
                    <FieldError id={`${id}-written-error`} message={errors.correctedValueAsWritten} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${id}-snippet`} className="text-xs">
                      Passage stating it (optional)
                    </Label>
                    <textarea
                      id={`${id}-snippet`}
                      rows={2}
                      maxLength={1000}
                      value={draft.correctedSnippet}
                      onChange={(e) => update({ correctedSnippet: e.target.value })}
                      aria-invalid={Boolean(errors.correctedSnippet) || undefined}
                      className={TEXTAREA}
                    />
                    <p className="text-xs text-text-muted">When the stored snippet does not contain the right number.</p>
                    <FieldError id={`${id}-snippet-error`} message={errors.correctedSnippet} />
                  </div>
                </>
              ) : null}
              {draft.addToEval && target.kind === "news_item" ? (
                <>
                  <div className="space-y-1">
                    <Label htmlFor={`${id}-event`} className="text-xs">
                      Right event type
                    </Label>
                    <select
                      id={`${id}-event`}
                      value={draft.label.eventType}
                      onChange={(e) => update({ label: { ...draft.label, eventType: e.target.value as EventType | "" } })}
                      aria-invalid={Boolean(errors.eventType) || undefined}
                      className="h-8 w-full rounded-md border border-border bg-surface-raised px-2 text-sm text-text"
                    >
                      <option value="">Choose…</option>
                      {EVENT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {EVENT_LABELS[t]}
                        </option>
                      ))}
                    </select>
                    <FieldError id={`${id}-event-error`} message={errors.eventType} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${id}-ticker`} className="text-xs">
                      Holding it affects (empty: none)
                    </Label>
                    <Input
                      id={`${id}-ticker`}
                      className="h-8 font-mono"
                      spellCheck={false}
                      value={draft.label.affectedTicker}
                      onChange={(e) => update({ label: { ...draft.label, affectedTicker: e.target.value } })}
                      aria-invalid={Boolean(errors.affectedTicker) || undefined}
                    />
                    <FieldError id={`${id}-ticker-error`} message={errors.affectedTicker} />
                  </div>
                  <div className="flex flex-wrap gap-3 text-xs">
                    <label className="inline-flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={draft.label.relevant}
                        onChange={(e) => update({ label: { ...draft.label, relevant: e.target.checked } })}
                        className="size-4 accent-accent"
                      />
                      Relevant
                    </label>
                    <label className="inline-flex items-center gap-1.5">
                      <input
                        type="checkbox"
                        checked={draft.label.rumour}
                        onChange={(e) => update({ label: { ...draft.label, rumour: e.target.checked } })}
                        className="size-4 accent-accent"
                      />
                      Rumour
                    </label>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}

          {failure ? (
            <p role="alert" className="flex items-start gap-1 text-xs text-text">
              <CircleAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning-signal" />
              {failure}
            </p>
          ) : null}
          <div className="flex justify-end gap-1.5">
            <Button type="button" size="sm" variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button type="submit" size="sm" disabled={pending}>
              {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SendIcon aria-hidden="true" />}
              Send
            </Button>
          </div>
        </form>,
            document.body,
          )
        : null}
    </span>
  );
}

function Effects({ mark }: { mark: FeedbackOut }) {
  const parts = effectParts(mark.effects);
  if (parts.length === 0) return <>Saved.</>;
  return (
    <>
      {parts.map((p, i) => (
        <span key={p.text}>
          {i > 0 ? "; " : ""}
          {p.href ? (
            <Link href={p.href} className="text-accent hover:underline">
              {p.text}
            </Link>
          ) : (
            p.text
          )}
        </span>
      ))}
    </>
  );
}
