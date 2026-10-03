import { formatPct } from "@/lib/charts/format";
import { shareBasisLabel } from "@/lib/network/logic";

import { toNumber } from "./logic";
import type { EvidenceDomain, EvidenceSummary } from "./types";

/** One derived figure's evidence, flattened across domains: the passage, when it was known, and why a piece is missing. */
export type EvidenceItem = {
  key: string;
  title: string;
  detail: string | null;
  /** The stored source passage; null means none is stored (the UI then shows `passageReason`). */
  passage: string | null;
  passageReason: string | null;
  /** ISO date or instant the record became known to the app (or public, for filings). */
  knownAt: string | null;
  coverageReason: string | null;
  sourceUrl: string | null;
  source: string | null;
};

export const EVIDENCE_DOMAINS: readonly { id: EvidenceDomain; label: string }[] = [
  { id: "filed-notes", label: "Filed notes" },
  { id: "positions", label: "Positions" },
  { id: "insider-activity", label: "Insider" },
  { id: "dependencies", label: "Dependencies" },
  { id: "deals", label: "Deals" },
  { id: "consensus", label: "Consensus" },
];

const joined = (...parts: (string | null | undefined | false)[]) => parts.filter((p): p is string => typeof p === "string" && p !== "").join(" · ") || null;
/** 13D/G `stake_percent` is already in percent units (backend multiplies by 100). */
const pct = (v: number | string | null | undefined) => (v === null || v === undefined || v === "" ? null : `${v}%`);

/** A fraction (0..1, e.g. 13F `pct_of_shares_outstanding`, dependency `reported_share`) as a percent; null when missing. */
export function fractionPct(v: unknown, digits = 2): string | null {
  const n = toNumber(v);
  return n === null ? null : formatPct(n, digits);
}

/** The reason to show when no passage is stored: an empty or blank passage counts as none. */
function noPassage(passage: string | null | undefined, reason: string | null | undefined, fallback: string): string | null {
  if (typeof passage === "string" && passage.trim() !== "") return reason ?? null;
  return reason && reason.trim() !== "" ? reason : fallback;
}

export function evidenceItems(s: EvidenceSummary, domain: EvidenceDomain): EvidenceItem[] {
  switch (domain) {
    case "filed-notes":
      return s.filed_notes.notes.map((n) => ({
        key: `note-${n.fact_id}-${n.revision}`,
        title: n.label ?? n.concept,
        detail: joined(`period ${n.period_end}`, n.fiscal_period, n.form, n.accession, `revision ${n.revision}`),
        passage: n.passage,
        passageReason: noPassage(n.passage, n.passage_reason, "No passage stored for this fact."),
        knownAt: n.known_at,
        coverageReason: null,
        sourceUrl: n.source_url,
        source: n.source,
      }));
    case "positions":
      return [
        ...s.positions.control.stakes.map((k) => ({
          key: `stake-${k.stake.accession}`,
          title: `${k.stake.schedule} stake${k.superseded ? " (superseded)" : ""} · ${k.stake.issuer_name ?? "issuer"}`,
          detail: joined(k.stake.holders.map((h) => h.person_name).join(", "), pct(k.stake.stake_percent), k.stake.stake_basis, k.stake.accession),
          passage: k.source_passage,
          passageReason: noPassage(k.source_passage, k.passage_reason, "No purpose text stored (13G carries none)."),
          knownAt: k.known_at,
          coverageReason: k.coverage_reason,
          sourceUrl: k.stake.source_url,
          source: k.stake.form,
        })),
        ...(s.positions.institutional.ownership?.top_holders ?? []).map((h) => ({
          key: `13f-${h.cik}-${h.accession}`,
          title: `13F · ${h.manager_name ?? h.cik}`,
          detail: joined(`${h.shares} shares`, fractionPct(h.pct_of_shares_outstanding) !== null ? `${fractionPct(h.pct_of_shares_outstanding)} of outstanding` : null, h.accession),
          passage: null,
          passageReason: "A 13F table row has no narrative passage; the filing is the evidence.",
          knownAt: s.positions.institutional.ownership?.periods[0]?.latest_filed ?? null,
          coverageReason: s.positions.institutional.pit_note ?? s.positions.institutional.coverage.reason,
          sourceUrl: null,
          source: s.positions.institutional.ownership?.source ?? "13F",
        })),
      ];
    case "insider-activity":
      return s.insider_activity.net_discretionary_derived_from.map((i) => ({
        key: `ins-${i.accession}`,
        title: `Form 4 ${i.accession}`,
        detail: joined(i.economic_class, i.shares != null ? `${i.shares} shares` : null),
        passage: i.passage,
        passageReason: noPassage(i.passage, i.passage_reason, "No footnote stored; the class rests on the transaction code."),
        knownAt: i.filed,
        coverageReason: s.insider_activity.coverage.reason,
        sourceUrl: i.source_url,
        source: "form4",
      }));
    case "dependencies":
      return s.dependencies.dependencies.map((d) => ({
        key: `dep-${d.kind}-${d.edge_id}`,
        title: `${d.relationship} · ${d.counterparty_name ?? (d.anonymous_counterparty ? "anonymous counterparty" : (d.counterparty_key ?? "unknown"))}`,
        detail: joined(d.direction, fractionPct(d.reported_share, 1) !== null ? `${fractionPct(d.reported_share, 1)} ${d.share_basis ? shareBasisLabel(d.share_basis) : ""}`.trim() : null, d.fiscal_period, d.evidence_tier),
        passage: d.passage,
        passageReason: noPassage(d.passage, d.passage_reason, "No passage stored for this link."),
        knownAt: d.known_at,
        coverageReason: null,
        sourceUrl: d.source_url,
        source: d.source,
      }));
    case "deals":
      return s.deals.deals.flatMap((deal) =>
        deal.events.map((e) => ({
          key: `deal-${e.event_id}`,
          title: `${deal.deal_key} · ${e.event_type} (${e.event_state})`,
          detail: joined(`current: ${deal.current_state ?? "unknown"}`, deal.state_basis, e.status),
          passage: e.passage,
          passageReason: noPassage(e.passage, e.passage_reason, "No passage stored for this event."),
          knownAt: e.known_at,
          coverageReason: null,
          sourceUrl: e.source_url,
          source: e.source,
        })),
      );
    case "consensus":
      return s.consensus.series.flatMap((series) =>
        series.vintages.map((v) => ({
          key: `cons-${v.observation_id}`,
          title: `${series.metric} ${series.fiscal_period} · ${v.vintage ?? "vintage n/a"}`,
          detail: joined(v.value != null ? `${v.value} ${v.currency ?? ""}`.trim() : null, v.analyst_count != null ? `${v.analyst_count} analysts` : null, v.provider),
          passage: v.passage,
          passageReason: noPassage(v.passage, v.passage_reason, "No passage stored for this vintage."),
          knownAt: v.known_at,
          coverageReason: null,
          sourceUrl: v.source_url,
          source: v.provider,
        })),
      );
  }
}
