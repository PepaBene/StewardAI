/** What the AI approvers decided (GET /approvals/ai-summary), parsed from the untyped response. */

export type ListingDecision = { ticker: string; status: string; decidedAt: string; reason: string };
export type PeerDecision = { id: number; ticker: string; peer: string; status: string; reason: string; at: string };
export type PageDecision = { ticker: string; at: string; applied: string[] };
export type FactDecision = {
  id: number;
  ticker: string;
  slot: string;
  value: string;
  tier: string;
  status: string;
  confidence: number | null;
  sourceUrl: string;
  retrievedAt: string;
};

export type DisclosureDecision = {
  id: number;
  ticker: string;
  relationship: string;
  counterparty: string;
  share: number | null;
  basis: string;
  period: string;
  at: string;
  reason: string;
};
export type ExposureDecision = {
  ticker: string;
  kind: string;
  name: string;
  share: number | null;
  period: string;
  sourceUrl: string;
  quote: string;
};

export type AiDecisions = {
  days: number;
  listings: ListingDecision[];
  peers: PeerDecision[];
  pages: PageDecision[];
  facts: FactDecision[];
  disclosures: DisclosureDecision[];
  exposures: ExposureDecision[];
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

function rows(raw: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const v = raw[key];
  return Array.isArray(v) ? v.filter(isRecord) : [];
}

/** The `applied` detail of an investor-page decision: a list of strings, or the keys of an object. */
function appliedList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x)));
  if (isRecord(v)) return Object.entries(v).map(([k, val]) => `${k}: ${String(val)}`);
  return [];
}

export function parseAiDecisions(raw: unknown): AiDecisions {
  const empty: AiDecisions = { days: 7, listings: [], peers: [], pages: [], facts: [], disclosures: [], exposures: [] };
  if (!isRecord(raw)) return empty;
  return {
    days: typeof raw.days === "number" ? raw.days : 7,
    listings: rows(raw, "listings")
      .filter((r) => typeof r.ticker === "string")
      .map((r) => ({
        ticker: String(r.ticker),
        status: String(r.status ?? ""),
        decidedAt: String(r.decided_at ?? ""),
        reason: String(r.reason ?? ""),
      })),
    peers: rows(raw, "peers")
      .filter((r) => typeof r.id === "number" && typeof r.ticker === "string")
      .map((r) => ({
        id: Number(r.id),
        ticker: String(r.ticker),
        peer: String(r.peer_ticker ?? ""),
        status: String(r.status ?? ""),
        reason: String(r.reason ?? ""),
        at: String(r.at ?? ""),
      })),
    pages: rows(raw, "investor_pages")
      .filter((r) => typeof r.ticker === "string")
      .map((r) => ({ ticker: String(r.ticker), at: String(r.attempted_at ?? ""), applied: appliedList(r.applied) })),
    facts: rows(raw, "web_facts")
      .filter((r) => typeof r.id === "number" && typeof r.ticker === "string")
      .map((r) => ({
        id: Number(r.id),
        ticker: String(r.ticker),
        slot: String(r.slot ?? ""),
        value: String(r.value_text ?? ""),
        tier: String(r.tier ?? ""),
        status: String(r.status ?? ""),
        confidence: r.confidence === null || r.confidence === undefined ? null : Number(r.confidence),
        sourceUrl: String(r.source_url ?? ""),
        retrievedAt: String(r.retrieved_at ?? ""),
      })),
    disclosures: rows(raw, "disclosures")
      .filter((r) => typeof r.id === "number" && typeof r.ticker === "string")
      .map((r) => ({
        id: Number(r.id),
        ticker: String(r.ticker),
        relationship: String(r.relationship ?? ""),
        counterparty: r.anonymous_counterparty === true ? "not named by the filer" : String(r.counterparty_name ?? "unnamed"),
        share: r.reported_share === null || r.reported_share === undefined ? null : Number(r.reported_share),
        basis: String(r.share_basis ?? ""),
        period: String(r.fiscal_period ?? ""),
        at: String(r.decided_at ?? ""),
        reason: String(r.decision_reason ?? ""),
      })),
    exposures: rows(raw, "exposures")
      .filter((r) => typeof r.ticker === "string" && typeof r.name === "string")
      .map((r) => ({
        ticker: String(r.ticker),
        kind: String(r.kind ?? ""),
        name: String(r.name),
        share: r.share === null || r.share === undefined ? null : Number(r.share),
        period: String(r.period ?? ""),
        sourceUrl: String(r.source_url ?? ""),
        quote: String(r.quote ?? ""),
      })),
  };
}

export function totalDecisions(d: AiDecisions): number {
  return d.listings.length + d.peers.length + d.pages.length + d.facts.length + d.disclosures.length + d.exposures.length;
}
