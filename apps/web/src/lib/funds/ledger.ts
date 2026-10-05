import type { components } from "@/lib/api/schema";

export type LedgerRow = components["schemas"]["LedgerRow"];
export type Tier = LedgerRow["tier"];

/** Best to worst source of constituents; the page ranks and labels by this order. */
export const TIERS: readonly { id: Tier; label: string; meaning: string }[] = [
  { id: "official_holdings", label: "Official holdings", meaning: "The fund or index publishes its own holdings file." },
  { id: "issuer_downloadable", label: "Issuer download", meaning: "The issuer offers a downloadable holdings list." },
  { id: "regulatory_filing", label: "Regulatory filing", meaning: "Holdings come from a periodic regulatory filing, so they can be months old." },
  { id: "proxy_fund", label: "Proxy fund", meaning: "Holdings are borrowed from a similar fund, so they are an approximation." },
  { id: "unavailable", label: "No holdings source", meaning: "No constituents are available; look-through for this fund is unknown." },
];

const RANK = new Map(TIERS.map((t, i) => [t.id, i]));

export const tierLabel = (t: Tier) => TIERS.find((x) => x.id === t)?.label ?? t;

export type TierCount = { id: Tier; label: string; count: number };

/** Entries per tier in rank order, tiers with no entries kept so a missing tier is visible. */
export function tierCounts(rows: readonly LedgerRow[]): TierCount[] {
  return TIERS.map((t) => ({ id: t.id, label: t.label, count: rows.filter((r) => r.tier === t.id).length }));
}

/** Best sources first, then by name, so the weakest entries sit together at the end. */
export function rankRows(rows: readonly LedgerRow[]): LedgerRow[] {
  return [...rows].sort((a, b) => (RANK.get(a.tier) ?? 99) - (RANK.get(b.tier) ?? 99) || a.label.localeCompare(b.label));
}

/** A boolean flag in words; null stays "Unknown" and is never read as no. */
export function flagText(v: boolean | null): "Yes" | "No" | "Unknown" {
  return v === null ? "Unknown" : v ? "Yes" : "No";
}
