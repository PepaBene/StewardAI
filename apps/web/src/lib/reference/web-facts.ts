/** Facts found by web research (GET /holdings/{ticker}/web-facts), parsed from the untyped response. */

export const SLOT_LABELS: Record<string, string> = {
  next_report_date: "Next report date",
  shares_outstanding: "Shares outstanding",
  company_description: "Description",
  annual_revenue: "Revenue, latest fiscal year",
  segment_revenue: "Revenue by segment",
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

/** The value as shown: segment revenue is stored as a JSON list and read as "Name amount · Name amount". */
export function displayValue(fact: Pick<WebFact, "slot" | "value">): string {
  if (fact.slot !== "segment_revenue") return fact.value;
  try {
    const lines: unknown = JSON.parse(fact.value);
    if (!Array.isArray(lines)) return fact.value;
    const parts = lines.flatMap((l: unknown) =>
      typeof l === "object" && l !== null && typeof (l as { segment?: unknown }).segment === "string" && typeof (l as { amount?: unknown }).amount === "number"
        ? [`${(l as { segment: string }).segment} ${new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format((l as { amount: number }).amount)}`]
        : [],
    );
    return parts.length > 0 ? parts.join(" · ") : fact.value;
  } catch {
    return fact.value;
  }
}

export function decidedByOwner(fact: WebFact): boolean {
  return fact.decidedBy.startsWith("owner:");
}
