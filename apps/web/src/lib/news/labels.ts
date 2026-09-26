import type { Confidence, EventType } from "@/lib/api/client";

/* Display labels for the news pipeline: event types, severities and source tiers. */

export const EVENT_TYPES = [
  "guidance_change",
  "m_and_a",
  "dividend_cut",
  "earnings_release",
  "management_change",
  "regulatory_legal",
  "capital_allocation",
  "credit_rating",
  "central_bank",
  "macro_surprise",
  "insider_transaction",
  "product_business",
  "sector_competitor",
  "index_change",
  "analyst_rating",
  "delisting",
  "entity_status",
  "other",
] as const satisfies readonly EventType[];

export const EVENT_LABELS: Record<EventType, string> = {
  guidance_change: "Guidance change",
  m_and_a: "M&A",
  dividend_cut: "Dividend cut",
  earnings_release: "Earnings release",
  management_change: "Management change",
  regulatory_legal: "Regulatory or legal",
  capital_allocation: "Capital allocation",
  credit_rating: "Credit rating",
  central_bank: "Central bank",
  macro_surprise: "Macro surprise",
  insider_transaction: "Insider transaction",
  product_business: "Product or business",
  sector_competitor: "Sector or competitor",
  index_change: "Index change",
  analyst_rating: "Analyst rating",
  delisting: "Delisting",
  entity_status: "Legal entity status",
  other: "Other",
};

export function eventLabel(eventType: string | null | undefined): string {
  if (!eventType) return "Not classified";
  return EVENT_LABELS[eventType as EventType] ?? eventType.replaceAll("_", " ");
}

/* Severity uses the muted signal palette (never gain/loss), with a word for each level. */
export const SEVERITY = {
  0: { label: "Ignored", tone: "neutral", description: "Severity 0: not shown in digests." },
  1: { label: "Digest", tone: "neutral", description: "Severity 1: listed in the digest." },
  2: { label: "Highlight", tone: "info", description: "Severity 2: digest highlight, linked to the thesis." },
  3: { label: "Alert", tone: "warning", description: "Severity 3: instant alert when confirmed." },
} as const;

export type SeverityLevel = keyof typeof SEVERITY;

export const TIERS = {
  1: { label: "Tier 1", description: "Primary source: the company, a regulator, an official newswire." },
  2: { label: "Tier 2", description: "Curated outlet: needs a second independent outlet to confirm." },
  3: { label: "Tier 3", description: "Sentiment: blogs and social; never alerts on its own." },
} as const;

export const CONFIDENCES = ["verified", "corroborated", "unconfirmed", "disputed"] as const satisfies readonly Confidence[];

export const STATUS_LABELS: Record<string, string> = {
  pending: "Awaiting classification",
  confirmed: "Confirmed",
  unconfirmed: "Unconfirmed",
  disputed: "Disputed",
  sentiment: "Sentiment only",
};
