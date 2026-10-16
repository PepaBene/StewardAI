"use client";

import { LoaderCircleIcon, SparklesIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import type { MetricInfo } from "@/lib/discover/discover";
import { chipText, draftFilters, type ParseResult } from "@/lib/discover/screen-draft";

/*
 * "Ask for a screen" (insight I10.3): a sentence becomes conditions in the builder below, where
 * the owner can change them. Phrases that map to no metric are listed, a request for a forecast
 * is refused, and nothing runs until the owner presses Run (or turns on running automatically).
 */

const EXAMPLE = "US dividend payers yielding over 4% with low debt and positive earnings growth";

function errorText(status: number, error: unknown): string {
  const d = error && typeof error === "object" && "detail" in error ? (error as { detail: unknown }).detail : null;
  const detail = typeof d === "string" ? d : Array.isArray(d) ? "Write between 3 and 1000 characters." : "";
  if (status === 429) return `The AI budget stopped this request. ${detail}`;
  if (status === 409) return detail || "The screen reader is not available right now.";
  return `The request was not read (HTTP ${status}). ${detail}`;
}

export function AskScreen({
  catalogue,
  onApply,
}: {
  catalogue: readonly MetricInfo[];
  /** Replace the builder's conditions (and sort) with the draft; `run` asks to run it at once. */
  onApply: (result: ParseResult, run: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ParseResult | null>(null);
  const [autoRun, setAutoRun] = useState(false);

  async function ask() {
    const sentence = text.trim();
    if (sentence.length < 3) return setError("Write a sentence describing the stocks you want.");
    setBusy(true);
    setError(null);
    try {
      const r = await getBrowserApiClient().POST("/discover/parse", { body: { text: sentence } });
      if (r.response.ok && r.data) {
        setResult(r.data);
        if (!r.data.draft.refused) onApply(r.data, autoRun);
      } else setError(errorText(r.response.status, r.error));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const d = result?.draft;
  const chips = d ? draftFilters(d) : [];
  return (
    <section aria-labelledby="ask-heading" className="space-y-3 border border-border bg-surface-muted px-4 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id="ask-heading" className="text-base font-semibold">
          Ask for a screen
        </h3>
        <label className="flex items-center gap-2 text-xs text-text-muted">
          <input type="checkbox" checked={autoRun} onChange={(e) => setAutoRun(e.target.checked)} />
          Run it as soon as it is read
        </label>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
        className="flex flex-col gap-2 sm:flex-row"
      >
        <label htmlFor="ask-text" className="sr-only">
          Describe the stocks you want
        </label>
        <textarea
          id="ask-text"
          rows={2}
          maxLength={1000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void ask();
            }
          }}
          placeholder={EXAMPLE}
          className="min-h-[3.25rem] flex-1 border border-border bg-surface-raised px-2.5 py-2 text-sm focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none"
        />
        <Button type="submit" disabled={busy} className="self-start">
          {busy ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <SparklesIcon aria-hidden="true" />}
          Read request
        </Button>
      </form>
      <p className="text-xs text-text-muted">
        One AI call turns the sentence into conditions below. Nothing runs until you press Run{autoRun ? " (or right away, as you chose)" : ""}.
      </p>
      {error ? (
        <p role="alert" className="text-sm text-loss">
          {error}
        </p>
      ) : null}
      {d ? (
        <div className="space-y-2" aria-live="polite">
          {d.refused ? (
            <p className="text-sm">
              <span className="font-semibold">Not a screen: </span>
              {d.refusal_reason ?? "No metric can measure that."} Try describing what the companies are like today, not what they will do.
            </p>
          ) : (
            <>
              <p className="text-sm">
                {chips.length ? "Filled in below. Change any condition before running:" : "No condition could be read from that sentence."}
              </p>
              {chips.length ? (
                <ul className="flex flex-wrap gap-1.5" aria-label="Conditions read from the request">
                  {chips.map((c, i) => (
                    <li key={`${c.metric}-${c.op}-${i}`} className="border border-border bg-surface-raised px-2 py-0.5 text-xs">
                      {chipText(c, catalogue)}
                    </li>
                  ))}
                </ul>
              ) : null}
              {(d.filters ?? []).some((f) => f.note) ? (
                <p className="text-xs text-text-muted">
                  {(d.filters ?? [])
                    .filter((f) => f.note)
                    .map((f) => `${catalogue.find((m) => m.id === f.metric_id)?.label ?? f.metric_id}: ${f.note}`)
                    .join("; ")}
                </p>
              ) : null}
            </>
          )}
          {d.unmatched_phrases?.length ? (
            <div className="text-sm">
              <span className="font-semibold">Not understood, so left out:</span>
              <ul className="mt-1 list-disc pl-5 text-text-muted">
                {d.unmatched_phrases.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {d.keywords?.length ? (
            <p className="text-xs text-text-muted">Names and themes are not a filter yet: {d.keywords.join(", ")}.</p>
          ) : null}
          <p className="flex items-center justify-between gap-2 text-[11px] text-text-faint">
            <span>
              {result?.cached ? "Answered from the cache. " : ""}
              {result?.evaluation}
            </span>
            <button type="button" onClick={() => setResult(null)} aria-label="Dismiss the reading" className="text-text-muted hover:text-text">
              <XIcon aria-hidden="true" className="size-3.5" />
            </button>
          </p>
        </div>
      ) : null}
    </section>
  );
}
