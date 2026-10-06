"use client";

import { useState } from "react";

import { EmptyState } from "@/components/empty-state";
import { Panel } from "@/components/oversight/panel";
import { SaveNote, type SaveState } from "@/components/reference/form-bits";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { DECISION_LABEL, decisionBody, decisionsFor, DECISION_RESULT, safeUrl, shareText, type Disclosure, type FilingHit, type GraphDecision } from "@/lib/relationships/relationships";

import { DecisionHistory } from "./all-records-panel";
import { callApi, DecisionForm } from "./decision-form";
import { Fact, SourceLink, StatusMark } from "./status-mark";

const REL_WORD: Record<string, string> = { customer: "Customer", supplier: "Supplier", procurement_award: "Procurement award" };
const DIR_WORD: Record<string, string> = {
  issuer_sells_to: "The holding sells to this counterparty",
  issuer_buys_from: "The holding buys from this counterparty",
  award_to_issuer: "The counterparty awarded a contract to the holding",
  unknown: "Direction not stated",
};

export function decideDisclosure(ticker: string, id: number, decision: GraphDecision, reason: string) {
  return callApi(
    () => getBrowserApiClient().POST("/holdings/{ticker}/relationship-disclosures/{disclosure_id}/decision", { params: { path: { ticker, disclosure_id: id } }, body: decisionBody(decision, reason) }),
    `Saved: relationship ${id} is now ${DECISION_RESULT[decision]}.`,
  );
}

/** One disclosure with its evidence passage, source link and a decision form. Never auto-merged. */
export function DisclosureCard({ d, onChanged }: { d: Disclosure; onChanged: () => void }) {
  const link = safeUrl(d.sourceUrl);
  const options = decisionsFor(d.status).map((v) => ({ value: v, label: DECISION_LABEL[v] }));
  return (
    <li className="space-y-2 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusMark status={d.status} />
        <span className="num text-sm font-semibold">{d.ticker}</span>
        <span className="text-sm">{REL_WORD[d.relationship] ?? d.relationship}</span>
        <span className="text-sm text-text-muted">with</span>
        <span className="text-sm font-medium">{d.counterparty}</span>
        <span className="num ml-auto text-xs text-text-muted">#{d.id}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-3">
        <Fact label="Direction">{DIR_WORD[d.direction] ?? d.direction}</Fact>
        <Fact label="Share of revenue or cost">{shareText(d.share)}{d.shareBasis ? ` (${d.shareBasis})` : ""}</Fact>
        <Fact label="Fiscal period">{d.fiscalPeriod}</Fact>
      </dl>
      {d.passage ? (
        <blockquote className="max-w-[76ch] border-l-2 border-border pl-3 text-sm italic">{d.passage}</blockquote>
      ) : (
        <p className="text-sm text-text-muted">No passage stored. Confirming needs a passage or linked evidence.</p>
      )}
      <p className="text-xs text-text-muted">
        <SourceLink href={link} /> {d.source ? `· ${d.source}` : ""} {d.origin ? `· found by ${d.origin.replaceAll("_", " ")}` : ""}
      </p>
      {d.decisionReason ? (
        <p className="text-xs text-text-muted">
          Decided by {d.decisionReason.startsWith("AI approval") ? "the AI" : "you"}: {d.decisionReason}
        </p>
      ) : null}
      <DecisionHistory kind="suggestion" id={d.id} />
      {d.ticker ? (
        <DecisionForm
          options={options}
          onSubmit={async (v, reason) => {
            const r = await decideDisclosure(d.ticker!, d.id, v as GraphDecision, reason);
            if (r.ok) onChanged();
            return r;
          }}
        />
      ) : null}
    </li>
  );
}

function HitRow({ h, onChanged }: { h: FilingHit; onChanged: () => void }) {
  const [state, setState] = useState<SaveState>({ kind: "idle" });
  async function read() {
    setState({ kind: "saving" });
    const r = await callApi(() => getBrowserApiClient().POST("/relationship-review/hits/{hit_id}/review", { params: { path: { hit_id: h.id } } }), `Read filing ${h.id}. Passages now appear as suggested relationships.`);
    setState(r.ok ? { kind: "ok", text: r.text } : { kind: "error", text: r.text });
    if (r.ok) onChanged();
  }
  const link = safeUrl(h.url);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border border-border-soft px-3 py-2 text-sm">
      <span className="num font-semibold">{h.ticker}</span>
      <span>
        {h.form ?? "Filing"} by {h.filer ?? "an unnamed filer"}
      </span>
      <span className="num text-xs text-text-muted">{h.filedAt?.slice(0, 10) ?? "date not recorded"}</span>
      <span className="text-xs text-text-muted">{h.state === "failed" ? `Last read failed${h.note ? `: ${h.note}` : ""}` : h.state === "fetched" ? "Fetched, not yet read" : "Not read yet"}</span>
      <SourceLink href={link} label="Open filing" />
      <Button type="button" size="sm" variant="outline" onClick={read} disabled={state.kind === "saving"} className="ml-auto">
        Read passages now
      </Button>
      <SaveNote state={state} />
    </li>
  );
}

/** Section 1: relationships suggested from filings that wait for the owner's decision. */
export function SuggestionsPanel({ hits, disclosures, onChanged }: { hits: FilingHit[]; disclosures: Disclosure[]; onChanged: () => void }) {
  return (
    <Panel
      id="suggested"
      title="Suggested relationships"
      description="Customer and supplier links found in filings. Nothing is merged or trusted until you confirm it; a confirmed link needs its source passage, and every decision needs a reason."
    >
      {disclosures.length === 0 ? (
        <EmptyState title="Nothing awaits a decision">Suggested relationships appear here when a filing names a counterparty of one of your holdings.</EmptyState>
      ) : (
        <ul className="space-y-3">
          {disclosures.map((d) => (
            <DisclosureCard key={d.id} d={d} onChanged={onChanged} />
          ))}
        </ul>
      )}
      {hits.length > 0 ? (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Filings waiting to be read ({hits.length})</h3>
          <ul className="space-y-2">
            {hits.map((h) => (
              <HitRow key={h.id} h={h} onChanged={onChanged} />
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
