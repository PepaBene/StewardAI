"use client";

import { LoaderCircleIcon } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  recordDealEvent,
  recordDisclosureReview,
  suggestEdge,
  type ActionResult,
  type DisclosureReview,
  type EdgeSuggestion,
  type EventSuggestion,
} from "@/lib/network/actions";

const SELECT =
  "h-9 w-full min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30";

function Field({ label, children, wide }: { label: string; children: (id: string) => ReactNode; wide?: boolean }) {
  const id = useId();
  return (
    <div className={wide ? "space-y-1 sm:col-span-2" : "space-y-1"}>
      <Label htmlFor={id}>{label}</Label>
      {children(id)}
    </div>
  );
}

function FormShell({ title, hint, onSubmit, children }: { title: string; hint: string; onSubmit: () => Promise<ActionResult>; children: ReactNode }) {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ActionResult | null>(null);
  return (
    <details className="border border-border-soft">
      <summary className="cursor-pointer px-3 py-1.5 text-sm font-medium text-accent-ink select-none hover:underline">{title}</summary>
      <form
        className="flex flex-col gap-3 border-t border-border-soft p-3"
        onSubmit={(e) => {
          e.preventDefault();
          setPending(true);
          setResult(null);
          void onSubmit().then((r) => {
            setPending(false);
            setResult(r);
          });
        }}
      >
        <p className="text-xs text-text-muted">{hint}</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={pending}>
            {pending ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            Record as suggested
          </Button>
          {result ? (
            <p role={result.ok ? "status" : "alert"} className="min-w-0 text-xs text-text">
              {result.ok ? "Recorded. " : "Not recorded. "}
              {result.message}
            </p>
          ) : null}
        </div>
      </form>
    </details>
  );
}

const EDGE_TYPES: EdgeSuggestion["edgeType"][] = ["partner", "customer", "supplier", "competitor", "acquirer", "acquired", "investor", "investment", "product", "technology", "executive"];

/**
 * Manual entries. Every form records a SUGGESTED row: confirming is a separate, reasoned
 * decision on the row itself, so typing a relationship here never makes it a fact.
 */
export function RecordForms({ ticker, onRecorded }: { ticker: string; onRecorded: () => void }) {
  const [edge, setEdge] = useState<EdgeSuggestion>({
    subjectKey: ticker,
    objectKey: "",
    objectName: "",
    edgeType: "partner",
    direction: "symmetric",
    source: "owner_entry",
    sourceUrl: "",
    validFrom: "",
    validTo: "",
    passage: "",
  });
  const [disc, setDisc] = useState<DisclosureReview>({
    relationship: "customer",
    direction: "issuer_sells_to",
    counterpartyName: "",
    anonymous: false,
    sharePercent: "",
    shareBasis: "issuer_revenue",
    fiscalPeriod: "",
    validFrom: "",
    passage: "",
    source: "owner_review",
    sourceUrl: "",
  });
  const [ev, setEv] = useState<EventSuggestion>({ eventType: "deal", eventState: "announced", counterparty: "", date: "", source: "owner_entry", sourceUrl: "" });

  const done = (r: ActionResult) => {
    if (r.ok) onRecorded();
    return r;
  };

  return (
    <Blueprint as="section" aria-labelledby="network-record-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Owner entries" title={<span id="network-record-heading">Record evidence</span>} />
      <p className="max-w-prose text-sm text-text-muted">Entries are stored as suggestions with the source you cite. They become facts only through a confirmation with a reason.</p>

      <FormShell title="Suggest a relationship" hint="A partner, customer, acquirer or other typed edge with its source and validity." onSubmit={() => suggestEdge(edge).then(done)}>
        <Field label="Counterparty name">{(id) => <Input id={id} required value={edge.objectName} onChange={(e) => setEdge({ ...edge, objectName: e.target.value })} />}</Field>
        <Field label="Counterparty key (ticker or LEI, optional)">{(id) => <Input id={id} value={edge.objectKey} onChange={(e) => setEdge({ ...edge, objectKey: e.target.value })} />}</Field>
        <Field label="Relationship type">
          {(id) => (
            <select id={id} className={SELECT} value={edge.edgeType} onChange={(e) => setEdge({ ...edge, edgeType: e.target.value as EdgeSuggestion["edgeType"] })}>
              {EDGE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
        </Field>
        <Field label="Direction">
          {(id) => (
            <select id={id} className={SELECT} value={edge.direction} onChange={(e) => setEdge({ ...edge, direction: e.target.value as EdgeSuggestion["direction"] })}>
              <option value="symmetric">Mutual</option>
              <option value="outgoing">Company to counterparty</option>
              <option value="incoming">Counterparty to company</option>
            </select>
          )}
        </Field>
        <Field label="Source name">{(id) => <Input id={id} required minLength={2} value={edge.source} onChange={(e) => setEdge({ ...edge, source: e.target.value })} />}</Field>
        <Field label="Source URL">{(id) => <Input id={id} required type="url" minLength={8} value={edge.sourceUrl} onChange={(e) => setEdge({ ...edge, sourceUrl: e.target.value })} />}</Field>
        <Field label="Valid from">{(id) => <Input id={id} type="date" value={edge.validFrom} onChange={(e) => setEdge({ ...edge, validFrom: e.target.value })} />}</Field>
        <Field label="Valid to (blank = open)">{(id) => <Input id={id} type="date" value={edge.validTo} onChange={(e) => setEdge({ ...edge, validTo: e.target.value })} />}</Field>
        <Field label="Source passage" wide>
          {(id) => <textarea id={id} rows={3} className={`${SELECT} h-auto py-1.5`} value={edge.passage} onChange={(e) => setEdge({ ...edge, passage: e.target.value })} />}
        </Field>
      </FormShell>

      <FormShell title="Record a relationship passage" hint="A customer, supplier or award passage read from a filing. Named and anonymous counterparties stay separate." onSubmit={() => recordDisclosureReview(ticker, disc).then(done)}>
        <Field label="Relationship">
          {(id) => (
            <select id={id} className={SELECT} value={disc.relationship} onChange={(e) => setDisc({ ...disc, relationship: e.target.value as DisclosureReview["relationship"] })}>
              <option value="customer">Customer</option>
              <option value="supplier">Supplier</option>
              <option value="procurement_award">Procurement award</option>
            </select>
          )}
        </Field>
        <Field label="Direction">
          {(id) => (
            <select id={id} className={SELECT} value={disc.direction} onChange={(e) => setDisc({ ...disc, direction: e.target.value as DisclosureReview["direction"] })}>
              <option value="issuer_sells_to">Company sells to counterparty</option>
              <option value="issuer_buys_from">Company buys from counterparty</option>
              <option value="award_to_issuer">Award to company</option>
              <option value="unknown">Unknown (excluded from concentration)</option>
            </select>
          )}
        </Field>
        <Field label="Counterparty name">{(id) => <Input id={id} disabled={disc.anonymous} value={disc.counterpartyName} onChange={(e) => setDisc({ ...disc, counterpartyName: e.target.value })} />}</Field>
        <div className="flex items-end gap-2 pb-2">
          <input id="network-anon" type="checkbox" checked={disc.anonymous} onChange={(e) => setDisc({ ...disc, anonymous: e.target.checked, counterpartyName: e.target.checked ? "" : disc.counterpartyName })} />
          <Label htmlFor="network-anon">Anonymous (the filing does not name it)</Label>
        </div>
        <Field label="Reported share (%, blank if none)">{(id) => <Input id={id} inputMode="decimal" value={disc.sharePercent} onChange={(e) => setDisc({ ...disc, sharePercent: e.target.value })} />}</Field>
        <Field label="Share basis">
          {(id) => (
            <select id={id} className={SELECT} value={disc.shareBasis} onChange={(e) => setDisc({ ...disc, shareBasis: e.target.value })}>
              <option value="issuer_revenue">Company revenue</option>
              <option value="issuer_purchases">Company purchases</option>
              <option value="">Not stated</option>
            </select>
          )}
        </Field>
        <Field label="Fiscal period (for example FY2025)">{(id) => <Input id={id} value={disc.fiscalPeriod} onChange={(e) => setDisc({ ...disc, fiscalPeriod: e.target.value })} />}</Field>
        <Field label="Valid from">{(id) => <Input id={id} type="date" value={disc.validFrom} onChange={(e) => setDisc({ ...disc, validFrom: e.target.value })} />}</Field>
        <Field label="Source name">{(id) => <Input id={id} required value={disc.source} onChange={(e) => setDisc({ ...disc, source: e.target.value })} />}</Field>
        <Field label="Source URL">{(id) => <Input id={id} required type="url" value={disc.sourceUrl} onChange={(e) => setDisc({ ...disc, sourceUrl: e.target.value })} />}</Field>
        <Field label="Passage (at least 20 characters)" wide>
          {(id) => <textarea id={id} required minLength={20} rows={4} className={`${SELECT} h-auto py-1.5`} value={disc.passage} onChange={(e) => setDisc({ ...disc, passage: e.target.value })} />}
        </Field>
      </FormShell>

      <FormShell title="Record a deal or financing state" hint="One state change with its source. The server refuses transitions the deal state machine does not allow." onSubmit={() => recordDealEvent(ticker, ev).then(done)}>
        <Field label="Type">
          {(id) => (
            <select id={id} className={SELECT} value={ev.eventType} onChange={(e) => setEv({ ...ev, eventType: e.target.value as EventSuggestion["eventType"] })}>
              <option value="deal">Deal</option>
              <option value="financing">Financing</option>
            </select>
          )}
        </Field>
        <Field label="State">
          {(id) => (
            <select id={id} className={SELECT} value={ev.eventState} onChange={(e) => setEv({ ...ev, eventState: e.target.value as EventSuggestion["eventState"] })}>
              {(["proposed", "announced", "amended", "approved", "closed", "terminated", "reversed"] as const).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          )}
        </Field>
        <Field label="Counterparty">{(id) => <Input id={id} value={ev.counterparty} onChange={(e) => setEv({ ...ev, counterparty: e.target.value })} />}</Field>
        <Field label="Date of the state">{(id) => <Input id={id} type="date" value={ev.date} onChange={(e) => setEv({ ...ev, date: e.target.value })} />}</Field>
        <Field label="Source name">{(id) => <Input id={id} required value={ev.source} onChange={(e) => setEv({ ...ev, source: e.target.value })} />}</Field>
        <Field label="Source URL">{(id) => <Input id={id} required type="url" value={ev.sourceUrl} onChange={(e) => setEv({ ...ev, sourceUrl: e.target.value })} />}</Field>
      </FormShell>
    </Blueprint>
  );
}
