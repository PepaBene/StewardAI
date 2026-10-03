"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";
import { decideDisclosure, resolveContradiction, reviewFilingHit, type ActionResult } from "@/lib/network/actions";
import { countByStatus, directionLabel, humanize, reviewState, shareBasisLabel } from "@/lib/network/logic";
import type { Contradiction, FilingHit, NetworkDisclosure, ReviewQueue } from "@/lib/network/types";
import { cn } from "@/lib/utils";

import { RowStatusBadge, SourceLink } from "./badges";
import { DecisionControl } from "./decision-control";

/**
 * Passage-level relationship review (D5.8): every customer, supplier or award claim is a
 * passage the owner reads, with both counterparties and the direction resolved, before it is
 * confirmed. A keyword hit alone is never a relationship.
 */
export function ReviewPanel({
  ticker,
  disclosures,
  queue,
  queueStatus = "ok",
  queueReason = null,
  asOfDate,
  onChanged,
}: {
  ticker: string;
  disclosures: readonly NetworkDisclosure[];
  queue: ReviewQueue | null;
  /** Whether the filing queue itself loaded: a failed or loading queue is not an empty one. */
  queueStatus?: "ok" | "loading" | "unavailable";
  queueReason?: string | null;
  asOfDate: string | null;
  onChanged: () => void;
}) {
  const counts = countByStatus(disclosures);
  const open = disclosures.filter((d) => d.status !== "rejected");
  return (
    <Blueprint as="section" aria-labelledby="network-review-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading kicker="Relationship review" title={<span id="network-review-heading">Passages awaiting a decision</span>} />
      <p className="max-w-prose text-sm text-text">
        {disclosures.length === 0 && queueStatus === "ok" && !queue?.filingHits.length && !queue?.contradictions.length
          ? "No relationship passage is stored for this company."
          : `${counts.suggested} suggested and ${counts.disputed} disputed passages, ${counts.confirmed} confirmed, ${counts.rejected} rejected${counts.unrecognised ? `, ${counts.unrecognised} with an unrecognised status` : ""}${queue ? `; ${queue.filingHits.filter((h) => h.reviewState !== "reviewed").length} filings still to read and ${queue.contradictions.length} open contradictions` : "; the filing queue and open contradictions are not shown below"}.`}
      </p>
      {queueStatus === "loading" ? (
        <p role="status" className="text-xs text-text-muted">Loading the filing queue and open contradictions…</p>
      ) : queueStatus === "unavailable" ? (
        <p role="alert" className="border border-warning-signal/40 bg-warning-signal-subtle p-2 text-xs text-text">
          Filings to read and open contradictions could not be loaded{queueReason ? ` (${queueReason})` : ""}. An empty list here would not mean none are open.
        </p>
      ) : null}
      {asOfDate ? <p className="text-xs text-text">Decisions apply now, not at {formatDate(asOfDate)}; the list shows the passages as known on that day.</p> : null}

      {queue && queue.filingHits.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold">Filings to read</h3>
          <ul className="mt-1 flex flex-col">
            {queue.filingHits.map((h) => (
              <HitRow key={h.id} hit={h} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      ) : null}

      {queue && queue.contradictions.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold">Open contradictions</h3>
          <ul className="mt-1 flex flex-col gap-2">
            {queue.contradictions.map((c) => (
              <ContradictionRow key={c.id} c={c} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      ) : null}

      {open.length > 0 ? (
        <div>
          <h3 className="text-sm font-semibold">Passages for {ticker}</h3>
          <ul className="mt-1 flex flex-col gap-3">
            {open.map((d) => (
              <DisclosureCard key={d.id} ticker={ticker} d={d} readOnly={asOfDate !== null} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      ) : null}

      <ProvenanceFooter
        provenance={{
          source: "SEC filings and issuer disclosures, passage-level review queue",
          asOf: asOfDate,
          method: "Both counterparties and the direction are resolved by the owner; named and anonymous customers are kept apart; contradicting evidence is recorded.",
          state: reviewState(disclosures),
          reason: open.length === 0 ? (disclosures.length === 0 ? "No relationship passage is stored." : "Every stored passage was rejected.") : null,
          coverage: open.length ? { covered: counts.confirmed, total: open.length, unit: "passages confirmed" } : null,
        }}
      />
    </Blueprint>
  );
}

function useAction() {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ActionResult | null>(null);
  async function run(fn: () => Promise<ActionResult>, onOk: () => void) {
    setPending(true);
    setResult(null);
    const r = await fn();
    setPending(false);
    setResult(r);
    if (r.ok) onOk();
  }
  return { pending, result, run };
}

export function HitRow({ hit, onChanged }: { hit: FilingHit; onChanged: () => void }) {
  const a = useAction();
  const [armed, setArmed] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"confirm" | "trigger" | null>(null);
  // Arming swaps one button for another: move focus with it so keyboard users are not dropped.
  useEffect(() => {
    if (focusNext.current === "confirm") confirmRef.current?.focus();
    else if (focusNext.current === "trigger") triggerRef.current?.focus();
    focusNext.current = null;
  }, [armed]);
  function arm(on: boolean) {
    focusNext.current = on ? "confirm" : "trigger";
    setArmed(on);
  }
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 border-t border-border-soft py-1.5 text-sm first:border-0">
      <span className="min-w-0">
        {hit.form ?? "Filing"} by {hit.filerName ?? "unknown filer"}
        <span className="num text-xs text-text-muted"> · {hit.filedAt ? formatDate(hit.filedAt.slice(0, 10)) : "undated"} · {hit.reviewState}</span>
        {hit.url ? <> · <SourceLink href={hit.url}>open filing</SourceLink></> : null}
        {hit.reviewNote ? <span className="block text-xs text-text-muted">{hit.reviewNote}</span> : null}
        {a.result ? <span role={a.result.ok ? "status" : "alert"} className="block text-xs text-text">{a.result.message}</span> : null}
      </span>
      {hit.reviewState !== "reviewed" ? (
        armed ? (
          <span className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirm reading this filing">
            <span className="text-xs text-text-muted">Fetches the filing and stores passages that name the holding as suggestions.</span>
            <Button
              ref={confirmRef}
              type="button"
              size="sm"
              disabled={a.pending}
              onClick={() => {
                focusNext.current = "trigger";
                void a.run(() => reviewFilingHit(hit.id), onChanged).then(() => setArmed(false));
              }}
            >
              {a.pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
              Confirm: read filing
            </Button>
            <Button type="button" size="sm" variant="outline" disabled={a.pending} onClick={() => arm(false)}>
              Cancel
            </Button>
          </span>
        ) : (
          <Button ref={triggerRef} type="button" size="sm" variant="outline" onClick={() => arm(true)}>
            Read passages…
          </Button>
        )
      ) : null}
    </li>
  );
}

function ContradictionRow({ c, onChanged }: { c: Contradiction; onChanged: () => void }) {
  const id = useId();
  const a = useAction();
  const [note, setNote] = useState("");
  const bad = note.trim().length < 5;
  return (
    <li className="flex flex-col gap-1.5 border border-disputed/40 bg-disputed-subtle p-2 text-sm">
      <p>
        <strong>{humanize(c.kind)}</strong> · {humanize(c.relationship)} · {c.fiscalPeriod ?? "no fiscal period"} · disclosure #{c.disclosureId ?? "?"}
        {c.otherDisclosureId ? ` against #${c.otherDisclosureId}` : ""}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 space-y-1">
          <Label htmlFor={`${id}-note`}>Resolution note (at least 5 characters)</Label>
          <input
            id={`${id}-note`}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={1000}
            className="h-9 w-full min-w-0 border border-border bg-surface-raised px-3 text-sm outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30"
          />
        </div>
        {(["resolved", "dismissed"] as const).map((r) => (
          <Button key={r} type="button" size="sm" variant="outline" disabled={a.pending || bad} onClick={() => void a.run(() => resolveContradiction(c.id, r, note), onChanged)}>
            {humanize(r)}
          </Button>
        ))}
      </div>
      {a.result ? <p role={a.result.ok ? "status" : "alert"} className="text-xs">{a.result.message}</p> : null}
    </li>
  );
}

function DisclosureCard({ ticker, d, readOnly, onChanged }: { ticker: string; d: NetworkDisclosure; readOnly: boolean; onChanged: () => void }) {
  const who = d.anonymous ? "Anonymous counterparty (never merged with a named one)" : (d.counterpartyName ?? d.counterpartyKey ?? "Counterparty not resolved");
  const unresolved = !d.anonymous && !d.counterpartyName && !d.counterpartyKey;
  return (
    <li className={cn("flex flex-col gap-2 border p-3 text-sm", d.status === "confirmed" ? "border-border" : "border-dashed border-neutral-signal")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 font-medium">
          {humanize(d.relationship)}: {who}
        </p>
        <RowStatusBadge status={d.status} />
      </div>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-2">
        <div><dt className="inline text-text-muted">Direction: </dt><dd className="inline">{directionLabel(d.direction)}{d.direction === "unknown" ? " (excluded from concentration)" : ""}</dd></div>
        <div><dt className="inline text-text-muted">Reported share: </dt><dd className="num inline">{d.reportedShare === null ? "not reported" : `${formatPct(d.reportedShare, 1)} ${d.shareBasis ? shareBasisLabel(d.shareBasis) : "(basis not stated)"}`}</dd></div>
        <div><dt className="inline text-text-muted">Fiscal period: </dt><dd className="num inline">{d.fiscalPeriod ?? "not stated"}</dd></div>
        <div><dt className="inline text-text-muted">Source: </dt><dd className="inline"><SourceLink href={d.sourceUrl}>{d.source}</SourceLink></dd></div>
      </dl>
      {unresolved ? <p className="text-xs text-text">The counterparty is not resolved: confirmation needs a named counterparty or an explicit anonymous flag.</p> : null}
      {d.passage ? <blockquote className="border-l-2 border-border pl-3 text-sm whitespace-pre-wrap">{d.passage}</blockquote> : <p className="text-xs text-text-muted">No passage stored.</p>}
      {d.decisionReason ? <p className="text-xs text-text-muted">Decision on record: {d.decisionReason}</p> : null}
      <details>
        <summary className="cursor-pointer text-xs text-accent-ink select-none hover:underline">Decide on this passage</summary>
        <DecisionControl status={d.status} readOnly={readOnly} onDecide={(dec, reason) => decideDisclosure(ticker, d.id, dec, reason)} onDone={onChanged} />
      </details>
    </li>
  );
}
