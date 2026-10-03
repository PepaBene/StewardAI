"use client";

import { useState } from "react";

import { DataStateBadge, Segmented } from "@/components/data-state";
import { EVIDENCE_DOMAINS, evidenceItems } from "@/lib/holding-data/evidence-items";
import { evidenceCounts } from "@/lib/holding-data/logic";
import type { EvidenceDomain, EvidenceSummary } from "@/lib/holding-data/types";

import { day, HoldingPanel, SourceLink } from "./shared";

/**
 * The backend's evidence layer for this holding, one domain at a time. Every row carries the
 * stored source passage (or why none is stored), the date it became known, the source link and,
 * where the backend gives one, the coverage reason. Domains with no rows say why.
 */
export function EvidenceLedger({ summary }: { summary: EvidenceSummary }) {
  const [domain, setDomain] = useState<EvidenceDomain>("filed-notes");
  const counts = evidenceCounts(summary);
  const current = counts.find((c) => c.domain === domain);
  const items = evidenceItems(summary, domain);
  return (
    <HoldingPanel
      id="evidence"
      kicker="Evidence"
      title="Source passage, known-at date and coverage reason"
      takeaway={`${counts.filter((c) => c.items > 0).length} of ${counts.length} evidence domains have rows at this cutoff.`}
      provenance={{
        source: "Stored filings, graph edges, deal events and consensus observations",
        asOf: null,
        knownAt: summary.known_as_of,
        method: "Derived figures cite the stored passage they rest on; where none is stored the reason is stated.",
        state: current ? current.state : "unavailable",
        reason: current?.reason ?? null,
        coverage: { covered: counts.filter((c) => c.state === "verified").length, total: counts.length, unit: "evidence domains covered" },
      }}
    >
      <Segmented label="Evidence domain" value={domain} onChange={setDomain} options={EVIDENCE_DOMAINS.map((d) => ({ id: d.id, label: `${d.label} (${counts.find((c) => c.domain === d.id)?.items ?? 0})` }))} />
      {current ? (
        <p className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <DataStateBadge state={current.state} reason={current.reason} />
          <span className="min-w-0">{current.reason}</span>
        </p>
      ) : null}
      {domain === "dependencies" && summary.dependencies.excluded_unconfirmed > 0 ? (
        <p className="text-xs text-text-muted">{summary.dependencies.excluded_unconfirmed} unconfirmed link(s) are excluded and not counted.</p>
      ) : null}
      {items.length === 0 ? (
        <p className="text-sm text-text-muted">No {current?.label.toLowerCase() ?? "evidence"} rows at this cutoff. This is a gap, not a zero.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((it) => (
            <li key={it.key} className="min-w-0 border-l-2 border-border pl-3 text-sm">
              <p className="font-medium">{it.title}</p>
              {it.detail ? <p className="text-xs text-text-muted">{it.detail}</p> : null}
              {it.passage && it.passage.trim() !== "" ? <blockquote className="mt-1 max-w-prose border-l border-border-soft pl-2 text-xs whitespace-pre-line text-text">{it.passage.length > 600 ? `${it.passage.slice(0, 600)}…` : it.passage}</blockquote> : <p className="mt-1 text-xs text-text-muted">No passage: {it.passageReason ?? "no reason is stored for this row."}</p>}
              <p className="num mt-1 flex flex-wrap gap-x-3 text-xs text-text-muted">
                <span>Known at {day(it.knownAt)}</span>
                {it.source ? <SourceLink url={it.sourceUrl} label={it.source} /> : null}
                {it.coverageReason ? <span>Coverage: {it.coverageReason}</span> : null}
              </p>
            </li>
          ))}
        </ul>
      )}
    </HoldingPanel>
  );
}
