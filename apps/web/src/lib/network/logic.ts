import type { DataCoverage } from "@/lib/data-state/provenance";
import type { DataState } from "@/lib/data-state/states";

import type {
  BusinessNetwork,
  ConcentrationPeriod,
  Deal,
  DealStep,
  NetworkEdge,
  NetworkEvent,
  PatentFamily,
  PatentOverlapRow,
  RowStatus,
} from "./types";

/* ------------------------------------------------------------ resource states */

type ParsedState = { kind: "loading" } | { kind: "ok"; data: { ok: boolean; reason?: string } } | { kind: "error"; title: string };

/** "ok" only for a loaded, recognised response: loading, failure and a rejected shape are never an empty result. */
export function resourceStatus(state: ParsedState): "ok" | "loading" | "unavailable" {
  if (state.kind === "loading") return "loading";
  if (state.kind === "ok" && state.data.ok) return "ok";
  return "unavailable";
}

export function resourceReason(state: ParsedState): string | null {
  if (state.kind === "error") return state.title;
  if (state.kind === "ok" && !state.data.ok) return `unrecognised response: ${state.data.reason ?? "unknown shape"}`;
  return null;
}

/* ---------------------------------------------------------------- as-of control */

/** `YYYY-MM-DD` that is a real calendar date. */
export function isIsoDate(value: string | null | undefined): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value;
}

/**
 * The API's `as_of` query value for a chosen day: the end of that day (UTC), so everything
 * the app knew on that day is included. Empty or invalid input means "now" (no param).
 */
export function asOfParam(date: string | null | undefined, now: Date = new Date()): string | undefined {
  if (!isIsoDate(date)) return undefined;
  const instant = `${date}T23:59:59Z`;
  // The API refuses an as_of after the present moment (422), compared in UTC: today (or a later
  // day) has no completed end of day yet, so it means "now" and sends nothing.
  return Date.parse(instant) > now.getTime() ? undefined : instant;
}

/* ------------------------------------------------------------------- edge groups */

export type EdgeGroup = "control" | "ownership" | "transaction" | "commercial" | "technology" | "leadership" | "other";

export const EDGE_GROUPS: Record<EdgeGroup, { label: string; note: string }> = {
  control: {
    label: "Consolidation and control",
    note: "Accounting parent or subsidiary (GLEIF level 2). Consolidation is not beneficial ownership.",
  },
  ownership: { label: "Ownership and investment", note: "Holders, investors and investments. Kept apart from consolidation." },
  transaction: { label: "Acquisitions", note: "Acquirer or acquired in a deal; the deal state is on the timeline." },
  commercial: { label: "Commercial", note: "Customers, suppliers, partners and competitors." },
  technology: { label: "Product and technology", note: "Products and technologies linked to the company." },
  leadership: { label: "Leadership", note: "Executives and their tenure." },
  other: { label: "Other", note: "A relationship type this screen does not classify." },
};

const GROUP_OF: Record<string, EdgeGroup> = {
  parent: "control",
  ultimate_parent: "control",
  subsidiary: "control",
  owner: "ownership",
  investor: "ownership",
  investment: "ownership",
  acquirer: "transaction",
  acquired: "transaction",
  customer: "commercial",
  supplier: "commercial",
  partner: "commercial",
  competitor: "commercial",
  product: "technology",
  technology: "technology",
  executive: "leadership",
};

export function edgeGroup(edgeType: string): EdgeGroup {
  return GROUP_OF[edgeType] ?? "other";
}

export const GROUP_ORDER: readonly EdgeGroup[] = ["control", "ownership", "transaction", "commercial", "technology", "leadership", "other"];

export function humanize(key: string): string {
  const s = key.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export const STATUS_LABEL: Record<RowStatus, string> = {
  confirmed: "Confirmed",
  suggested: "Suggested",
  disputed: "Disputed",
  rejected: "Rejected",
  unrecognised: "Unrecognised status",
};

/** Only a confirmed row is a fact; everything else is a candidate, a dispute or a refusal. */
export function isFact(status: RowStatus): boolean {
  return status === "confirmed";
}

export function directionLabel(direction: string): string {
  switch (direction) {
    case "outgoing":
      return "company → counterparty";
    case "incoming":
      return "counterparty → company";
    case "symmetric":
      return "mutual";
    case "issuer_sells_to":
      return "company sells to counterparty";
    case "issuer_buys_from":
      return "company buys from counterparty";
    case "award_to_issuer":
      return "award to company";
    default:
      return "direction not stated";
  }
}

/* ------------------------------------------------------------------ edge ledger */

export type EdgeFilter = { statuses: ReadonlySet<RowStatus>; groups: ReadonlySet<EdgeGroup> | null };

const STATUS_RANK: Record<RowStatus, number> = { confirmed: 0, disputed: 1, suggested: 2, unrecognised: 3, rejected: 4 };

export function filterEdges(edges: readonly NetworkEdge[], f: EdgeFilter): NetworkEdge[] {
  return edges
    .filter((e) => f.statuses.has(e.status) && (f.groups === null || f.groups.has(edgeGroup(e.edgeType))))
    .sort((a, b) => {
      const g = GROUP_ORDER.indexOf(edgeGroup(a.edgeType)) - GROUP_ORDER.indexOf(edgeGroup(b.edgeType));
      if (g !== 0) return g;
      const s = STATUS_RANK[a.status] - STATUS_RANK[b.status];
      if (s !== 0) return s;
      return (b.validFrom ?? "").localeCompare(a.validFrom ?? "") || a.objectName.localeCompare(b.objectName);
    });
}

export function countByStatus<T extends { status: RowStatus }>(rows: readonly T[]): Record<RowStatus, number> {
  const out: Record<RowStatus, number> = { confirmed: 0, suggested: 0, disputed: 0, rejected: 0, unrecognised: 0 };
  for (const r of rows) out[r.status] += 1;
  return out;
}

export type TimeDomain = { from: number; to: number };

function ts(date: string | null): number | null {
  if (!date) return null;
  const t = Date.parse(date.length === 10 ? `${date}T00:00:00Z` : date);
  return Number.isNaN(t) ? null : t;
}

/**
 * The shared time axis of the validity bars: earliest dated start to `end` (the as-of day or
 * today). Returns null when no edge has any date, so no axis is drawn from nothing.
 */
export function validityDomain(edges: readonly NetworkEdge[], end: Date): TimeDomain | null {
  const points: number[] = [];
  for (const e of edges) {
    const a = ts(e.validFrom);
    const b = ts(e.validTo);
    if (a !== null) points.push(a);
    if (b !== null) points.push(b);
  }
  if (points.length === 0) return null;
  const to = Math.max(end.getTime(), ...points);
  const from = Math.min(...points);
  return { from, to: to > from ? to : from + 86_400_000 };
}

export type ValidityBar =
  | { kind: "dated"; left: number; width: number; openEnded: boolean; openStart: boolean }
  | { kind: "undated" };

/** Position of one edge's validity on the shared axis, as percentages. Undated edges get no bar. */
export function validityBar(edge: Pick<NetworkEdge, "validFrom" | "validTo">, domain: TimeDomain | null): ValidityBar {
  const a = ts(edge.validFrom);
  const b = ts(edge.validTo);
  if (!domain || (a === null && b === null)) return { kind: "undated" };
  const span = domain.to - domain.from;
  const start = a ?? domain.from;
  const end = b ?? domain.to;
  const left = Math.min(100, Math.max(0, ((start - domain.from) / span) * 100));
  const right = Math.min(100, Math.max(0, ((end - domain.from) / span) * 100));
  return { kind: "dated", left, width: Math.max(1.5, right - left), openEnded: b === null, openStart: a === null };
}

export function validityText(edge: Pick<NetworkEdge, "validFrom" | "validTo">): string {
  if (!edge.validFrom && !edge.validTo) return "no validity dates";
  return `${edge.validFrom ?? "start unknown"} to ${edge.validTo ?? "open"}`;
}

/* ------------------------------------------------------------ coverage helpers */

/** Confirmed edges out of all non-rejected edges: the denominator beside the graph. */
export function edgeCoverage(edges: readonly NetworkEdge[]): DataCoverage {
  const live = edges.filter((e) => e.status !== "rejected");
  return { covered: live.filter((e) => e.status === "confirmed").length, total: live.length, unit: "edges confirmed" };
}

/** Panel state for evidence-backed rows: no rows is a gap, never "verified". */
export function rowsState(rows: readonly { status: RowStatus }[]): DataState {
  const live = rows.filter((r) => r.status !== "rejected");
  if (live.length === 0) return "not_covered";
  if (live.some((r) => r.status === "disputed")) return "disputed";
  return live.every((r) => r.status === "confirmed") ? "verified" : "partial";
}

/** Provenance state of the passage review: every passage rejected or none stored is "not covered", never "verified". */
export function reviewState(disclosures: readonly { status: RowStatus }[]): DataState {
  const open = disclosures.filter((d) => d.status !== "rejected");
  if (open.length === 0) return "not_covered";
  return open.every((d) => d.status === "confirmed") ? "verified" : "partial";
}

/* ------------------------------------------------------------------ leadership */

export type LeadershipEntry = {
  key: string;
  name: string;
  role: string | null;
  status: RowStatus;
  from: string | null;
  to: string | null;
  /** True when `to` is unknown because the row has no end date at all (events), not "still serving". */
  endUnknown: boolean;
  source: string;
  sourceUrl: string | null;
  passage: string | null;
  origin: "edge" | "event";
};

function detailText(detail: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const k of keys) {
    const v = detail[k];
    if (typeof v === "string" && v.trim() !== "") return v;
  }
  return null;
}

/** Executive edges and leadership events, most recent start first; unconfirmed ones stay labelled. */
export function leadershipEntries(net: Pick<BusinessNetwork, "edges" | "events">): LeadershipEntry[] {
  const out: LeadershipEntry[] = [];
  for (const e of net.edges) {
    if (e.edgeType !== "executive" || e.status === "rejected") continue;
    out.push({
      key: `edge:${e.id}`,
      name: e.objectName,
      role: detailText(e.detail, ["title", "role", "position", "job_title"]),
      status: e.status,
      from: e.validFrom,
      to: e.validTo,
      endUnknown: false,
      source: e.source,
      sourceUrl: e.sourceUrl,
      passage: e.passage,
      origin: "edge",
    });
  }
  for (const ev of net.events) {
    if (ev.eventType !== "leadership" || ev.status === "rejected") continue;
    out.push({
      key: `event:${ev.id}`,
      name: detailText(ev.detail, ["person", "name", "executive"]) ?? ev.counterpartyKey ?? "Leadership change",
      role: detailText(ev.detail, ["title", "role", "position"]) ?? ev.eventState,
      status: ev.status,
      from: ev.effectiveAt ?? ev.announcedAt,
      to: null,
      endUnknown: true,
      source: ev.source,
      sourceUrl: ev.sourceUrl,
      passage: null,
      origin: "event",
    });
  }
  return out.sort((a, b) => (b.from ?? "").localeCompare(a.from ?? "") || a.name.localeCompare(b.name));
}

/* ------------------------------------------------------------------ peer ranking */

export type RankedPeer = PatentOverlapRow & { rank: number | null; ranked: boolean };

/**
 * Peers ranked by the share of the company's patent families that overlap the peer's
 * technology subclasses. A peer with no attributed patent families is listed last, unranked:
 * missing data is not "zero overlap". Ties share a rank.
 */
export function rankPeers(rows: readonly PatentOverlapRow[]): RankedPeer[] {
  const ranked = rows
    .filter((r) => r.overlapShare !== null && r.peerFamilies > 0)
    .sort((a, b) => (b.overlapShare ?? 0) - (a.overlapShare ?? 0) || (b.jaccard ?? 0) - (a.jaccard ?? 0) || a.peer.localeCompare(b.peer));
  const out: RankedPeer[] = [];
  ranked.forEach((r, i) => {
    const prev = out[i - 1];
    const tie = prev && prev.overlapShare === r.overlapShare && prev.jaccard === r.jaccard;
    out.push({ ...r, rank: tie && prev.rank !== null ? prev.rank : i + 1, ranked: true });
  });
  const rest = rows
    .filter((r) => !(r.overlapShare !== null && r.peerFamilies > 0))
    .sort((a, b) => a.peer.localeCompare(b.peer))
    .map((r) => ({ ...r, rank: null, ranked: false }));
  return [...out, ...rest];
}

/** One-sentence peer summary; the top-peer clause appears only when some peer is actually ranked. */
export function peerTakeaway(ranked: readonly RankedPeer[], total: number, fmtPct: (v: number, digits: number) => string): string {
  const rankedRows = ranked.filter((r) => r.ranked);
  const head = `${rankedRows.length} of ${total} confirmed peers can be ranked (patent families on both sides)`;
  const top = rankedRows[0];
  if (!top || top.overlapShare === null) return `${head}; no overlap share can be stated.`;
  return `${head}; the top peer shares ${fmtPct(top.overlapShare, 0)} of this company's families by CPC subclass.`;
}

/* --------------------------------------------------------------- patent matrix */

export type PatentColourMode = "citations" | "status";

export type MatrixCell = {
  subclass: string;
  year: number;
  /** Distinct families in the cell. */
  families: number;
  familyKeys: string[];
  /** Forward citations summed over families that report a count. */
  citations: number;
  /** Families in the cell with no citation count (unknown, not zero). */
  citationsUnknown: number;
  statusCounts: Record<string, number>;
  /** The single status when all families share one, else "mixed". */
  dominantStatus: string;
};

export type PatentMatrix = {
  subclasses: string[];
  years: number[];
  cells: Map<string, MatrixCell>;
  /** Distinct families attributed to the holding through a dated ownership path. */
  attributedFamilies: number;
  /** Of those, families placed in the matrix (priority date and a CPC subclass known). */
  placedFamilies: number;
  /** Listed by the API but with no dated ownership path: not counted for the holding. */
  unattributedFamilies: number;
  missingPriorityDate: number;
  missingCpc: number;
  maxFamilies: number;
  maxCitations: number;
  /** Families shown in rows beyond the row cap. */
  hiddenSubclasses: number;
  /** Calendar years older than the drawn axis (the axis is capped at 60 years from the newest). */
  olderYearsDropped: number;
};

export function cellKey(subclass: string, year: number): string {
  return `${subclass}|${year}`;
}

/** `H01L21/02` -> `H01L`. */
export function cpcSubclass(code: string): string | null {
  const c = code.replace(/\s+/g, "");
  return c.length >= 4 && /^[A-Za-z]/.test(c) ? c.slice(0, 4).toUpperCase() : null;
}

/** One row per family for the detail list: the attributed owner row wins, otherwise the first (same rule as the matrix). */
export function familyDetailMap(patents: readonly PatentFamily[]): Map<string, PatentFamily> {
  const out = new Map<string, PatentFamily>();
  for (const p of patents) {
    const have = out.get(p.familyKey);
    if (!have || (!have.attributed && p.attributed)) out.set(p.familyKey, p);
  }
  return out;
}

/**
 * Technology class x priority-year matrix from distinct, attributed patent families. A family
 * with several CPC subclasses appears in each of them, so cell counts must not be summed across
 * rows (the placed-family total counts each family once). Subclasses are ranked by distinct
 * families; `maxRows` caps the grid and reports what was left out.
 */
export function buildPatentMatrix(patents: readonly PatentFamily[], maxRows = 12): PatentMatrix {
  const all = [...familyDetailMap(patents).values()];
  const attributed = all.filter((p) => p.attributed);
  let missingPriority = 0;
  let missingCpc = 0;
  const placed = new Set<string>();
  const cells = new Map<string, MatrixCell>();
  const perSubclass = new Map<string, Set<string>>();
  for (const p of attributed) {
    const year = p.priorityDate ? Number(p.priorityDate.slice(0, 4)) : NaN;
    if (!Number.isInteger(year)) {
      missingPriority += 1;
      continue;
    }
    const subs = [...new Set(p.cpcCodes.map(cpcSubclass).filter((s): s is string => s !== null))];
    if (subs.length === 0) {
      missingCpc += 1;
      continue;
    }
    placed.add(p.familyKey);
    for (const s of subs) {
      const key = cellKey(s, year);
      const cell =
        cells.get(key) ??
        ({ subclass: s, year, families: 0, familyKeys: [], citations: 0, citationsUnknown: 0, statusCounts: {}, dominantStatus: "unknown" } as MatrixCell);
      cell.families += 1;
      cell.familyKeys.push(p.familyKey);
      if (p.citationsForward === null) cell.citationsUnknown += 1;
      else cell.citations += p.citationsForward;
      cell.statusCounts[p.legalStatus] = (cell.statusCounts[p.legalStatus] ?? 0) + 1;
      cells.set(key, cell);
      const set = perSubclass.get(s) ?? new Set<string>();
      set.add(p.familyKey);
      perSubclass.set(s, set);
    }
  }
  for (const cell of cells.values()) {
    const kinds = Object.keys(cell.statusCounts);
    cell.dominantStatus = kinds.length === 1 ? kinds[0]! : "mixed";
  }
  const ranked = [...perSubclass.entries()].sort((a, b) => b[1].size - a[1].size || a[0].localeCompare(b[0])).map(([s]) => s);
  const subclasses = ranked.slice(0, maxRows);
  const shown = new Set(subclasses);
  const years = [...new Set([...cells.values()].map((c) => c.year))];
  const yearList = years.length ? range(Math.min(...years), Math.max(...years)) : [];
  const visible = [...cells.values()].filter((c) => shown.has(c.subclass));
  return {
    subclasses,
    years: yearList,
    cells: new Map(visible.map((c) => [cellKey(c.subclass, c.year), c])),
    attributedFamilies: attributed.length,
    placedFamilies: placed.size,
    unattributedFamilies: all.length - attributed.length,
    missingPriorityDate: missingPriority,
    missingCpc,
    maxFamilies: Math.max(0, ...visible.map((c) => c.families)),
    maxCitations: Math.max(0, ...visible.map((c) => c.citations)),
    hiddenSubclasses: ranked.length - subclasses.length,
    olderYearsDropped: years.length ? Math.max(0, Math.max(...years) - Math.min(...years) + 1 - yearList.length) : 0,
  };
}

/** Years a..b, capped at 60 columns counted from the NEWEST year so the latest filings are never dropped. */
function range(a: number, b: number): number[] {
  const out: number[] = [];
  for (let y = b; y >= a && out.length < 60; y -= 1) out.push(y);
  return out.reverse();
}

/** 0 (no citations) .. 3, on a square-root scale so a few heavily cited cells do not flatten the rest. */
export function citationLevel(citations: number, max: number): 0 | 1 | 2 | 3 {
  if (!(citations > 0) || !(max > 0)) return 0;
  const r = Math.sqrt(citations / max);
  return r > 0.66 ? 3 : r > 0.33 ? 2 : 1;
}

/** Bubble diameter in px for a family count, area proportional to count. */
export function bubbleSize(families: number, max: number, maxPx = 30, minPx = 8): number {
  if (!(families > 0) || !(max > 0)) return 0;
  return Math.max(minPx, Math.round(maxPx * Math.sqrt(families / max)));
}

/* --------------------------------------------------------------- what changed */

export type ChangeItem = {
  key: string;
  kind: "edge" | "event" | "disclosure" | "patent";
  label: string;
  detail: string;
  status: RowStatus | null;
  knownAt: string;
  sourceUrl: string | null;
};

export function latestDate(dates: readonly (string | null | undefined)[]): string | null {
  const valid = dates.filter((d): d is string => typeof d === "string" && !Number.isNaN(Date.parse(d)));
  return valid.length ? valid.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b)) : null;
}

/**
 * Rows the app learned of after `since` (and up to `until` when given), newest first. The API
 * stores when a row became known, not when its status last changed, so a later decision on an
 * older row is not listed here. `since` must be a date; otherwise nothing is invented.
 */
export function whatChanged(net: BusinessNetwork, since: string | null, until?: string | null): ChangeItem[] {
  if (!since) return [];
  const from = Date.parse(since);
  if (Number.isNaN(from)) return [];
  const to = until ? Date.parse(until) : Number.POSITIVE_INFINITY;
  const inRange = (knownAt: string | null): knownAt is string => {
    if (!knownAt) return false;
    const t = Date.parse(knownAt);
    return !Number.isNaN(t) && t > from && t <= to;
  };
  const out: ChangeItem[] = [];
  for (const e of net.edges) {
    if (!inRange(e.knownAt)) continue;
    out.push({
      key: `edge:${e.id}`,
      kind: "edge",
      label: `${humanize(e.edgeType)}: ${e.objectName}`,
      detail: `${e.source}, ${validityText(e)}`,
      status: e.status,
      knownAt: e.knownAt,
      sourceUrl: e.sourceUrl,
    });
  }
  for (const ev of net.events) {
    if (!inRange(ev.knownAt)) continue;
    out.push({
      key: `event:${ev.id}`,
      kind: "event",
      label: `${humanize(ev.eventType)} ${ev.eventState}`,
      detail: ev.counterpartyKey ?? ev.source,
      status: ev.status,
      knownAt: ev.knownAt,
      sourceUrl: ev.sourceUrl,
    });
  }
  for (const d of net.disclosures) {
    if (!inRange(d.knownAt)) continue;
    out.push({
      key: `disclosure:${d.id}`,
      kind: "disclosure",
      label: `${humanize(d.relationship)} disclosure: ${d.anonymous ? "anonymous counterparty" : (d.counterpartyName ?? d.counterpartyKey ?? "unnamed")}`,
      detail: d.fiscalPeriod ? `fiscal period ${d.fiscalPeriod}` : "no fiscal period stated",
      status: d.status,
      knownAt: d.knownAt,
      sourceUrl: d.sourceUrl,
    });
  }
  const seen = new Set<string>();
  for (const p of net.patents) {
    if (seen.has(p.familyKey) || !inRange(p.knownAt)) continue;
    seen.add(p.familyKey);
    out.push({
      key: `patent:${p.familyKey}`,
      kind: "patent",
      label: `Patent family ${p.familyKey}`,
      detail: `${p.title ?? "untitled"} (${p.attributed ? "attributed" : "not attributed"}, ${p.legalStatus})`,
      status: null,
      knownAt: p.knownAt,
      sourceUrl: p.sourceUrl,
    });
  }
  return out.sort((a, b) => Date.parse(b.knownAt) - Date.parse(a.knownAt) || a.key.localeCompare(b.key));
}

/* ------------------------------------------------------------------------ deals */

export const DEAL_STAGES = [
  { id: "announcement", label: "Announcement", states: ["proposed", "announced"] },
  { id: "amendment", label: "Amendment", states: ["amended"] },
  { id: "approval", label: "Approval", states: ["approved"] },
  { id: "closing", label: "Closing", states: ["closed"] },
  { id: "termination", label: "Termination", states: ["terminated", "reversed"] },
] as const;

export type StageReach = "confirmed" | "suggested" | "none";
export type DealStage = { id: string; label: string; reach: StageReach; steps: DealStep[] };

/**
 * One deal on the five-stage track. A stage is "confirmed" only when a confirmed event with
 * that state exists; suggested-only stages stay visibly provisional; rejected or disputed
 * events never mark a stage.
 */
export function dealStages(deal: Pick<Deal, "timeline"> & { ignored?: Deal["ignored"] }): DealStage[] {
  // Events the backend state machine ignored (out of order) never mark a stage as reached.
  const ignored = new Set((deal.ignored ?? []).map((i) => i.eventId).filter((id): id is number => id !== null));
  return DEAL_STAGES.map((stage) => {
    const steps = deal.timeline.filter((s) => (stage.states as readonly string[]).includes(s.state) && !(s.eventId !== null && ignored.has(s.eventId)));
    const reach: StageReach = steps.some((s) => s.status === "confirmed") ? "confirmed" : steps.some((s) => s.status === "suggested") ? "suggested" : "none";
    return { id: stage.id, label: stage.label, reach, steps };
  });
}

/** Does a displayed state rest on unconfirmed events (so it must be flagged)? */
export function dealStateIsProvisional(deal: Pick<Deal, "state" | "confirmedState">): boolean {
  return deal.state !== null && deal.state !== deal.confirmedState;
}

const MONEY_KEYS = new Set(["price_per_share", "total_value", "cash_value", "stock_value", "debt_assumed", "debt_raised", "facility_size", "target_ebitda"]);
const PCT_KEYS = new Set(["cash_pct", "stock_pct", "ownership_pct", "interest_rate_pct"]);

export type TermRow = { key: string; label: string; value: string };

/** Confirmed deal terms as display rows, units explicit (percent keys are already 0-100). */
export function termRows(terms: Record<string, unknown>, fmtNumber: (n: number) => string): TermRow[] {
  const currency = typeof terms.currency === "string" ? terms.currency : null;
  const rows: TermRow[] = [];
  for (const [key, raw] of Object.entries(terms)) {
    if (key === "currency") continue;
    const n = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() !== "" && Number.isFinite(Number(raw)) ? Number(raw) : null;
    let value: string;
    if (n === null) value = typeof raw === "string" ? raw : JSON.stringify(raw);
    else if (PCT_KEYS.has(key)) value = `${fmtNumber(n)}%`;
    else if (MONEY_KEYS.has(key)) value = currency ? `${fmtNumber(n)} ${currency}` : `${fmtNumber(n)} (currency not stated)`;
    else value = fmtNumber(n);
    rows.push({ key, label: humanize(key), value });
  }
  return rows.sort((a, b) => a.key.localeCompare(b.key));
}

export function eventTimelineDate(ev: Pick<NetworkEvent, "effectiveAt" | "announcedAt">): string | null {
  return ev.effectiveAt ?? ev.announcedAt;
}

/* ----------------------------------------------------------------- concentration */

/**
 * What one side (named or anonymous) of a concentration row can honestly say:
 * `none` no counterparty on this side, `unknown` counterparties exist but no share was reported,
 * `exact` every share on this side was reported, `floor` at least one share is missing so the sum is a lower bound.
 */
export type SideShare = { kind: "none" | "unknown" | "exact" | "floor"; value: number | null };

export type ConcentrationRow = ConcentrationPeriod & {
  /** At least one side's sum is a floor (some disclosed counterparty reported no share). */
  isFloor: boolean;
  /** Some counterparty reported a share. */
  hasShare: boolean;
  named: SideShare;
  anonymous: SideShare;
};

function sideShare(count: number, sum: number | null, missing: number, otherCount: number): SideShare {
  if (count <= 0) return { kind: "none", value: null };
  // The API sends one missing count for both sides: with counterparties on the other side too, we
  // cannot tell which side is missing, so any missing share makes both sides lower bounds.
  const missingHere = otherCount <= 0 ? missing : missing > 0 ? 1 : 0;
  const allMissing = otherCount <= 0 && missing >= count;
  if (sum === null || allMissing || (sum === 0 && missing > 0)) return { kind: "unknown", value: null };
  return { kind: missingHere > 0 ? "floor" : "exact", value: sum };
}

export function sideShareLabel(side: SideShare, fmt: (v: number) => string): string {
  if (side.kind === "none") return "none disclosed";
  if (side.kind === "unknown" || side.value === null) return "not reported";
  return `${side.kind === "floor" ? "at least " : ""}${fmt(side.value)}`;
}

/** Periods newest first; a sum over rows with missing shares is flagged as a floor, never a total. */
export function concentrationRows(periods: readonly ConcentrationPeriod[]): ConcentrationRow[] {
  return [...periods]
    .sort((a, b) => b.fiscalPeriod.localeCompare(a.fiscalPeriod) || a.relationship.localeCompare(b.relationship))
    .map((p) => {
      const named = sideShare(p.namedCount, p.namedShareSum, p.missingShareCount, p.anonymousCount);
      const anonymous = sideShare(p.anonymousCount, p.anonymousShareSum, p.missingShareCount, p.namedCount);
      return {
        ...p,
        named,
        anonymous,
        isFloor: named.kind === "floor" || anonymous.kind === "floor",
        hasShare: p.largestShare !== null || named.value !== null || anonymous.value !== null,
      };
    });
}

/** The largest single reported share over the periods that report one; null when none does. */
export function largestReportedShare(rows: readonly Pick<ConcentrationPeriod, "largestShare">[]): number | null {
  const v = rows.map((r) => r.largestShare).filter((x): x is number => x !== null);
  return v.length ? Math.max(...v) : null;
}

export function shareBasisLabel(basis: string): string {
  if (basis === "issuer_revenue") return "of the company's revenue";
  if (basis === "issuer_purchases") return "of the company's purchases";
  return `basis: ${basis}`;
}
