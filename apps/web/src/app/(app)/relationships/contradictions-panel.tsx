"use client";

import { EmptyState } from "@/components/empty-state";
import { Panel } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { titleCase as titleOf } from "@/lib/parse-untyped";
import { RESOLUTION_NOTE_MAX, RESOLUTION_NOTE_MIN, resolutionBody, safeUrl, shareText, validateReason, type Contradiction, type Disclosure } from "@/lib/relationships/relationships";

import { callApi, DecisionForm } from "./decision-form";
import { Fact, SourceLink, StatusMark } from "./status-mark";

function Side({ label, d, id }: { label: string; d: Disclosure | undefined; id: number | null }) {
  return (
    <div className="min-w-0 space-y-1.5 border border-border bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{label}</span>
        {d ? <StatusMark status={d.status} /> : null}
        <span className="num ml-auto text-xs text-text-muted">{id === null ? "no id" : `#${id}`}</span>
      </div>
      {d ? (
        <>
          <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
            <Fact label="Counterparty">{d.counterparty}</Fact>
            <Fact label="Share">{shareText(d.share)}</Fact>
            <Fact label="Fiscal period">{d.fiscalPeriod}</Fact>
            <Fact label="Source">{d.source}</Fact>
          </dl>
          {d.passage ? <blockquote className="border-l-2 border-border pl-3 text-sm italic">{d.passage}</blockquote> : null}
          <p className="text-xs">
            <SourceLink href={safeUrl(d.sourceUrl)} />
          </p>
        </>
      ) : (
        <p className="text-sm text-text-muted">This record is not in the open queue. Enter the holding below to load every recorded relationship and compare it here.</p>
      )}
    </div>
  );
}

/** Section 2: two sources that disagree about the same relationship, side by side. */
export function ContradictionsPanel({ items, byId, onChanged }: { items: Contradiction[]; byId: Map<number, Disclosure>; onChanged: () => void }) {
  return (
    <Panel id="contradictions" title="Contradictions between sources" description="Two disclosures that disagree about the same relationship. Resolving or dismissing closes the flag only; it does not change either disclosure.">
      {items.length === 0 ? (
        <EmptyState title="No open contradictions">A flag appears when two sources disagree about a counterparty, a share or a period.</EmptyState>
      ) : (
        <ul className="space-y-4">
          {items.map((c) => (
            <li key={c.id} className="space-y-2">
              <p className="text-sm">
                <span className="num font-semibold">{c.ticker}</span> {titleOf(c.relationship)}: {titleOf(c.kind)}
                {c.fiscalPeriod ? ` in ${c.fiscalPeriod}` : ""}
                <span className="num ml-2 text-xs text-text-muted">flag #{c.id}</span>
              </p>
              {c.detail ? <p className="text-xs text-text-muted">{c.detail}</p> : null}
              <div className="grid gap-3 md:grid-cols-2">
                <Side label="One source says" d={c.disclosureId === null ? undefined : byId.get(c.disclosureId)} id={c.disclosureId} />
                <Side label="The other says" d={c.otherId === null ? undefined : byId.get(c.otherId)} id={c.otherId} />
              </div>
              <DecisionForm
                summary="Close this flag"
                options={[
                  { value: "resolved", label: "Resolved: I settled which source is right" },
                  { value: "dismissed", label: "Dismissed: not a real conflict" },
                ]}
                reasonMin={RESOLUTION_NOTE_MIN}
                reasonMax={RESOLUTION_NOTE_MAX}
                validate={(_v, reason) => ({ reason: validateReason(reason, RESOLUTION_NOTE_MIN, RESOLUTION_NOTE_MAX) })}
                onSubmit={async (v, note) => {
                  const r = await callApi(
                    () => getBrowserApiClient().POST("/relationship-review/contradictions/{contradiction_id}/resolve", { params: { path: { contradiction_id: c.id } }, body: resolutionBody(v as "resolved" | "dismissed", note) }),
                    `Saved: flag ${c.id} is ${v}.`,
                  );
                  if (r.ok) onChanged();
                  return r;
                }}
              />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
