import type { NetworkSummary, PatentSummary } from "./types";

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function records(v: unknown): Record<string, unknown>[] | null {
  return Array.isArray(v) && v.every(isRecord) ? (v as Record<string, unknown>[]) : null;
}

function tally(rows: readonly Record<string, unknown>[], key: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) {
    const raw = r[key];
    const k = typeof raw === "string" && raw ? raw : "unknown";
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

/** `HoldingDataContext.business_network`; null (shown as unavailable) for any other shape. */
export function parseNetworkSummary(body: unknown): NetworkSummary | null {
  if (!isRecord(body)) return null;
  const edges = records(body.edges);
  const events = records(body.events);
  const disclosures = records(body.disclosures);
  if (!edges || !events || !disclosures) return null;
  const byStatus = tally(edges, "status");
  const confirmed = byStatus.confirmed ?? 0;
  const limitations = Array.isArray(body.limitations) ? body.limitations.filter((x): x is string => typeof x === "string") : [];
  return { edges: edges.length, byStatus, confirmed, candidates: edges.length - confirmed, events: events.length, disclosures: disclosures.length, limitations };
}

/** `HoldingDataContext.patents` (`{families, trend}`); null for any other shape. */
export function parsePatentSummary(body: unknown): PatentSummary | null {
  if (!isRecord(body)) return null;
  const families = records(body.families);
  if (!families) return null;
  const known = families.map((f) => f.known_at).filter((x): x is string => typeof x === "string").sort();
  return {
    families: families.length,
    attributed: families.filter((f) => f.attributed === true).length,
    byLegalStatus: tally(families, "legal_status"),
    latestKnownAt: known.length > 0 ? known[known.length - 1] : null,
    trendRows: Array.isArray(body.trend) ? body.trend.length : null,
  };
}
