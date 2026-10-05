"use client";

import { EmptyState } from "@/components/empty-state";
import { Panel } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { titleCase } from "@/lib/parse-untyped";
import { DECISION_LABEL, decisionBody, decisionsFor, DECISION_RESULT, safeUrl, type CompanyEvent, type Disclosure, type GraphDecision } from "@/lib/relationships/relationships";

import { callApi, DecisionForm } from "./decision-form";
import { DisclosureCard } from "./suggestions-panel";
import { Fact, SourceLink, StatusMark } from "./status-mark";

function EventCard({ ticker, ev, onChanged }: { ticker: string; ev: CompanyEvent; onChanged: () => void }) {
  return (
    <li className="space-y-2 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusMark status={ev.status} />
        <span className="text-sm font-medium">{titleCase(ev.eventType)}</span>
        <span className="text-sm text-text-muted">{titleCase(ev.eventState)}</span>
        <span className="num ml-auto text-xs text-text-muted">#{ev.id}</span>
      </div>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-4">
        <Fact label="Announced">{ev.announcedAt?.slice(0, 10) ?? null}</Fact>
        <Fact label="Effective">{ev.effectiveAt?.slice(0, 10) ?? null}</Fact>
        <Fact label="Counterparty">{ev.counterparty}</Fact>
        <Fact label="Linked evidence">{ev.evidenceId === null ? "none, so it cannot be confirmed" : `#${ev.evidenceId}`}</Fact>
      </dl>
      <p className="text-xs text-text-muted">
        <SourceLink href={safeUrl(ev.sourceUrl)} /> {ev.source ? `· ${ev.source}` : ""}
      </p>
      <DecisionForm
        options={decisionsFor(ev.status).map((v) => ({ value: v, label: DECISION_LABEL[v] }))}
        onSubmit={async (v, reason) => {
          const r = await callApi(
            () => getBrowserApiClient().POST("/holdings/{ticker}/company-events/{event_id}/decision", { params: { path: { ticker, event_id: ev.id } }, body: decisionBody(v as GraphDecision, reason) }),
            `Saved: event ${ev.id} is now ${DECISION_RESULT[v as GraphDecision]}.`,
          );
          if (r.ok) onChanged();
          return r;
        }}
      />
    </li>
  );
}

/** Section 4: every disclosure and company event of one holding, whatever its status, so a decided row can be moved or reopened. */
export function EventsPanel({ ticker, disclosures, events, note, onChanged }: { ticker: string | null; disclosures: Disclosure[] | null; events: CompanyEvent[] | null; note: React.ReactNode; onChanged: () => void }) {
  return (
    <Panel
      id="decisions"
      title="Disclosure and event decisions"
      description="All recorded customer and supplier disclosures and company events for one holding. A confirmed row can be rejected or disputed; a rejected or disputed row can be sent back to review."
    >
      {note}
      {ticker === null ? (
        <EmptyState title="Choose a holding">Enter a ticker above to list its disclosures and events.</EmptyState>
      ) : (
        <>
          <h3 className="text-sm font-semibold">Disclosures ({disclosures?.length ?? 0})</h3>
          {disclosures && disclosures.length > 0 ? (
            <ul className="space-y-3">
              {disclosures.map((d) => (
                <DisclosureCard key={d.id} d={d} onChanged={onChanged} />
              ))}
            </ul>
          ) : disclosures ? (
            <p className="text-sm text-text-muted">No disclosures recorded for {ticker}.</p>
          ) : null}
          <h3 className="text-sm font-semibold">Company events ({events?.length ?? 0})</h3>
          {events && events.length > 0 ? (
            <ul className="space-y-3">
              {events.map((ev) => (
                <EventCard key={ev.id} ticker={ticker} ev={ev} onChanged={onChanged} />
              ))}
            </ul>
          ) : events ? (
            <p className="text-sm text-text-muted">No company events recorded for {ticker}.</p>
          ) : null}
        </>
      )}
    </Panel>
  );
}
