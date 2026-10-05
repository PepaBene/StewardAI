"use client";

import { useState } from "react";

import { Field, SaveNote, SELECT_CLASS, TEXTAREA_CLASS, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiErrorText } from "@/lib/reference/form-utils";
import { DECISION_REASON_MAX, validateReason } from "@/lib/relationships/relationships";

export type DecisionResult = { ok: true; text: string } | { ok: false; text: string };

/** Runs one API call and turns it into words; a thrown network error is reported, never swallowed. */
export async function callApi(fn: () => Promise<{ response: Response; error?: unknown }>, okText: string): Promise<DecisionResult> {
  try {
    const r = await fn();
    return r.response.ok ? { ok: true, text: okText } : { ok: false, text: apiErrorText(r.error, r.response.status) };
  } catch (err) {
    return { ok: false, text: `Not saved: ${err instanceof Error ? err.message : String(err)}` };
  }
}

export type DecisionOption = { value: string; label: string };

/**
 * One owner decision on one row: choose what to do, say why, save. The reason is always required.
 * Nothing is applied until the owner presses the button, and the list reloads after a success.
 */
export function DecisionForm({
  options,
  reasonMin = 10,
  reasonMax = DECISION_REASON_MAX,
  withEvidence,
  validate,
  onSubmit,
  summary = "Decide",
  confirmText,
}: {
  /** When set, the first press asks "are you sure" with this text and the second one submits. */
  confirmText?: (value: string) => string;
  options: readonly DecisionOption[];
  reasonMin?: number;
  reasonMax?: number;
  /** Show an optional evidence number (corporate actions). */
  withEvidence?: boolean;
  validate?: (value: string, reason: string, evidence: string) => { reason?: string; evidence?: string };
  onSubmit: (value: string, reason: string, evidence: string) => Promise<DecisionResult>;
  summary?: string;
}) {
  const [value, setValue] = useState(options[0]?.value ?? "");
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [touched, setTouched] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const errors = validate ? validate(value, reason, evidence) : { reason: validateReason(reason, reasonMin, reasonMax) };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (errors.reason || errors.evidence) return;
    // A second, explicit press records it: nothing is sent on the first one.
    if (confirmText && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    setSave({ kind: "saving" });
    const r = await onSubmit(value, reason, evidence);
    if (r.ok) {
      setSave({ kind: "ok", text: r.text });
      setReason("");
      setEvidence("");
      setTouched(false);
    } else setSave({ kind: "error", text: r.text });
  }

  const label = options.find((o) => o.value === value)?.label ?? "Save";
  return (
    <details className="border-t border-border-soft pt-2">
      <summary className="cursor-pointer text-sm text-accent-ink hover:underline">{summary}</summary>
      <form onSubmit={submit} noValidate className="mt-2 space-y-3 border border-border bg-surface-muted p-3">
        <div className="grid gap-3 sm:grid-cols-[14rem_1fr]">
          <Field label="Decision">
            <select value={value} onChange={(e) => { setValue(e.target.value); setConfirming(false); }} className={SELECT_CLASS}>
              {options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          {withEvidence ? (
            <Field label="Evidence number" hint="Optional unless confirming and none is linked yet." error={touched ? errors.evidence : undefined}>
              <Input value={evidence} onChange={(e) => setEvidence(e.target.value)} inputMode="numeric" className="num" />
            </Field>
          ) : null}
        </div>
        <Field label="Reason (required)" hint={`${reason.trim().length} of ${reasonMax} characters, at least ${reasonMin}.`} error={touched ? errors.reason : undefined}>
          <textarea value={reason} onChange={(e) => { setReason(e.target.value); setConfirming(false); }} maxLength={reasonMax} className={TEXTAREA_CLASS} />
        </Field>
        {confirming && confirmText ? (
          <p role="alert" className="border border-dashed border-border bg-surface-raised px-3 py-2 text-sm">
            {confirmText(value)} Press the button again to record it, or change anything above to cancel.
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={save.kind === "saving"}>
            {confirming ? `Yes, record: ${label}` : label}
          </Button>
          {confirming ? (
            <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          ) : null}
          <SaveNote state={save} />
        </div>
      </form>
    </details>
  );
}
