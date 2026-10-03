import {
  FORWARD_KINDS,
  type ActualVsConsensus,
  type ConsensusSeries,
  type FactRow,
  type ForwardKind,
  type ForwardObs,
  type ForwardPayload,
  type IrRelease,
  type IrSource,
  type ParsedFacts,
  type PeerEntry,
  type PeerMetricRow,
  type PeerMetricsPayload,
  type PeerStatus,
  type Reconciliation,
} from "./types";

/*
 * Runtime guards for the untyped financial responses. The backend serialises `Decimal` as a
 * JSON number (or a numeric string); anything that is neither is treated as missing, never as
 * zero. A response whose outer shape is wrong is rejected (null), so a view shows
 * "unavailable" instead of drawing garbage; a single malformed row is dropped and counted.
 */

export type Rec = Record<string, unknown>;

export function isRec(v: unknown): v is Rec {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function int(v: unknown): number | null {
  const n = num(v);
  return n !== null && Number.isInteger(n) ? n : null;
}

const RECONCILIATION: readonly Reconciliation[] = ["reconciled", "failed", "unchecked"];

function parseMembers(dimensions: unknown): { members: { axis: string; member: string }[]; typed: boolean } {
  if (!isRec(dimensions)) return { members: [], typed: false };
  const members: { axis: string; member: string }[] = [];
  if (Array.isArray(dimensions.members)) {
    for (const m of dimensions.members) {
      if (Array.isArray(m) && typeof m[0] === "string" && typeof m[1] === "string") members.push({ axis: m[0], member: m[1] });
      else if (isRec(m) && typeof m.axis === "string" && typeof m.member === "string") members.push({ axis: m.axis, member: m.member });
    }
  }
  const typed = Array.isArray(dimensions.typed) ? dimensions.typed.length > 0 : isRec(dimensions.typed) ? Object.keys(dimensions.typed).length > 0 : !!dimensions.typed;
  return { members, typed };
}

function checkNote(validation: unknown): string | null {
  if (!isRec(validation)) return null;
  const failed = validation.failed_checks;
  if (Array.isArray(failed) && failed.length > 0) return `Failed: ${failed.filter((x) => typeof x === "string").join(", ")}`;
  return str(validation.reason);
}

export function parseFactRow(raw: unknown): FactRow | null {
  if (!isRec(raw)) return null;
  const id = int(raw.id);
  const concept = str(raw.concept);
  const statement = str(raw.statement);
  const periodEnd = str(raw.period_end);
  const knownAt = str(raw.known_at);
  const reconciliation = raw.reconciliation;
  if (id === null || !concept || !statement || !periodEnd || !knownAt) return null;
  if (typeof reconciliation !== "string" || !(RECONCILIATION as readonly string[]).includes(reconciliation)) return null;
  const dims = parseMembers(raw.dimensions);
  return {
    id,
    concept,
    label: str(raw.label),
    statement,
    lineItem: str(raw.line_item),
    periodStart: str(raw.period_start),
    periodEnd,
    fiscalPeriod: str(raw.fiscal_period),
    basis: str(raw.basis),
    value: num(raw.value),
    unit: str(raw.unit),
    currency: str(raw.currency),
    dimensional: dims.members.length > 0 || dims.typed,
    members: dims.members,
    source: str(raw.source),
    sourceUrl: str(raw.source_url),
    publishedAt: str(raw.published_at),
    knownAt,
    revision: int(raw.revision),
    amendsFactId: int(raw.amends_fact_id),
    amendsAccession: str(raw.amends_accession),
    isAmendment: raw.is_amendment === true,
    accession: str(raw.accession),
    form: str(raw.form),
    reconciliation: reconciliation as Reconciliation,
    checkNote: checkNote(raw.validation),
    textBlock: typeof raw.text_block === "string" && raw.text_block !== "",
  };
}

/** `GET /holdings/{ticker}/financial-history`: a JSON array of fact rows. */
export function parseFacts(body: unknown): ParsedFacts | null {
  if (!Array.isArray(body)) return null;
  const rows: FactRow[] = [];
  let dropped = 0;
  for (const raw of body) {
    const row = parseFactRow(raw);
    if (row) rows.push(row);
    else dropped += 1;
  }
  if (body.length > 0 && rows.length === 0) return null;
  return { rows, dropped };
}

/** Concatenate parsed responses; null when any is null. */
export function mergeFacts(parts: readonly (ParsedFacts | null)[]): ParsedFacts | null {
  const rows: FactRow[] = [];
  let dropped = 0;
  for (const p of parts) {
    if (!p) return null;
    rows.push(...p.rows);
    dropped += p.dropped;
  }
  return { rows, dropped };
}

// ---------------------------------------------------------------------------------------
// Forward data
// ---------------------------------------------------------------------------------------

export function parseForwardObs(raw: unknown): ForwardObs | null {
  if (!isRec(raw)) return null;
  const id = int(raw.id);
  const kind = raw.kind;
  const metric = str(raw.metric);
  const fiscalPeriod = str(raw.fiscal_period);
  const knownAt = str(raw.known_at);
  if (id === null || typeof kind !== "string" || !(FORWARD_KINDS as readonly string[]).includes(kind)) return null;
  if (!metric || !fiscalPeriod || !knownAt) return null;
  return {
    id,
    kind: kind as ForwardKind,
    provider: str(raw.provider),
    metric,
    basis: str(raw.basis),
    fiscalPeriod,
    periodEnd: str(raw.period_end),
    currency: str(raw.currency),
    value: num(raw.value),
    low: num(raw.low),
    high: num(raw.high),
    analystCount: int(raw.analyst_count),
    vintage: str(raw.vintage),
    publishedAt: str(raw.published_at),
    knownAt,
    sourceUrl: str(raw.source_url),
  };
}

/** `GET /holdings/{ticker}/forward-data/consensus`. */
export function parseForwardPayload(body: unknown): ForwardPayload | null {
  if (!isRec(body) || !Array.isArray(body.observations)) return null;
  const observations: ForwardObs[] = [];
  let dropped = 0;
  for (const raw of body.observations) {
    const o = parseForwardObs(raw);
    if (o) observations.push(o);
    else dropped += 1;
  }
  if (body.observations.length > 0 && observations.length === 0) return null;
  const series: ConsensusSeries[] = [];
  if (Array.isArray(body.consensus_series)) {
    for (const raw of body.consensus_series) {
      if (!isRec(raw) || !Array.isArray(raw.vintages)) continue;
      const metric = str(raw.metric);
      const fiscalPeriod = str(raw.fiscal_period);
      if (!metric || !fiscalPeriod) continue;
      const vintages = raw.vintages.map(parseForwardObs).filter((o): o is ForwardObs => o !== null);
      series.push({
        metric,
        fiscalPeriod,
        provider: str(raw.provider),
        currency: str(raw.currency),
        basis: str(raw.basis),
        vintages,
        revisionSincePrevious: num(raw.revision_since_previous),
      });
    }
  }
  return { observations, series, knownAsOf: str(body.known_as_of), dropped };
}

/** `GET /holdings/{ticker}/actual-vs-consensus`. */
export function parseActualVsConsensus(body: unknown): ActualVsConsensus | null {
  if (!isRec(body)) return null;
  const state = str(body.state);
  if (!state) return null;
  let actual: ActualVsConsensus["actual"] = null;
  if (isRec(body.actual)) {
    const value = num(body.actual.value);
    if (value !== null) {
      actual = {
        value,
        unit: str(body.actual.unit),
        currency: str(body.actual.currency),
        knownAt: str(body.actual.known_at),
        accession: str(body.actual.accession),
        reconciled: body.actual.reconciled === true,
      };
    }
  }
  let consensus: ActualVsConsensus["consensus"] = null;
  if (isRec(body.consensus)) {
    const c = body.consensus;
    consensus = {
      provider: str(c.provider),
      value: num(c.value),
      low: num(c.low),
      high: num(c.high),
      analystCount: int(c.analyst_count),
      currency: str(c.currency),
      basis: str(c.basis),
      vintage: str(c.vintage),
      knownAt: str(c.known_at),
    };
  }
  return {
    state,
    reason: str(body.reason),
    metric: str(body.metric),
    fiscalPeriod: str(body.fiscal_period),
    actual,
    consensus,
    surprise: num(body.surprise),
    surprisePct: num(body.surprise_percent),
  };
}

export function parseIrReleases(body: unknown): IrRelease[] | null {
  if (!isRec(body) || !Array.isArray(body.releases)) return null;
  const out: IrRelease[] = [];
  for (const raw of body.releases) {
    if (!isRec(raw)) continue;
    const id = int(raw.id);
    const releaseType = str(raw.release_type);
    const sourceUrl = str(raw.source_url);
    if (id === null || !releaseType || !sourceUrl) continue;
    out.push({
      id,
      releaseType,
      sourceUrl,
      publishedAt: str(raw.published_at),
      knownAt: str(raw.known_at),
      analystCount: int(raw.analyst_count),
      basis: str(raw.basis),
      status: str(raw.status),
    });
  }
  return out;
}

export function parseIrSources(body: unknown): IrSource[] | null {
  if (!isRec(body) || !Array.isArray(body.sources)) return null;
  const out: IrSource[] = [];
  for (const raw of body.sources) {
    if (!isRec(raw)) continue;
    const id = int(raw.id);
    const url = str(raw.url);
    const releaseType = str(raw.release_type);
    if (id === null || !url || !releaseType) continue;
    out.push({
      id,
      url,
      releaseType,
      basis: str(raw.basis),
      label: str(raw.label),
      active: raw.active === true,
      lastCheckedAt: str(raw.last_checked_at),
      lastStatus: str(raw.last_status),
      lastError: str(raw.last_error),
    });
  }
  return out;
}

// ---------------------------------------------------------------------------------------
// Peers
// ---------------------------------------------------------------------------------------

const PEER_STATUSES: readonly PeerStatus[] = ["suggested", "confirmed", "rejected"];

/** `GET /holdings/{ticker}/peer-set`. */
export function parsePeerSet(body: unknown): PeerEntry[] | null {
  if (!isRec(body) || !Array.isArray(body.peers)) return null;
  const out: PeerEntry[] = [];
  for (const raw of body.peers) {
    if (!isRec(raw)) continue;
    const id = int(raw.id);
    const peerTicker = str(raw.peer_ticker);
    const status = raw.status;
    if (id === null || !peerTicker || typeof status !== "string" || !(PEER_STATUSES as readonly string[]).includes(status)) continue;
    out.push({
      id,
      peerTicker,
      basis: str(raw.basis),
      status: status as PeerStatus,
      evidenceId: int(raw.evidence_id),
      source: str(raw.source),
      validFrom: str(raw.valid_from),
      validTo: str(raw.valid_to),
      knownAt: str(raw.known_at),
    });
  }
  return out;
}

function stringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function stringMap(v: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (isRec(v)) for (const [k, val] of Object.entries(v)) if (typeof val === "string") out[k] = val;
  return out;
}

/** `GET /holdings/{ticker}/peer-metrics`. */
export function parsePeerMetrics(body: unknown): PeerMetricsPayload | null {
  if (!isRec(body) || !isRec(body.covered_peer_metrics)) return null;
  const metrics: PeerMetricRow[] = [];
  for (const [id, raw] of Object.entries(body.covered_peer_metrics)) {
    if (!isRec(raw)) continue;
    metrics.push({
      id,
      mean: num(raw.mean),
      coveredPeers: int(raw.covered_peers) ?? 0,
      included: stringList(raw.included),
      excluded: stringList(raw.excluded),
      excludedReasons: stringMap(raw.excluded_reasons),
      holdingValue: num(raw.holding_value),
      holdingReason: str(raw.holding_reason),
      periodEnd: str(raw.period_end),
      periodStart: str(raw.period_start),
      basis: str(raw.basis),
    });
  }
  const confirmedPeers = (Array.isArray(body.confirmed_peers) ? body.confirmed_peers : [])
    .filter(isRec)
    .map((p) => ({ peerTicker: str(p.peer_ticker) ?? "", basis: str(p.basis), source: str(p.source) }))
    .filter((p) => p.peerTicker !== "");
  const excludedPeers = (Array.isArray(body.excluded_peers) ? body.excluded_peers : [])
    .filter(isRec)
    .map((p) => ({ peer: str(p.peer) ?? "", reason: str(p.reason) ?? "No reason recorded" }))
    .filter((p) => p.peer !== "");
  let holdingPeriod: PeerMetricsPayload["holdingPeriod"] = null;
  if (isRec(body.holding_period)) {
    const h = body.holding_period;
    holdingPeriod = {
      periodStart: str(h.period_start),
      periodEnd: str(h.period_end),
      basis: str(h.basis),
      unit: str(h.unit),
      reconciliation: str(h.reconciliation),
      unreconciledInputs: stringList(h.unreconciled_inputs),
    };
  }
  return {
    confirmedPeers,
    metrics,
    excludedPeers,
    holdingPeriod,
    reconciledOnly: body.reconciled_only === true,
    formulas: stringMap(body.formula_definitions),
    universe: str(body.universe),
    knownAsOf: str(body.known_as_of),
  };
}
