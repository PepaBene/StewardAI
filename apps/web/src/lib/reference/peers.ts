/**
 * The peer comparison card's view model. `/holdings/{ticker}/peer-metrics` is untyped in the
 * OpenAPI schema, so the payload is read defensively: anything missing becomes null (shown as
 * "Not computable"), never zero.
 */

export type PeerMetricKind = "percent" | "multiple";

export const PEER_METRICS: readonly { id: string; label: string; kind: PeerMetricKind }[] = [
  { id: "revenue_growth", label: "Revenue growth", kind: "percent" },
  { id: "gross_margin", label: "Gross margin", kind: "percent" },
  { id: "operating_margin", label: "Operating margin", kind: "percent" },
  { id: "roic", label: "Return on invested capital", kind: "percent" },
  { id: "free_cash_flow_conversion", label: "Free cash flow conversion", kind: "percent" },
  { id: "net_debt_to_equity", label: "Net debt to equity", kind: "multiple" },
];

export type PeerRow = {
  id: string;
  label: string;
  kind: PeerMetricKind;
  holding: number | null;
  /** Why the holding has no value, when it has none. */
  holdingReason: string | null;
  peerMean: number | null;
  /** Peers inside the mean. */
  covered: number;
  /** Peers left out, each with the reason. */
  excluded: { peer: string; reason: string }[];
  formula: string | null;
};

export type PeerComparison = {
  rows: PeerRow[];
  confirmedPeers: number;
  /** Peers that have no comparable period at all. */
  excludedPeers: { peer: string; reason: string }[];
  periodEnd: string | null;
  basis: string | null;
  universe: string;
  knownAsOf: string | null;
};

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

export function parsePeerMetrics(payload: unknown): PeerComparison | null {
  if (!isRecord(payload)) return null;
  const metrics = isRecord(payload.covered_peer_metrics) ? payload.covered_peer_metrics : {};
  const formulas = isRecord(payload.formula_definitions) ? payload.formula_definitions : {};
  const peers = Array.isArray(payload.confirmed_peers) ? payload.confirmed_peers : [];
  const period = isRecord(payload.holding_period) ? payload.holding_period : null;

  const rows: PeerRow[] = PEER_METRICS.map((m) => {
    const entry = isRecord(metrics[m.id]) ? (metrics[m.id] as Record<string, unknown>) : {};
    const reasons = isRecord(entry.excluded_reasons) ? entry.excluded_reasons : {};
    const excluded = Array.isArray(entry.excluded) ? entry.excluded.filter((p): p is string => typeof p === "string") : [];
    return {
      id: m.id,
      label: m.label,
      kind: m.kind,
      holding: toNumber(entry.holding_value),
      holdingReason: str(entry.holding_reason),
      peerMean: toNumber(entry.mean),
      covered: toNumber(entry.covered_peers) ?? 0,
      excluded: excluded.map((peer) => ({ peer, reason: str(reasons[peer]) ?? "No value" })),
      formula: str(formulas[m.id]),
    };
  });

  const excludedPeers = Array.isArray(payload.excluded_peers)
    ? payload.excluded_peers.filter(isRecord).map((e) => ({ peer: str(e.peer) ?? "Unknown", reason: str(e.reason) ?? "No reason recorded" }))
    : [];

  return {
    rows,
    confirmedPeers: peers.length,
    excludedPeers,
    periodEnd: period ? str(period.period_end) : null,
    basis: period ? str(period.basis) : null,
    universe: str(payload.universe) ?? "Owner-confirmed peers with compatible filed facts.",
    knownAsOf: str(payload.known_as_of),
  };
}

/** Holding minus peer mean, null unless both exist. */
export function peerDelta(row: PeerRow): number | null {
  return row.holding !== null && row.peerMean !== null ? row.holding - row.peerMean : null;
}

/** Position (0-100) of a value on a track spanning every value in the row plus zero, for the range bar. */
export function trackPositions(row: PeerRow): { holding: number | null; mean: number | null; zero: number } | null {
  const vals = [row.holding, row.peerMean].filter((v): v is number => v !== null);
  if (vals.length === 0) return null;
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;
  return {
    holding: row.holding === null ? null : pos(row.holding),
    mean: row.peerMean === null ? null : pos(row.peerMean),
    zero: pos(0),
  };
}

export function formatPeerValue(value: number | null, kind: PeerMetricKind): string {
  if (value === null) return "Not computable";
  if (kind === "multiple") return `${value.toFixed(2)}×`;
  return `${(value * 100).toFixed(1)}%`;
}
