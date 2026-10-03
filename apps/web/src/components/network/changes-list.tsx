"use client";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { ProvenanceFooter } from "@/components/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/lib/format";
import { isIsoDate, whatChanged } from "@/lib/network/logic";
import type { BusinessNetwork } from "@/lib/network/types";

import { RowStatusBadge, SourceLink } from "./badges";

const KIND_LABEL = { edge: "Relationship", event: "Event", disclosure: "Disclosure", patent: "Patent" } as const;

/**
 * "What changed since the last review": rows the app learned of after the review date. The
 * default date is the latest monthly review; the owner can pick another. The API records when
 * a row became known, not when its status last changed, and the list says so.
 */
export function ChangesList({
  net,
  since,
  onSince,
  sinceOrigin,
  asOfDate,
}: {
  net: BusinessNetwork;
  since: string;
  onSince: (v: string) => void;
  /** Where the default date came from ("latest monthly review 28 Sep 2026"), or null when none. */
  sinceOrigin: string | null;
  asOfDate: string | null;
}) {
  const valid = isIsoDate(since);
  const items = whatChanged(net, valid ? `${since}T23:59:59Z` : null, asOfDate ? `${asOfDate}T23:59:59Z` : null);
  return (
    <Blueprint as="section" aria-labelledby="network-changes-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Review" title={<span id="network-changes-heading">What changed since the last review</span>} />
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-44 space-y-1">
          <Label htmlFor="network-since">Changes after</Label>
          <Input id="network-since" type="date" value={since} onChange={(e) => onSince(e.target.value)} />
        </div>
        <p className="mb-1 min-w-0 text-xs text-text-muted">
          {sinceOrigin ?? "No monthly review is on record: choose a date. Nothing is listed without one."}
        </p>
      </div>
      <p className="max-w-prose text-sm text-text">
        {!valid
          ? "Choose a date to list what the app learned after it."
          : items.length === 0
            ? `Nothing new was recorded after ${formatDate(since)}.`
            : `${items.length} ${items.length === 1 ? "row was" : "rows were"} recorded after ${formatDate(since)}.`}
      </p>
      {items.length > 0 ? (
        <ul className="flex flex-col">
          {items.map((c) => (
            <li key={c.key} className="grid grid-cols-1 gap-x-3 gap-y-0.5 border-t border-border-soft py-2 first:border-0 sm:grid-cols-[6.5rem_minmax(0,1fr)_auto]">
              <span className="num text-xs text-text-muted">
                {formatDate(c.knownAt.slice(0, 10))}
                <span className="block">{KIND_LABEL[c.kind]}</span>
              </span>
              <span className="min-w-0 text-sm">
                {c.label}
                <span className="block text-xs text-text-muted">{c.detail}</span>
                {c.sourceUrl ? <SourceLink href={c.sourceUrl}>source</SourceLink> : null}
              </span>
              {c.status ? <RowStatusBadge status={c.status} /> : <span className="text-xs text-text-muted">no status</span>}
            </li>
          ))}
        </ul>
      ) : null}
      <ProvenanceFooter
        provenance={{
          source: "Company graph, events, disclosures and patents (known-at dates)",
          asOf: asOfDate,
          method: "Listed by when the app learned of a row. A later decision on an older row (confirm or reject) is not listed because the API exposes no decision date.",
          state: valid ? "verified" : "unavailable",
          reason: valid ? null : "No review date selected.",
        }}
      />
    </Blueprint>
  );
}
