"use client";

import { CheckIcon, FlagIcon, LoaderCircleIcon } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { FEEDBACK_KINDS, type NewsFeedbackKind, type NewsFeedbackTarget } from "@/lib/news/analysis";

const SELECT =
  "rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text shadow-xs outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

/**
 * Marks an item, story, claim or brief line: wrong company, duplicate, misread, a missing
 * source, useful or not. Marks go to the benchmark, never to model training.
 */
export function NewsFeedback({
  target,
  targetId,
  label,
}: {
  target: NewsFeedbackTarget;
  targetId: string | number;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<NewsFeedbackKind>("wrong_entity");
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const selectId = useId();

  async function send() {
    setState("sending");
    try {
      const { response } = await getBrowserApiClient().POST("/news/feedback", {
        body: { target, target_id: String(targetId), kind },
      });
      setState(response.ok ? "sent" : "error");
    } catch {
      setState("error");
    }
  }

  if (state === "sent") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-text-muted" role="status">
        <CheckIcon aria-hidden="true" className="size-3.5 text-positive-signal" />
        Marked
      </span>
    );
  }
  if (!open) {
    return (
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)} aria-label={`Mark ${label}`}>
        <FlagIcon aria-hidden="true" />
        Mark
      </Button>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <label htmlFor={selectId} className="sr-only">
        What is wrong with {label}
      </label>
      <select id={selectId} className={SELECT} value={kind} onChange={(e) => setKind(e.target.value as NewsFeedbackKind)}>
        {FEEDBACK_KINDS.map((k) => (
          <option key={k.id} value={k.id}>
            {k.label}
          </option>
        ))}
      </select>
      <Button size="sm" onClick={() => void send()} disabled={state === "sending"}>
        {state === "sending" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
        Send
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {state === "error" ? <span className="text-xs text-text">Could not save the mark. Try again.</span> : null}
    </span>
  );
}
