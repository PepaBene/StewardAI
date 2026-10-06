/** Facts found by web research (GET /holdings/{ticker}/web-facts), parsed from the untyped response. */

export const SLOT_LABELS: Record<string, string> = {
  next_report_date: "Next report date",
  shares_outstanding: "Shares outstanding",
  company_description: "Description",
};

export const TIER_LABELS: Record<string, string> = {
  T1: "Tier 1: the company's own site, a regulator or an exchange",
  T2: "Tier 2: established press or wire service",
  T3: "Tier 3: reference site (names and descriptions only)",
  rejected: "Not a reliable source",
};

export type WebFact = {
  id: number;
  slot: string;
  value: string;
  tier: string;
  quote: string;
  sourceUrl: string;
  host: string;
  retrievedAt: string;
  status: "approved" | "needs_review" | "rejected";
  decidedBy: string;
  confidence: number | null;
  reasons: string | null;
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

export function parseWebFacts(raw: unknown): WebFact[] {
  if (!isRecord(raw) || !Array.isArray(raw.facts)) return [];
  const out: WebFact[] = [];
  for (const item of raw.facts) {
    if (!isRecord(item)) continue;
    const status = item.status;
    if (status !== "approved" && status !== "needs_review" && status !== "rejected") continue;
    if (typeof item.id !== "number" || typeof item.slot !== "string") continue;
    out.push({
      id: item.id,
      slot: item.slot,
      value: String(item.value_text ?? ""),
      tier: String(item.tier ?? ""),
      quote: String(item.quote ?? ""),
      sourceUrl: String(item.source_url ?? ""),
      host: String(item.host ?? ""),
      retrievedAt: String(item.retrieved_at ?? ""),
      status,
      decidedBy: String(item.decided_by ?? ""),
      confidence: typeof item.confidence === "number" ? item.confidence : item.confidence ? Number(item.confidence) : null,
      reasons: typeof item.reasons === "string" ? item.reasons : null,
    });
  }
  return out;
}

export function decidedByOwner(fact: WebFact): boolean {
  return fact.decidedBy.startsWith("owner:");
}
