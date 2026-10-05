/**
 * View models for the "Business network and innovation" panels, from
 * `/holdings/{ticker}/business-network` and `/holdings/{ticker}/patent-trends` (both untyped in
 * OpenAPI). Suggested and disputed edges are never merged with confirmed ones.
 */
import { toNumber } from "@/lib/reference/peers";

export type EdgeStatus = "confirmed" | "suggested" | "disputed" | "rejected";

export const STATUS_LABEL: Record<EdgeStatus, string> = {
  confirmed: "Confirmed",
  suggested: "Suggested",
  disputed: "Disputed",
  rejected: "Rejected",
};

const STATUS_ORDER: EdgeStatus[] = ["confirmed", "disputed", "suggested", "rejected"];

export type NetworkEdge = {
  id: string;
  type: string;
  counterparty: string;
  status: EdgeStatus;
  direction: string | null;
  validFrom: string | null;
  validTo: string | null;
  tier: string | null;
  source: string | null;
  sourceUrl: string | null;
  passage: string | null;
  reason: string | null;
};

export type NetworkEvent = {
  id: string;
  type: string;
  state: string | null;
  status: EdgeStatus | null;
  date: string | null;
  source: string | null;
  sourceUrl: string | null;
  counterparty: string | null;
};

export type Network = {
  edges: NetworkEdge[];
  events: NetworkEvent[];
  patentFamilies: number;
  limitations: string[];
  knownAsOf: string | null;
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function toStatus(v: unknown): EdgeStatus {
  return v === "confirmed" || v === "disputed" || v === "rejected" ? v : "suggested";
}

export function typeLabel(type: string): string {
  return type.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
}

export function parseNetwork(payload: unknown): Network | null {
  if (!isRecord(payload)) return null;
  const edges: NetworkEdge[] = (Array.isArray(payload.edges) ? payload.edges : []).filter(isRecord).map((e, i) => ({
    id: String(e.id ?? i),
    type: str(e.edge_type) ?? "unknown",
    counterparty: str(e.object_name) ?? str(e.object_key) ?? "Unnamed counterparty",
    status: toStatus(e.status),
    direction: str(e.direction),
    validFrom: str(e.valid_from),
    validTo: str(e.valid_to),
    tier: str(e.evidence_tier),
    source: str(e.source),
    sourceUrl: str(e.source_url),
    passage: str(e.passage),
    reason: str(e.decision_reason),
  }));
  edges.sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status) || a.type.localeCompare(b.type) || a.counterparty.localeCompare(b.counterparty));

  const events: NetworkEvent[] = (Array.isArray(payload.events) ? payload.events : []).filter(isRecord).map((e, i) => ({
    id: String(e.id ?? i),
    type: str(e.event_type) ?? "event",
    state: str(e.event_state),
    status: e.status === undefined || e.status === null ? null : toStatus(e.status),
    date: str(e.effective_at) ?? str(e.announced_at),
    source: str(e.source),
    sourceUrl: str(e.source_url),
    counterparty: str(e.counterparty_key),
  }));

  const families = new Set(
    (Array.isArray(payload.patents) ? payload.patents : []).filter(isRecord).filter((p) => p.attributed !== false).map((p) => String(p.family_key)),
  );
  return {
    edges,
    events,
    patentFamilies: families.size,
    limitations: Array.isArray(payload.limitations) ? payload.limitations.filter((l): l is string => typeof l === "string") : [],
    knownAsOf: str(payload.known_as_of),
  };
}

export function statusCounts(edges: readonly NetworkEdge[]): Record<EdgeStatus, number> {
  const out: Record<EdgeStatus, number> = { confirmed: 0, disputed: 0, suggested: 0, rejected: 0 };
  for (const e of edges) out[e.status] += 1;
  return out;
}

export type PatentCell = {
  cpc: string;
  year: number;
  families: number;
  citations: number;
  granted: number;
  expired: number;
  expiringSoon: number;
  expiryEstimated: boolean;
};

export type PatentMatrix = {
  rows: string[];
  years: number[];
  cells: Map<string, PatentCell>;
  maxFamilies: number;
  maxCitations: number;
};

export const cellKey = (cpc: string, year: number) => `${cpc}|${year}`;

export function parsePatentTrends(payload: unknown, maxRows = 8, maxYears = 12): PatentMatrix | null {
  if (!Array.isArray(payload)) return null;
  const raw: PatentCell[] = [];
  for (const r of payload) {
    if (!isRecord(r)) continue;
    const cpc = str(r.cpc);
    const year = toNumber(r.year);
    if (!cpc || year === null) continue;
    const counts = isRecord(r.legal_status_counts) ? r.legal_status_counts : {};
    raw.push({
      cpc,
      year,
      families: toNumber(r.families) ?? 0,
      citations: toNumber(r.forward_citations) ?? 0,
      granted: (toNumber(counts.granted) ?? 0) + (toNumber(counts.active) ?? 0),
      expired: toNumber(r.expired) ?? 0,
      expiringSoon: toNumber(r.expiring_within_5y) ?? 0,
      expiryEstimated: r.expiry_is_estimated === true,
    });
  }
  if (raw.length === 0) return { rows: [], years: [], cells: new Map(), maxFamilies: 0, maxCitations: 0 };
  const totals = new Map<string, number>();
  for (const c of raw) totals.set(c.cpc, (totals.get(c.cpc) ?? 0) + c.families);
  const rows = [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, maxRows).map(([c]) => c);
  const allYears = [...new Set(raw.map((c) => c.year))].sort((a, b) => a - b);
  const years = allYears.slice(-maxYears);
  const cells = new Map<string, PatentCell>();
  for (const c of raw) if (rows.includes(c.cpc) && years.includes(c.year)) cells.set(cellKey(c.cpc, c.year), c);
  return {
    rows,
    years,
    cells,
    maxFamilies: Math.max(1, ...[...cells.values()].map((c) => c.families)),
    maxCitations: Math.max(0, ...[...cells.values()].map((c) => c.citations)),
  };
}

/** Square side as a share of the cell: area is proportional to families, never below a visible minimum. */
export function cellSize(families: number, max: number): number {
  if (families <= 0 || max <= 0) return 0;
  return Math.max(0.28, Math.sqrt(families / max));
}

/** Citation shade from 0 (none or lowest) to 3, relative to the busiest cell. */
export function citationStep(citations: number, max: number): 0 | 1 | 2 | 3 {
  if (max <= 0 || citations <= 0) return 0;
  const r = citations / max;
  return r > 0.66 ? 3 : r > 0.33 ? 2 : 1;
}
