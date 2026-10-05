/**
 * View models for the per-holding data charts (insider activity, surprise, concentration,
 * stakes, price diagnostics, patent overlap, ownership history). Most of these responses are
 * untyped or loosely typed, so every field is read defensively: anything missing becomes null
 * (drawn as a gap or "Not published"), never zero.
 */

type Rec = Record<string, unknown>;
export const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

// ---------------------------------------------------------------- insider activity

export type InsiderMonth = { month: string; net: number | null; buys: number; sells: number };

function nextMonth(m: string): string {
  const [y, mo] = m.split("-").map(Number);
  return mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, "0")}`;
}

/**
 * Net discretionary shares per calendar month (open-market buys minus sells), from the rows the
 * backend itself counts toward net discretionary activity. Months with no counted row between
 * the first and last are null (a gap), not zero.
 */
export function insiderMonthly(payload: unknown): { months: InsiderMonth[]; countedRows: number; excludedRows: number } {
  const rows = isRec(payload) ? arr(payload.transactions).filter(isRec) : [];
  const byMonth = new Map<string, InsiderMonth>();
  let counted = 0;
  let excluded = 0;
  for (const r of rows) {
    if (r.counts_toward_net_discretionary !== true) {
      excluded += 1;
      continue;
    }
    const shares = num(r.shares);
    const date = text(r.transaction_date) ?? text(r.filed);
    if (shares === null || !date || !/^\d{4}-\d{2}/.test(date)) {
      excluded += 1;
      continue;
    }
    counted += 1;
    const month = date.slice(0, 7);
    const m = byMonth.get(month) ?? { month, net: 0, buys: 0, sells: 0 };
    if (r.economic_class === "open_market_buy") {
      m.net = (m.net ?? 0) + shares;
      m.buys += 1;
    } else {
      m.net = (m.net ?? 0) - shares;
      m.sells += 1;
    }
    byMonth.set(month, m);
  }
  const keys = [...byMonth.keys()].sort();
  const months: InsiderMonth[] = [];
  if (keys.length > 0) {
    for (let m = keys[0]; m <= keys[keys.length - 1]; m = nextMonth(m)) {
      months.push(byMonth.get(m) ?? { month: m, net: null, buys: 0, sells: 0 });
    }
  }
  return { months, countedRows: counted, excludedRows: excluded };
}

// ---------------------------------------------------------------- surprise

export type Surprise = {
  metric: string;
  period: string;
  /** Surprise as a fraction of |consensus|; null when there is no comparable consensus. */
  fraction: number | null;
  actual: number | null;
  consensus: number | null;
  /** Why there is no surprise, in words. */
  reason: string | null;
};

const SURPRISE_REASON: Record<string, string> = {
  no_actual: "No reported figure stored",
  no_consensus_before_report: "No consensus known before the report",
  currency_mismatch: "Consensus and actual are in different currencies",
  unsupported_metric: "Metric has no reported counterpart",
};

export function parseSurprise(metric: string, period: string, payload: unknown): Surprise {
  const base = { metric, period, fraction: null, actual: null, consensus: null };
  if (!isRec(payload)) return { ...base, reason: "Not available" };
  const state = text(payload.state);
  const actual = isRec(payload.actual) ? num(payload.actual.value) : null;
  const consensus = isRec(payload.consensus) ? num(payload.consensus.value) : null;
  if (state !== "available") {
    return { ...base, actual, consensus, reason: SURPRISE_REASON[state ?? ""] ?? text(payload.reason) ?? "Not available" };
  }
  const fraction = num(payload.surprise_percent);
  return {
    ...base,
    actual,
    consensus,
    fraction,
    reason: fraction === null ? "Consensus is zero, so a percentage is not defined" : null,
  };
}

// ---------------------------------------------------------------- customer / supplier concentration

export type ConcentrationSegment = { label: string; value: number };
export type ConcentrationSide = {
  side: "customer" | "supplier";
  period: string;
  denominator: string;
  segments: ConcentrationSegment[];
  reported: number;
  /** Counterparties that disclosed no share at all. */
  missingShare: number;
  exceedsTotal: boolean;
  contradictions: number;
};

const DENOMINATOR: Record<string, string> = {
  issuer_revenue: "the issuer's revenue",
  issuer_purchases: "the issuer's purchases",
};

/** The latest period of each side, split into named, unnamed and an explicit undisclosed rest. */
export function concentrationSides(payload: unknown): ConcentrationSide[] {
  const periods = isRec(payload) ? arr(payload.periods).filter(isRec) : [];
  const out: ConcentrationSide[] = [];
  for (const side of ["customer", "supplier"] as const) {
    const rows = periods.filter((p) => p.relationship === side && text(p.fiscal_period));
    rows.sort((a, b) => (text(b.fiscal_period) ?? "").localeCompare(text(a.fiscal_period) ?? ""));
    const row = rows[0];
    if (!row) continue;
    const named = num(row.named_share_sum) ?? 0;
    const anon = num(row.anonymous_share_sum) ?? 0;
    const reported = num(row.reported_share_sum) ?? named + anon;
    const segments: ConcentrationSegment[] = [];
    if (named > 0) segments.push({ label: "Named", value: named });
    if (anon > 0) segments.push({ label: "Unnamed (anonymous)", value: anon });
    if (reported < 1) segments.push({ label: "Undisclosed", value: 1 - reported });
    out.push({
      side,
      period: text(row.fiscal_period) ?? "",
      denominator: DENOMINATOR[text(row.share_basis) ?? ""] ?? "the issuer's total",
      segments,
      reported,
      missingShare: num(row.missing_share_count) ?? 0,
      exceedsTotal: row.share_exceeds_total === true || reported > 1,
      contradictions: num(row.open_contradictions) ?? 0,
    });
  }
  return out;
}

// ---------------------------------------------------------------- stakes

export type StakeRow = {
  key: string;
  label: string;
  /** Percent points (0..100), null when the filing gives none. */
  percent: number | null;
  asOf: string | null;
  detail: string;
  url: string | null;
};

/** 13D/G stakes: filer-reported percent of the share class, largest first, unknowns last. */
export function controlStakeRows(payload: unknown): StakeRow[] {
  const stakes = isRec(payload) ? arr(payload.stakes).filter(isRec) : [];
  const rows = stakes.map((s, i): StakeRow => {
    const holders = arr(s.holders).filter(isRec);
    const name = text(holders[0]?.person_name) ?? text(s.issuer_name) ?? "Unnamed filer";
    return {
      key: `${text(s.accession) ?? i}`,
      label: name,
      percent: num(s.stake_percent),
      asOf: text(s.event_date) ?? text(s.filed),
      detail: `Schedule ${text(s.schedule) ?? "13D/G"}${s.is_amendment === true ? " amendment" : ""}`,
      url: text(s.source_url),
    };
  });
  return sortStakes(rows);
}

/** N-PORT positions: percent of each fund's own net assets as filed (the denominators differ). */
export function fundPositionRows(payload: unknown): StakeRow[] {
  const positions = isRec(payload) ? arr(payload.positions).filter(isRec) : arr(payload).filter(isRec);
  const rows = positions.map((p, i): StakeRow => ({
    key: `${text(p.accession) ?? ""}:${text(p.series_id) ?? i}:${i}`,
    label: text(p.fund_name) ?? text(p.registrant_name) ?? "Unnamed fund",
    percent: num(p.pct_of_fund),
    asOf: text(p.report_period_end),
    detail: p.stale === true ? "Stale report" : "Latest report",
    url: text(p.source_url),
  }));
  return sortStakes(rows);
}

function sortStakes(rows: StakeRow[]): StakeRow[] {
  return [...rows].sort((a, b) => (b.percent ?? -Infinity) - (a.percent ?? -Infinity));
}

// ---------------------------------------------------------------- price diagnostics

export type PriceSeries = {
  key: string;
  label: string;
  chosen: boolean;
  expected: number;
  observed: number;
  /** Missing days against the exchange calendar; null when no calendar judged it. */
  missing: number | null;
  stale: number | null;
  lastSession: string | null;
  basis: string | null;
  access: string | null;
};

export function parseDiagnostics(payload: unknown): PriceSeries[] {
  return arr(payload)
    .filter(isRec)
    .map((r, i) => {
      const expected = num(r.expected_sessions) ?? 0;
      return {
        key: `${text(r.symbol) ?? i}:${text(r.source) ?? ""}:${text(r.bar_type) ?? ""}`,
        label: `${text(r.symbol) ?? "Unknown"} · ${text(r.source) ?? "unknown source"} · ${text(r.bar_type) ?? "bars"}`,
        chosen: r.chosen === true,
        expected,
        observed: num(r.observed_sessions) ?? 0,
        missing: expected > 0 ? num(r.missing_days) : null,
        stale: num(r.stale_sessions),
        lastSession: text(r.last_session),
        basis: text(r.calendar_basis),
        access: text(r.access_state),
      };
    })
    .sort((a, b) => Number(b.chosen) - Number(a.chosen));
}

// ---------------------------------------------------------------- patent overlap

export type OverlapRow = { peer: string; share: number | null; peerFamilies: number; ownFamilies: number; reason: string | null };

/** Share of the holding's patent families that overlap each peer's technology classes. */
export function overlapRows(payload: unknown): OverlapRow[] {
  const peers = isRec(payload) ? arr(payload.peers).filter(isRec) : [];
  return peers
    .map((p, i): OverlapRow => {
      const share = num(p.overlap_share);
      const peerFamilies = num(p.peer_families) ?? 0;
      return {
        peer: text(p.peer) ?? `Peer ${i + 1}`,
        share,
        peerFamilies,
        ownFamilies: num(p.own_families) ?? 0,
        reason: share === null ? (peerFamilies === 0 ? "Peer has no attributed patent families" : "No families of its own to compare") : null,
      };
    })
    .sort((a, b) => (b.share ?? -Infinity) - (a.share ?? -Infinity));
}

// ---------------------------------------------------------------- ownership history

export type OwnershipMetric = "holders" | "shares" | "pct";
export type OwnershipPoint = { period: string; value: number | null };

/** Oldest-first series of one 13F metric; the API lists newest first. */
export function ownershipSeries(periods: readonly unknown[], metric: OwnershipMetric): OwnershipPoint[] {
  const key = metric === "holders" ? "holders" : metric === "shares" ? "shares" : "pct_of_shares_outstanding";
  return periods
    .filter(isRec)
    .map((p) => ({ period: text(p.period) ?? "", value: num(p[key]) }))
    .filter((p) => p.period)
    .sort((a, b) => a.period.localeCompare(b.period));
}
