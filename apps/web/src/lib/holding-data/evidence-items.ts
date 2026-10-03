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
const pct = (v: number | string | null | undefined) => (v === null || v === undefined || v === "" ? null : `${v}%`);

export function evidenceItems(s: EvidenceSummary, domain: EvidenceDomain): EvidenceItem[] {
  switch (domain) {
    case "filed-notes":
      return s.filed_notes.notes.map((n) => ({
        key: `note-${n.fact_id}-${n.revision}`,
        title: n.label ?? n.concept,
        detail: joined(`period ${n.period_end}`, n.fiscal_period, n.form, n.accession, `revision ${n.revision}`),
        passage: n.passage,
        passageReason: n.passage_reason ?? (n.passage === null ? "No passage stored for this fact." : null),
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
          passageReason: k.passage_reason ?? (k.source_passage === null ? "No purpose text stored (13G carries none)." : null),
          knownAt: k.known_at,
          coverageReason: k.coverage_reason,
          sourceUrl: k.stake.source_url,
          source: k.stake.form,
        })),
        ...(s.positions.institutional.ownership?.top_holders ?? []).map((h) => ({
          key: `13f-${h.cik}-${h.accession}`,
          title: `13F · ${h.manager_name ?? h.cik}`,
          detail: joined(`${h.shares} shares`, h.pct_of_shares_outstanding != null ? `${h.pct_of_shares_outstanding}% of outstanding` : null, h.accession),
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
        passageReason: i.passage_reason ?? (i.passage === null ? "No footnote stored; the class rests on the transaction code." : null),
        knownAt: i.filed,
        coverageReason: s.insider_activity.coverage.reason,
        sourceUrl: i.source_url,
        source: "form4",
      }));
    case "dependencies":
      return s.dependencies.dependencies.map((d) => ({
        key: `dep-${d.kind}-${d.edge_id}`,
        title: `${d.relationship} · ${d.counterparty_name ?? (d.anonymous_counterparty ? "anonymous counterparty" : (d.counterparty_key ?? "unknown"))}`,
        detail: joined(d.direction, d.reported_share != null ? `${d.reported_share} ${d.share_basis ?? ""}`.trim() : null, d.fiscal_period, d.evidence_tier),
        passage: d.passage,
        passageReason: d.passage_reason ?? (d.passage === null ? "No passage stored for this link." : null),
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
          passageReason: e.passage_reason ?? (e.passage === null ? "No passage stored for this event." : null),
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
          passageReason: v.passage_reason ?? (v.passage === null ? "No passage stored for this vintage." : null),
          knownAt: v.known_at,
          coverageReason: null,
          sourceUrl: v.source_url,
          source: v.provider,
        })),
      );
  }
}
